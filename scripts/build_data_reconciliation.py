#!/usr/bin/env python3
"""Build one exception-first reconciliation manifest from local pipeline data."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


REPO_ROOT = Path(__file__).resolve().parent.parent
ML_PACKAGE_ROOT = REPO_ROOT / "apps" / "ml"
if str(ML_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_PACKAGE_ROOT))

from ml.data_reconciliation import (  # noqa: E402 - standalone script path setup
    build_reconciliation_manifest,
)


def _view_stats(
    conn: duckdb.DuckDBPyConnection,
    view: str,
    *,
    max_lag_days: int | None,
) -> dict[str, Any]:
    try:
        minimum, maximum, rows, symbols = conn.execute(
            f"""
            SELECT MIN(date), MAX(date), COUNT(*), COUNT(DISTINCT act_symbol)
            FROM {view}
            """
        ).fetchone()
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}
    result: dict[str, Any] = {
        "status": "passed" if rows and maximum is not None else "failed",
        "rows": int(rows or 0),
        "symbols": int(symbols or 0),
        "min_date": minimum.isoformat() if minimum else None,
        "max_date": maximum.isoformat() if maximum else None,
    }
    if max_lag_days is not None:
        result["lag_days"] = (date.today() - maximum).days if maximum else None
        result["max_lag_days"] = max_lag_days
    return result


def _duplicate_stats(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    options = conn.execute(
        """
        SELECT COALESCE(SUM(total_rows - unique_rows), 0), COUNT(*)
        FROM (
            SELECT date,
                   COUNT(*) AS total_rows,
                   COUNT(DISTINCT (act_symbol, expiration, strike, call_put)) AS unique_rows
            FROM v_options
            WHERE date >= (SELECT MAX(date) FROM v_options) - INTERVAL 30 DAY
            GROUP BY date
            HAVING total_rows > unique_rows
        )
        """
    ).fetchone()
    ohlcv = conn.execute(
        """
        SELECT COALESCE(SUM(total_rows - unique_rows), 0), COUNT(*)
        FROM (
            SELECT date,
                   COUNT(*) AS total_rows,
                   COUNT(DISTINCT act_symbol) AS unique_rows
            FROM v_ohlcv
            WHERE date >= (SELECT MAX(date) FROM v_ohlcv) - INTERVAL 30 DAY
            GROUP BY date
            HAVING total_rows > unique_rows
        )
        """
    ).fetchone()
    return {
        "options": {
            "duplicate_rows": int(options[0]),
            "affected_dates": int(options[1]),
            "window_days": 30,
        },
        "ohlcv": {
            "duplicate_rows": int(ohlcv[0]),
            "affected_dates": int(ohlcv[1]),
            "window_days": 30,
        },
    }


def _coverage_ctes(days_ahead: int) -> str:
    return f"""
        WITH upcoming AS (
            SELECT DISTINCT act_symbol, CAST(date AS DATE) AS earnings_date, timing
            FROM v_earnings
            WHERE CAST(date AS DATE) BETWEEN CURRENT_DATE
                AND CURRENT_DATE + INTERVAL '{days_ahead}' DAY
        ),
        covered AS (
            SELECT DISTINCT u.act_symbol, u.earnings_date
            FROM upcoming u
            JOIN v_straddle_features sf
              ON sf.act_symbol = u.act_symbol
             AND sf.date < u.earnings_date
             AND (u.earnings_date - sf.date) BETWEEN 1 AND 25
             AND (
                  (LOWER(COALESCE(CAST(u.timing AS VARCHAR), '')) = 'amc'
                   AND sf.expiration > u.earnings_date)
                  OR
                  (LOWER(COALESCE(CAST(u.timing AS VARCHAR), '')) != 'amc'
                   AND sf.expiration >= u.earnings_date)
             )
        )
    """


def _event_coverage(
    conn: duckdb.DuckDBPyConnection,
    *,
    days_ahead: int,
) -> dict[str, Any]:
    ctes = _coverage_ctes(days_ahead)
    expected, covered = conn.execute(
        ctes
        + """
        SELECT
            (SELECT COUNT(*) FROM upcoming),
            (SELECT COUNT(*) FROM covered)
        """
    ).fetchone()
    missing_rows = conn.execute(
        ctes
        + """
        SELECT u.act_symbol, u.earnings_date
        FROM upcoming u
        LEFT JOIN covered c USING (act_symbol, earnings_date)
        WHERE c.act_symbol IS NULL
        ORDER BY u.earnings_date, u.act_symbol
        LIMIT 25
        """
    ).fetchall()
    expected = int(expected or 0)
    covered = int(covered or 0)
    return {
        "window_days": days_ahead,
        "expected_events": expected,
        "covered_events": covered,
        "missing_events": max(expected - covered, 0),
        "coverage_pct": round(covered / expected, 6) if expected else None,
        "missing_sample": [
            {"symbol": symbol, "earnings_date": earnings_date.isoformat()}
            for symbol, earnings_date in missing_rows
        ],
    }


def _config_items(path: Path, key: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, dict) else {}


def _active_stale_symbols(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
) -> list[str]:
    if not symbols:
        return []
    placeholders = ", ".join("?" for _ in symbols)
    rows = conn.execute(
        f"""
        SELECT DISTINCT act_symbol
        FROM (
            SELECT act_symbol FROM v_earnings
            WHERE CAST(date AS DATE) >= CURRENT_DATE
              AND act_symbol IN ({placeholders})
            UNION ALL
            SELECT act_symbol FROM v_options
            WHERE date = (SELECT MAX(date) FROM v_options)
              AND act_symbol IN ({placeholders})
        )
        ORDER BY act_symbol
        """,
        [*symbols, *symbols],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _symbol_mappings(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    renames = _config_items(REPO_ROOT / "config" / "ticker_renames.json", "renames")
    retired = _config_items(REPO_ROOT / "config" / "delisted_tickers.json", "tickers")
    old_symbols = sorted({*renames, *retired})
    return {
        "rename_rules": len(renames),
        "retired_symbols": len(retired),
        "stale_source_symbols": _active_stale_symbols(conn, old_symbols),
    }


def _corporate_actions(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "provider_enrichments" / "corporate_actions.json"
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {
            "status": "unavailable",
            "rows": 0,
            "symbols": 0,
            "events": 0,
            "continuity_status": "not_enforced",
        }
    rows = payload.get("rows") if isinstance(payload, dict) else []
    rows = [row for row in rows or [] if isinstance(row, dict)]
    return {
        "status": "observed",
        "generated_at": payload.get("generated_at"),
        "rows": len(rows),
        "symbols": len({row.get("symbol") for row in rows if row.get("symbol")}),
        "events": sum(int(row.get("event_count") or 0) for row in rows),
        "continuity_status": "observed_only",
    }


def _publish_manifest(
    manifest: dict[str, Any],
    *,
    report_path: Path,
    strict: bool,
) -> int:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    temporary.replace(report_path)
    quality = manifest["quality"]
    print(
        f"Data reconciliation: {quality['status']} · "
        f"{quality['critical_exceptions']} critical · {quality['warnings']} warnings"
    )
    print(f"Manifest: {manifest['manifest_id']} → {report_path}")
    for issue in manifest["exceptions"]:
        print(f"  [{issue['severity']}] {issue['code']}: {issue['summary']}")
    return 1 if strict and not quality["decision_safe"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=None,
        help="DuckDB file (defaults to DUCKDB_PATH or data/quantiv.duckdb)",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--max-lag-days", type=int, default=5)
    parser.add_argument("--days-ahead", type=int, default=21)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero when the manifest contains a critical exception",
    )
    args = parser.parse_args()

    data_dir = Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
    db_path = args.duckdb_path or Path(
        os.getenv("DUCKDB_PATH", data_dir / "quantiv.duckdb")
    )
    report_path = args.report or data_dir / "validation" / "data_reconciliation.json"
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception as exc:
        manifest = build_reconciliation_manifest(
            generated_at=datetime.now(timezone.utc).isoformat(),
            datasets={
                "duckdb": {
                    "status": "failed",
                    "rows": 0,
                    "error": str(exc),
                }
            },
            event_coverage={
                "window_days": args.days_ahead,
                "expected_events": 0,
                "covered_events": 0,
                "missing_events": 0,
                "coverage_pct": None,
                "missing_sample": [],
            },
            duplicates={},
            symbol_mappings={
                "rename_rules": 0,
                "retired_symbols": 0,
                "stale_source_symbols": [],
            },
            corporate_actions={
                "status": "unavailable",
                "rows": 0,
                "symbols": 0,
                "events": 0,
                "continuity_status": "not_enforced",
            },
            pipeline_controls={
                "quarantine": {"status": "not_instrumented", "mode": "fail_closed"},
                "idempotent_replay": {"status": "contract_only"},
            },
        )
        return _publish_manifest(
            manifest,
            report_path=report_path,
            strict=args.strict,
        )
    try:
        datasets = {
            "options": _view_stats(conn, "v_options", max_lag_days=args.max_lag_days),
            "ohlcv": _view_stats(conn, "v_ohlcv", max_lag_days=args.max_lag_days),
            "earnings": _view_stats(conn, "v_earnings", max_lag_days=None),
        }
        manifest = build_reconciliation_manifest(
            generated_at=datetime.now(timezone.utc).isoformat(),
            datasets=datasets,
            event_coverage=_event_coverage(conn, days_ahead=args.days_ahead),
            duplicates=_duplicate_stats(conn),
            symbol_mappings=_symbol_mappings(conn),
            corporate_actions=_corporate_actions(data_dir),
            pipeline_controls={
                "quarantine": {
                    "status": "not_instrumented",
                    "mode": "fail_closed",
                    "records": 0,
                },
                "idempotent_replay": {
                    "status": "contract_only",
                    "serving_key": [
                        "act_symbol",
                        "earnings_date",
                        "snapshot_date",
                        "model_horizon",
                    ],
                    "mechanism": "Parquet snapshots plus conflict-safe forecast upsert",
                },
            },
        )
    finally:
        conn.close()
    return _publish_manifest(
        manifest,
        report_path=report_path,
        strict=args.strict,
    )


if __name__ == "__main__":
    raise SystemExit(main())
