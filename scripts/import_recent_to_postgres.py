#!/usr/bin/env python3
"""
Import the most recent forecast Parquet snapshot into Neon Postgres.

Run by the daily-refresh GitHub Action right after ``scripts/daily_score.py``
writes ``data/forecasts/forecasts_<YYYY-MM-DD>.parquet``. The FastAPI
backend (Path B) queries this table for any endpoint that filters across
the full forecast universe rather than scoring on demand.

Usage:
    DATABASE_URL=postgres://... python scripts/import_recent_to_postgres.py
    # Only the last N days of snapshot rows, default 1:
    python scripts/import_recent_to_postgres.py --days 1
    # Force re-import everything in the newest Parquet (ignore --days):
    python scripts/import_recent_to_postgres.py --full
    # Pick a specific Parquet file (defaults to newest forecasts_*.parquet):
    python scripts/import_recent_to_postgres.py --file data/forecasts/forecasts_2026-05-22.parquet

Idempotent: rows are upserted on
``(act_symbol, earnings_date, snapshot_date, model_horizon)``. Re-running
the same day's snapshot is a no-op.

Schema notes:
- Column names mirror the Parquet (act_symbol, earnings_date, ...) rather
  than the legacy FastAPI Postgres column set (underlying, quote_ts,
  band68_low, ...). The legacy ``/em/*`` routes in apps/backend/routers/em.py
  reference the old column names and will need updating before they can
  query against this table — Path B's ``/api/ml/predict`` route doesn't
  touch this table at all.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
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
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS em_forecasts (
    act_symbol             TEXT NOT NULL,
    earnings_date          DATE NOT NULL,
    timing                 TEXT,
    snapshot_date          DATE NOT NULL,
    model_horizon          INTEGER NOT NULL,
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
    PRIMARY KEY (act_symbol, earnings_date, snapshot_date, model_horizon)
);
CREATE INDEX IF NOT EXISTS em_forecasts_symbol_date_idx
    ON em_forecasts (act_symbol, earnings_date);
CREATE INDEX IF NOT EXISTS em_forecasts_snapshot_idx
    ON em_forecasts (snapshot_date DESC);
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


def load_and_filter(parquet_path: Path, days: int, full: bool) -> pd.DataFrame:
    df = pd.read_parquet(parquet_path)
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

    # Replace pandas NaN/NaT with Python None for psycopg2's adapter.
    return df.astype(object).where(df.notna(), None)


def upsert(conn, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = ", ".join(COLUMNS)
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    update_cols = [c for c in COLUMNS if c not in (
        "act_symbol", "earnings_date", "snapshot_date", "model_horizon",
    )]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)

    sql = (
        f"INSERT INTO em_forecasts ({cols}) VALUES ({placeholders}) "
        "ON CONFLICT (act_symbol, earnings_date, snapshot_date, model_horizon) "
        f"DO UPDATE SET {set_clause}"
    )

    rows = list(df.itertuples(index=False, name=None))
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=1,
                    help="Import snapshots from the last N days (default 1)")
    ap.add_argument("--full", action="store_true",
                    help="Ignore --days; import every row in the Parquet")
    ap.add_argument("--file", default=None,
                    help="Specific Parquet file (default: newest forecasts_*.parquet)")
    args = ap.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        # The daily-refresh workflow runs this step unconditionally so a
        # missing DATABASE_URL (forks / preview branches without Neon
        # access) is a no-op rather than a workflow failure.
        print("DATABASE_URL not set — skipping em_forecasts import.")
        return 0

    parquet_path = pick_parquet(args.file)
    df = load_and_filter(parquet_path, args.days, args.full)
    print(f"import_recent_to_postgres: {parquet_path.name} → {len(df)} rows "
          f"({'full' if args.full else f'last {args.days}d'})")

    conn = psycopg2.connect(url)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
        with conn:
            count = upsert(conn, df)
        print(f"import_recent_to_postgres: upserted {count} rows")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
