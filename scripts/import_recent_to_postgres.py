#!/usr/bin/env python3
"""
Import the most recent forecast Parquet snapshot into Neon Postgres.

Run by the daily-refresh GitHub Action right after ``scripts/daily_score.py``
writes ``data/forecasts/forecasts_<YYYY-MM-DD>.parquet``. The FastAPI
backend reads the stored feature vectors for live re-inference.

Usage:
    DATABASE_URL=postgres://... python scripts/import_recent_to_postgres.py
    # Nightly CI imports the whole file (--full). For manual runs, --days
    # filters on chain snapshot_date; use --days 7+ or --full so you don't
    # drop rows whose snapshot is more than 1d old:
    python scripts/import_recent_to_postgres.py --full
    # Force re-import everything in the newest Parquet (ignore --days):
    python scripts/import_recent_to_postgres.py --full
    # Pick a specific Parquet file (defaults to newest forecasts_*.parquet):
    python scripts/import_recent_to_postgres.py --file data/forecasts/forecasts_2026-05-22.parquet

Idempotent: rows are upserted on
``(act_symbol, earnings_date, snapshot_date, model_horizon)``. Re-running
the same day's snapshot is a no-op.

Schema notes:
- Column names mirror the Parquet (`act_symbol`, `earnings_date`, ...).
- `POST /api/ml/predict` reads the saved `feature_vector`, substitutes the
  latest stock price, and scores it with the active signed model bundle.
- Retrain/rollback CI passes `--expected-model-bundle-id` and
  `--require-database` so serving/import mismatches or missing Neon authority
  fail before any rows are written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print(
        "psycopg2 not installed. Run `pip install psycopg2-binary`.",
        file=sys.stderr,
    )
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent
FORECASTS_DIR = REPO_ROOT / "data" / "forecasts"

# Order matters — also drives the INSERT column list below.
COLUMNS = [
    "act_symbol",
    "earnings_date",
    "timing",
    "snapshot_date",
    "model_horizon",
    "model_bundle_id",
    "spot_price",
    "atm_iv",
    "em_math_pct",
    "em_event_vol_pct",
    "em_ml_pct",
    "em_ml_abs",
    "correction_factor",
    "p10",
    "p25",
    "p50",
    "p75",
    "p90",
    "iv_crush_pct",
    "event_vol_fraction",
    "hist_move_avg_4q",
    "hist_straddle_accuracy",
    "iv_rv_ratio_20d",
    "parkinson_rv_20d",
    "vol_of_vol_20d",
    "scored_at",
    "feature_vector",
]

PRIMARY_KEY_COLUMNS = [
    "act_symbol",
    "earnings_date",
    "snapshot_date",
    "model_horizon",
]

# JSONB columns get a psycopg2 adapter wrapper at insert time. Listed
# separately because the value is a JSON string in the Parquet and we
# need to pass it through psycopg2.extras.Json so PG doesn't try to
# cast a Python string to JSONB literally.
JSONB_COLUMNS = {"feature_vector"}


@dataclass
class ImportStats:
    source_rows: int
    selected_rows: int
    duplicate_rows: int
    duplicate_keys: int

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS em_forecasts (
    act_symbol             TEXT NOT NULL,
    earnings_date          DATE NOT NULL,
    timing                 TEXT,
    snapshot_date          DATE NOT NULL,
    model_horizon          INTEGER NOT NULL,
    model_bundle_id        TEXT,
    spot_price             DOUBLE PRECISION,
    atm_iv                 DOUBLE PRECISION,
    em_math_pct            DOUBLE PRECISION,
    em_event_vol_pct       DOUBLE PRECISION,
    em_ml_pct              DOUBLE PRECISION,
    em_ml_abs              DOUBLE PRECISION,
    correction_factor      DOUBLE PRECISION,
    p10                    DOUBLE PRECISION,
    p25                    DOUBLE PRECISION,
    p50                    DOUBLE PRECISION,
    p75                    DOUBLE PRECISION,
    p90                    DOUBLE PRECISION,
    iv_crush_pct           DOUBLE PRECISION,
    event_vol_fraction     DOUBLE PRECISION,
    hist_move_avg_4q       DOUBLE PRECISION,
    hist_straddle_accuracy DOUBLE PRECISION,
    iv_rv_ratio_20d        DOUBLE PRECISION,
    parkinson_rv_20d       DOUBLE PRECISION,
    vol_of_vol_20d         DOUBLE PRECISION,
    scored_at              TIMESTAMPTZ,
    feature_vector         JSONB,
    PRIMARY KEY (act_symbol, earnings_date, snapshot_date, model_horizon)
);
-- feature_vector may be missing on rows written by an older daily_score
-- run; the API treats NULL as "re-inference unavailable for this snapshot".
ALTER TABLE em_forecasts ADD COLUMN IF NOT EXISTS feature_vector JSONB;
ALTER TABLE em_forecasts ADD COLUMN IF NOT EXISTS model_bundle_id TEXT;
CREATE INDEX IF NOT EXISTS em_forecasts_symbol_date_idx
    ON em_forecasts (act_symbol, earnings_date);
CREATE INDEX IF NOT EXISTS em_forecasts_snapshot_idx
    ON em_forecasts (snapshot_date DESC);
CREATE INDEX IF NOT EXISTS em_forecasts_bundle_idx
    ON em_forecasts (model_bundle_id);

CREATE TABLE IF NOT EXISTS em_forecast_imports (
    id                    BIGSERIAL PRIMARY KEY,
    parquet_file          TEXT NOT NULL,
    imported_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    import_mode           TEXT NOT NULL,
    source_rows           INTEGER NOT NULL,
    selected_rows         INTEGER NOT NULL,
    duplicate_rows        INTEGER NOT NULL DEFAULT 0,
    duplicate_keys        INTEGER NOT NULL DEFAULT 0,
    rows_upserted         INTEGER NOT NULL,
    feature_vector_rows   INTEGER NOT NULL,
    distinct_symbols      INTEGER NOT NULL,
    distinct_events       INTEGER NOT NULL,
    min_snapshot_date     DATE,
    max_snapshot_date     DATE,
    model_bundle_id       TEXT,
    horizons              JSONB NOT NULL DEFAULT '{}'::jsonb
);
ALTER TABLE em_forecast_imports ADD COLUMN IF NOT EXISTS model_bundle_id TEXT;
CREATE INDEX IF NOT EXISTS em_forecast_imports_imported_at_idx
    ON em_forecast_imports (imported_at DESC);
"""


def pick_parquet(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"--file {p} does not exist")
        return p
    candidates = sorted(FORECASTS_DIR.glob("forecasts_*.parquet"))
    if not candidates:
        raise SystemExit(
            f"No forecasts_*.parquet found in {FORECASTS_DIR}. "
            "Run scripts/daily_score.py first."
        )
    return candidates[-1]


def load_and_filter(parquet_path: Path, days: int, full: bool) -> tuple[pd.DataFrame, ImportStats]:
    df = pd.read_parquet(parquet_path)
    source_rows = len(df)
    # Older daily_score runs predate these serving/audit fields. Backfill only
    # those explicitly compatible columns; all core forecast columns remain
    # fail-closed below.
    for backward_compatible_column in ("feature_vector", "model_bundle_id"):
        if backward_compatible_column not in df.columns:
            df[backward_compatible_column] = None
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise SystemExit(
            f"Parquet {parquet_path.name} is missing expected columns: {missing}"
        )
    df = df[COLUMNS].copy()
    # Normalize dtypes to what psycopg2 will happily accept.
    df["earnings_date"] = pd.to_datetime(df["earnings_date"]).dt.date
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
    df["scored_at"] = pd.to_datetime(df["scored_at"], errors="coerce", utc=True)
    df["model_horizon"] = df["model_horizon"].astype("Int64")

    if not full:
        cutoff = date.today() - timedelta(days=days)
        df = df[df["snapshot_date"] >= cutoff]

    selected_rows = len(df)
    duplicate_rows = 0
    duplicate_keys = 0
    duplicate_mask = df.duplicated(PRIMARY_KEY_COLUMNS, keep=False)
    if duplicate_mask.any():
        duplicate_rows = int(duplicate_mask.sum())
        duplicate_keys = int(
            df.loc[duplicate_mask, PRIMARY_KEY_COLUMNS].drop_duplicates().shape[0]
        )
        print(
            "import_recent_to_postgres: warning: "
            f"{duplicate_rows} rows share {duplicate_keys} primary keys; "
            "deduping before upsert",
            file=sys.stderr,
        )
        df = df.drop_duplicates(PRIMARY_KEY_COLUMNS, keep="first")

    # Replace pandas NaN/NaT with Python None for psycopg2's adapter.
    out = df.astype(object).where(df.notna(), None)
    # A serving snapshot must be attributable to at most one promoted bundle.
    # Reject mixed-model files before opening a production database connection.
    _single_model_bundle_id(out)
    return out, ImportStats(
        source_rows=source_rows,
        selected_rows=selected_rows,
        duplicate_rows=duplicate_rows,
        duplicate_keys=duplicate_keys,
    )


def _sanitize_json_nan(value):
    """Recursively replace non-finite floats (NaN, +/-Inf) with None.

    Postgres JSONB doesn't accept the JSON5-style `NaN` / `Infinity` tokens
    that Python's json module emits by default, so any feature that landed
    as a float NaN in the source Parquet would otherwise crash the upsert
    with `invalid input syntax for type json`. We strip them out before
    handing the dict to psycopg2.extras.Json.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _sanitize_json_nan(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_nan(v) for v in value]
    return value


def upsert(conn, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ", ".join(COLUMNS)
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    update_cols = [c for c in COLUMNS if c not in PRIMARY_KEY_COLUMNS]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)

    sql = (
        f"INSERT INTO em_forecasts ({cols}) VALUES ({placeholders}) "
        "ON CONFLICT (act_symbol, earnings_date, snapshot_date, model_horizon) "
        f"DO UPDATE SET {set_clause}"
    )

    # Wrap JSONB column values in psycopg2's Json adapter so the JSON
    # string is sent as a JSONB literal rather than a quoted text blob.
    json_idx = [i for i, c in enumerate(COLUMNS) if c in JSONB_COLUMNS]
    raw_rows = list(df.itertuples(index=False, name=None))
    rows = []
    for row in raw_rows:
        if not json_idx:
            rows.append(row)
            continue
        row_list = list(row)
        for i in json_idx:
            value = row_list[i]
            if value is None:
                continue
            # daily_score writes a JSON string; Json() wants a Python object,
            # so parse first. Empty/invalid → NULL rather than crashing the
            # batch on one bad row. Strip NaN/Infinity floats post-parse —
            # Postgres JSONB rejects them and older parquets (pre-Phase 1b)
            # baked them in via Python json's default allow_nan=True.
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except (ValueError, TypeError):
                    parsed = None
            else:
                parsed = value
            if parsed is not None:
                parsed = _sanitize_json_nan(parsed)
            row_list[i] = psycopg2.extras.Json(parsed) if parsed is not None else None
        rows.append(tuple(row_list))
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
    return len(rows)


def _horizon_counts(df: pd.DataFrame) -> dict[str, int]:
    if df.empty or "model_horizon" not in df.columns:
        return {}
    counts = df["model_horizon"].value_counts(dropna=True).sort_index()
    return {str(int(horizon)): int(count) for horizon, count in counts.items()}


def _feature_vector_count(df: pd.DataFrame) -> int:
    if df.empty or "feature_vector" not in df.columns:
        return 0
    return int(df["feature_vector"].notna().sum())


def _single_model_bundle_id(df: pd.DataFrame) -> str | None:
    """Return the imported bundle ID and reject mixed-model snapshots."""
    if df.empty or "model_bundle_id" not in df.columns:
        return None
    bundle_ids = sorted({
        str(value).strip()
        for value in df["model_bundle_id"].dropna()
        if str(value).strip()
    })
    if len(bundle_ids) > 1:
        raise ValueError(
            "Forecast import contains multiple model bundle IDs: "
            + ", ".join(bundle_ids)
        )
    return bundle_ids[0] if bundle_ids else None


def verify_expected_model_bundle(df: pd.DataFrame, expected_bundle_id: str) -> None:
    observed = _single_model_bundle_id(df)
    if observed != expected_bundle_id:
        raise ValueError(
            "Forecast import bundle does not match the activated serving bundle: "
            f"expected {expected_bundle_id}, observed {observed or 'none'}"
        )


def verify_activation_receipt(path: Path, expected_bundle_id: str) -> dict:
    """Require serving activation of the same exact bundle before import."""
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("serving activation receipt must be a JSON object")
    if payload.get("schema") != "quantiv.serving-activation.v1":
        raise ValueError("unsupported serving activation receipt schema")
    if payload.get("status") != "passed":
        raise ValueError("serving activation receipt did not pass")
    if (
        payload.get("expected_bundle_id") != expected_bundle_id
        or payload.get("activated_bundle_id") != expected_bundle_id
    ):
        raise ValueError("serving activation receipt names a different bundle")
    receipt_id = str(payload.get("receipt_id") or "")
    if not receipt_id.startswith("sha256:") or len(receipt_id) != 71:
        raise ValueError("serving activation receipt ID is invalid")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_import_receipt(
    path: Path,
    *,
    parquet_path: Path,
    bundle_id: str,
    activation_receipt: dict,
    stats: ImportStats,
    frame: pd.DataFrame,
    rows_upserted: int,
) -> dict:
    """Record that the activated bundle's forecasts committed to Postgres."""
    core = {
        "schema": "quantiv.forecast-import.v1",
        "status": "passed",
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "model_bundle_id": bundle_id,
        "activation_receipt_id": activation_receipt["receipt_id"],
        "parquet_file": parquet_path.name,
        "parquet_sha256": _sha256_file(parquet_path),
        "source_rows": stats.source_rows,
        "selected_rows": stats.selected_rows,
        "rows_upserted": rows_upserted,
        "feature_vector_rows": _feature_vector_count(frame),
        "horizons": _horizon_counts(frame),
    }
    receipt = {
        **core,
        "receipt_id": "sha256:"
        + hashlib.sha256(
            json.dumps(core, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    return receipt


def record_import(
    conn,
    parquet_path: Path,
    import_mode: str,
    stats: ImportStats,
    df: pd.DataFrame,
    rows_upserted: int,
) -> None:
    """Persist import metadata so production status can explain coverage.

    This is intentionally separate from the serving table. If a workflow
    generated 424 rows but the live backend reports fewer feature rows,
    `/api/ml/status` can now show which Parquet file this database last
    ingested and how many rows survived filtering/deduplication.
    """
    if df.empty:
        min_snapshot = None
        max_snapshot = None
        distinct_symbols = 0
        distinct_events = 0
    else:
        min_snapshot = df["snapshot_date"].min()
        max_snapshot = df["snapshot_date"].max()
        distinct_symbols = int(df["act_symbol"].nunique(dropna=True))
        distinct_events = int(
            df[["act_symbol", "earnings_date"]]
            .dropna()
            .drop_duplicates()
            .shape[0]
        )

    sql = """
        INSERT INTO em_forecast_imports (
            parquet_file,
            import_mode,
            source_rows,
            selected_rows,
            duplicate_rows,
            duplicate_keys,
            rows_upserted,
            feature_vector_rows,
            distinct_symbols,
            distinct_events,
            min_snapshot_date,
            max_snapshot_date,
            model_bundle_id,
            horizons
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                parquet_path.name,
                import_mode,
                stats.source_rows,
                stats.selected_rows,
                stats.duplicate_rows,
                stats.duplicate_keys,
                rows_upserted,
                _feature_vector_count(df),
                distinct_symbols,
                distinct_events,
                min_snapshot,
                max_snapshot,
                _single_model_bundle_id(df),
                psycopg2.extras.Json(_horizon_counts(df)),
            ),
        )


def connect_with_retry(url: str, attempts: int = 4):
    """Connect to Postgres with a per-attempt timeout and backoff.

    Neon free-tier computes auto-suspend and can be slow/flaky to wake, and the
    GitHub runner occasionally hits transient network blips reaching AWS. A bare
    connect with no timeout hung ~7 min across every resolved IP and then failed
    the whole daily refresh; this fails fast per attempt and retries instead.
    """
    delays = [3, 8, 20]
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return psycopg2.connect(url, connect_timeout=15)
        except psycopg2.OperationalError as exc:
            last_exc = exc
            if i < attempts - 1:
                wait = delays[min(i, len(delays) - 1)]
                first_line = str(exc).splitlines()[0][:140]
                print(
                    f"  connect attempt {i + 1}/{attempts} failed ({first_line}); "
                    f"retrying in {wait}s",
                    flush=True,
                )
                time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7,
                    help="When not using --full: keep rows with snapshot_date >= today-N "
                         "(chain date, not Parquet write date). Nightly CI uses --full.")
    ap.add_argument("--full", action="store_true",
                    help="Ignore --days; import every row in the Parquet")
    ap.add_argument("--file", default=None,
                    help="Specific Parquet file (default: newest forecasts_*.parquet)")
    ap.add_argument(
        "--expected-model-bundle-id",
        default=None,
        help="Fail unless the selected forecast contains exactly this promoted bundle ID.",
    )
    ap.add_argument(
        "--require-database",
        action="store_true",
        help="Fail when DATABASE_URL is missing instead of treating the import as optional.",
    )
    ap.add_argument(
        "--activation-receipt",
        type=Path,
        default=None,
        help="Require a passing exact-bundle serving activation receipt before import.",
    )
    ap.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help="Write a content-addressed receipt after the database transaction commits.",
    )
    args = ap.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        # The daily-refresh workflow runs this step unconditionally so a
        # missing DATABASE_URL (forks / preview branches without Neon
        # access) is a no-op rather than a workflow failure.
        if args.require_database:
            print("DATABASE_URL not set — required forecast import cannot run.", file=sys.stderr)
            return 2
        print("DATABASE_URL not set — skipping em_forecasts import.")
        return 0

    parquet_path = pick_parquet(args.file)
    df, stats = load_and_filter(parquet_path, args.days, args.full)
    if args.expected_model_bundle_id:
        verify_expected_model_bundle(df, args.expected_model_bundle_id)
    activation_receipt = None
    if args.activation_receipt:
        if not args.expected_model_bundle_id:
            raise ValueError("--activation-receipt requires --expected-model-bundle-id")
        activation_receipt = verify_activation_receipt(
            args.activation_receipt,
            args.expected_model_bundle_id,
        )
    if args.receipt and activation_receipt is None:
        raise ValueError("--receipt requires a verified --activation-receipt")
    import_mode = "full" if args.full else f"days:{args.days}"
    print(f"import_recent_to_postgres: {parquet_path.name} → {len(df)} rows "
          f"({import_mode})")

    conn = connect_with_retry(url)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
        with conn:
            count = upsert(conn, df)
        with conn:
            record_import(conn, parquet_path, import_mode, stats, df, count)
        print(f"import_recent_to_postgres: upserted {count} rows")
        if args.receipt and activation_receipt is not None:
            receipt = write_import_receipt(
                args.receipt,
                parquet_path=parquet_path,
                bundle_id=args.expected_model_bundle_id,
                activation_receipt=activation_receipt,
                stats=stats,
                frame=df,
                rows_upserted=count,
            )
            print(f"import receipt: {receipt['receipt_id']} → {args.receipt}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
