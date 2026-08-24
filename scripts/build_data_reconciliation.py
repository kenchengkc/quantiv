#!/usr/bin/env python3
"""Build one exception-first reconciliation manifest from local pipeline data."""

from __future__ import annotations

import argparse
import hashlib
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
from export_quote_quarantine import export_quarantine  # noqa: E402
from delisted import canonical_ticker, is_delisted  # noqa: E402


MODEL_HORIZONS = (1, 2, 3, 7, 14, 21)


def _retired_symbol_sql(column: str) -> str:
    """SQL predicate excluding configured delistings and renamed-away symbols."""
    renames = _config_items(REPO_ROOT / "config" / "ticker_renames.json", "renames")
    retired = _config_items(REPO_ROOT / "config" / "delisted_tickers.json", "tickers")
    symbols = sorted({*renames, *retired})
    if not symbols:
        return "TRUE"
    literals = ", ".join("'" + symbol.replace("'", "''") + "'" for symbol in symbols)
    return f"{column} NOT IN ({literals})"


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
    active_symbol_filter = _retired_symbol_sql("act_symbol")
    return f"""
        WITH latest AS (
            SELECT MAX(date) AS snapshot_date FROM v_options
        ), calendar_upcoming AS (
            SELECT DISTINCT act_symbol, CAST(date AS DATE) AS earnings_date, timing
            FROM v_earnings
            WHERE CAST(date AS DATE) BETWEEN CURRENT_DATE
                AND CURRENT_DATE + INTERVAL '{days_ahead}' DAY
        ), active_symbols AS (
            SELECT DISTINCT act_symbol
            FROM v_options CROSS JOIN latest
            WHERE date = latest.snapshot_date
              AND {active_symbol_filter}
        ), expected AS (
            SELECT u.*, latest.snapshot_date
            FROM calendar_upcoming u
            JOIN active_symbols a USING (act_symbol)
            CROSS JOIN latest
        ),
        covered AS (
            SELECT DISTINCT u.act_symbol, u.earnings_date
            FROM expected u
            JOIN v_straddle_features sf
              ON sf.act_symbol = u.act_symbol
             AND sf.date = u.snapshot_date
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
    calendar_events, expected, covered, active_symbols = conn.execute(
        ctes
        + """
        SELECT
            (SELECT COUNT(*) FROM calendar_upcoming),
            (SELECT COUNT(*) FROM expected),
            (SELECT COUNT(*) FROM covered),
            (SELECT COUNT(*) FROM active_symbols)
        """
    ).fetchone()
    missing_rows = conn.execute(
        ctes
        + """
        SELECT u.act_symbol, u.earnings_date
        FROM expected u
        LEFT JOIN covered c USING (act_symbol, earnings_date)
        WHERE c.act_symbol IS NULL
        ORDER BY u.earnings_date, u.act_symbol
        LIMIT 25
        """
    ).fetchall()
    outside_rows = conn.execute(
        ctes
        + """
        SELECT u.act_symbol, u.earnings_date
        FROM calendar_upcoming u
        LEFT JOIN active_symbols a USING (act_symbol)
        WHERE a.act_symbol IS NULL
        ORDER BY u.earnings_date, u.act_symbol
        LIMIT 25
        """
    ).fetchall()
    calendar_events = int(calendar_events or 0)
    expected = int(expected or 0)
    covered = int(covered or 0)
    active_symbols = int(active_symbols or 0)
    outside_universe = max(calendar_events - expected, 0)
    overall_coverage = round(covered / expected, 6) if expected else None
    policy = json.loads(
        (REPO_ROOT / "config" / "option_quote_quality.json").read_text()
    )
    min_event_coverage = float(policy["min_upcoming_event_coverage"])
    active_symbol_filter = _retired_symbol_sql("act_symbol")
    horizon_rows = conn.execute(
        f"""
        WITH latest AS (SELECT MAX(date) AS snapshot_date FROM v_options),
        active_symbols AS (
            SELECT DISTINCT act_symbol
            FROM v_options CROSS JOIN latest
            WHERE date = latest.snapshot_date
              AND {active_symbol_filter}
        ),
        expected AS (
            SELECT e.act_symbol, CAST(e.date AS DATE) AS earnings_date,
                   (CAST(e.date AS DATE) - l.snapshot_date) AS horizon,
                   e.timing, l.snapshot_date
            FROM v_earnings e
            JOIN active_symbols a USING (act_symbol)
            CROSS JOIN latest l
            WHERE (CAST(e.date AS DATE) - l.snapshot_date) IN (1, 2, 3, 7, 14, 21)
        ), covered AS (
            SELECT DISTINCT e.act_symbol, e.earnings_date, e.horizon
            FROM expected e
            JOIN v_straddle_features sf
              ON sf.act_symbol = e.act_symbol
             AND sf.date = e.snapshot_date
             AND (
                  (LOWER(COALESCE(CAST(e.timing AS VARCHAR), '')) = 'amc'
                   AND sf.expiration > e.earnings_date)
                  OR
                  (LOWER(COALESCE(CAST(e.timing AS VARCHAR), '')) != 'amc'
                   AND sf.expiration >= e.earnings_date)
             )
        )
        SELECT e.horizon, COUNT(*) AS expected,
               COUNT(c.act_symbol) AS covered
        FROM expected e
        LEFT JOIN covered c
          ON c.act_symbol = e.act_symbol
         AND c.earnings_date = e.earnings_date
         AND c.horizon = e.horizon
        GROUP BY e.horizon
        ORDER BY e.horizon
        """
    ).fetchall()
    min_horizon_coverage = float(policy["min_horizon_coverage"])
    by_horizon = {}
    failed_horizons = []
    horizon_expected = 0
    horizon_covered = 0
    for horizon, horizon_expected_rows, horizon_covered_rows in horizon_rows:
        horizon_expected_rows = int(horizon_expected_rows)
        horizon_covered_rows = int(horizon_covered_rows)
        coverage = horizon_covered_rows / horizon_expected_rows if horizon_expected_rows else 1.0
        by_horizon[str(int(horizon))] = {
            "expected_events": horizon_expected_rows,
            "covered_events": horizon_covered_rows,
            "coverage_pct": round(coverage, 6),
        }
        horizon_expected += horizon_expected_rows
        horizon_covered += horizon_covered_rows
        if coverage < min_horizon_coverage:
            failed_horizons.append(
                {"horizon": int(horizon), "coverage_pct": round(coverage, 6)}
            )
    return {
        "status": (
            "passed"
            if expected == 0 or (overall_coverage or 0.0) >= min_event_coverage
            else "failed"
        ),
        "window_days": days_ahead,
        "calendar_events": calendar_events,
        "decision_universe_symbols": active_symbols,
        "outside_option_universe_events": outside_universe,
        "outside_option_universe_sample": [
            {"symbol": symbol, "earnings_date": earnings_date.isoformat()}
            for symbol, earnings_date in outside_rows
        ],
        "expected_events": expected,
        "covered_events": covered,
        "missing_events": max(expected - covered, 0),
        "coverage_pct": overall_coverage,
        "minimum_coverage_pct": min_event_coverage,
        "missing_sample": [
            {"symbol": symbol, "earnings_date": earnings_date.isoformat()}
            for symbol, earnings_date in missing_rows
        ],
        "horizon_coverage": {
            "status": "failed" if failed_horizons else "passed",
            "minimum_coverage_pct": min_horizon_coverage,
            "expected_events": horizon_expected,
            "covered_events": horizon_covered,
            "missing_events": max(horizon_expected - horizon_covered, 0),
            "by_horizon": by_horizon,
            "failed_horizons": failed_horizons,
        },
    }


def _quote_quality(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    policy = json.loads(
        (REPO_ROOT / "config" / "option_quote_quality.json").read_text()
    )
    latest, contracts, rejected = conn.execute(
        """
        WITH latest AS (SELECT MAX(date) AS date FROM v_options)
        SELECT l.date, COUNT(*),
               COUNT(*) FILTER (WHERE quote_quality_status = 'rejected')
        FROM v_options CROSS JOIN latest l
        WHERE v_options.date = l.date
        GROUP BY l.date
        """
    ).fetchone()
    pairs, rejected_pairs, eligible_groups = conn.execute(
        """
        WITH latest AS (SELECT MAX(date) AS date FROM v_options)
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE pair_quality_status = 'rejected'),
               COUNT(DISTINCT (act_symbol, expiration))
                   FILTER (WHERE pair_quality_status = 'eligible')
        FROM v_straddle_candidates
        WHERE date = (SELECT date FROM latest)
        """
    ).fetchone()
    reason_rows = conn.execute(
        """
        SELECT quote_rejection_reason, COUNT(*) AS rows
        FROM v_options
        WHERE date = (SELECT MAX(date) FROM v_options)
          AND quote_rejection_reason IS NOT NULL
        GROUP BY quote_rejection_reason
        ORDER BY rows DESC, quote_rejection_reason
        LIMIT 10
        """
    ).fetchall()
    contracts = int(contracts or 0)
    rejected = int(rejected or 0)
    pairs = int(pairs or 0)
    rejected_pairs = int(rejected_pairs or 0)
    contract_rate = rejected / contracts if contracts else 1.0
    pair_rate = rejected_pairs / pairs if pairs else 1.0
    max_contract = float(policy["max_contract_rejection_rate"])
    max_pair = float(policy["max_pair_rejection_rate"])
    return {
        "status": (
            "passed"
            if contracts and pairs and contract_rate <= max_contract and pair_rate <= max_pair
            else "failed"
        ),
        "source_date": latest.isoformat() if latest else None,
        "contracts": contracts,
        "eligible_contracts": contracts - rejected,
        "rejected_contracts": rejected,
        "contract_rejection_rate": round(contract_rate, 6),
        "max_contract_rejection_rate": max_contract,
        "same_strike_pairs": pairs,
        "eligible_pairs": pairs - rejected_pairs,
        "rejected_pairs": rejected_pairs,
        "pair_rejection_rate": round(pair_rate, 6),
        "max_pair_rejection_rate": max_pair,
        "eligible_symbol_expirations": int(eligible_groups or 0),
        "top_rejection_reasons": [
            {"reason": str(reason), "rows": int(rows)} for reason, rows in reason_rows
        ],
        "timestamp_precision": policy["timestamp_precision"],
        "source_capabilities": policy["source_capabilities"],
        "decision_scope": policy["decision_scope"],
        "live_trading_eligible": bool(policy["live_trading_eligible"]),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_reconciliation(
    conn: duckdb.DuckDBPyConnection, data_dir: Path
) -> dict[str, Any]:
    latest = conn.execute("SELECT MAX(date) FROM v_options").fetchone()[0]
    errors: list[str] = []
    if latest is None:
        return {"status": "failed", "errors": ["options view is empty"]}
    manifest_path = data_dir / "control" / "ingestion" / "options" / f"{latest}.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "failed",
            "source_date": latest.isoformat(),
            "manifest": str(manifest_path),
            "errors": [f"ingestion manifest unavailable: {exc}"],
        }
    expected = int(manifest.get("expected_rows", -1))
    received = int(manifest.get("received_rows", -2))
    view_rows = int(
        conn.execute("SELECT COUNT(*) FROM v_options_raw WHERE date = ?", [latest]).fetchone()[0]
    )
    if expected != received:
        errors.append(f"expected_rows={expected} received_rows={received}")
    if received != view_rows:
        errors.append(f"manifest rows={received} DuckDB rows={view_rows}")
    partition_value = manifest.get("partition")
    partition = data_dir / str(partition_value) if partition_value else None
    if partition is None or not partition.exists():
        errors.append("canonical partition is missing")
    elif _sha256_file(partition) != manifest.get("partition_sha256"):
        errors.append("canonical partition hash does not match manifest")
    if manifest.get("replay_equivalence") != "verified":
        errors.append("idempotent replay equivalence is not verified")
    return {
        "status": "failed" if errors else "passed",
        "source_date": latest.isoformat(),
        "manifest": str(manifest_path),
        "expected_rows": expected,
        "received_rows": received,
        "duckdb_rows": view_rows,
        "partition_sha256": manifest.get("partition_sha256"),
        "content_sha256": manifest.get("content_sha256"),
        "replay_equivalence": manifest.get("replay_equivalence"),
        "errors": errors,
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
) -> dict[str, list[str]]:
    if not symbols:
        return {"earnings": [], "latest_options": []}
    placeholders = ", ".join("?" for _ in symbols)
    earnings_rows = conn.execute(
        f"""
        SELECT DISTINCT act_symbol
        FROM v_earnings
        WHERE CAST(date AS DATE) >= CURRENT_DATE
          AND act_symbol IN ({placeholders})
        ORDER BY act_symbol
        """,
        symbols,
    ).fetchall()
    option_rows = conn.execute(
        f"""
        SELECT DISTINCT act_symbol
        FROM v_options
        WHERE date = (SELECT MAX(date) FROM v_options)
          AND act_symbol IN ({placeholders})
        ORDER BY act_symbol
        """,
        symbols,
    ).fetchall()
    return {
        "earnings": [str(row[0]) for row in earnings_rows],
        "latest_options": [str(row[0]) for row in option_rows],
    }


def _symbol_mappings(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    renames = _config_items(REPO_ROOT / "config" / "ticker_renames.json", "renames")
    retired = _config_items(REPO_ROOT / "config" / "delisted_tickers.json", "tickers")
    old_symbols = sorted({*renames, *retired})
    active = _active_stale_symbols(conn, old_symbols)
    return {
        "rename_rules": len(renames),
        "retired_symbols": len(retired),
        # A retired symbol with a future event can enter scoring and is a hard
        # mapping failure. A quote lingering in the immutable latest source
        # partition is retained for audit/history but explicitly quarantined
        # from the decision-universe denominator.
        "stale_source_symbols": active["earnings"],
        "stale_earnings_symbols": active["earnings"],
        "quarantined_latest_option_symbols": active["latest_options"],
    }


def _corporate_actions(
    conn: duckdb.DuckDBPyConnection, data_dir: Path
) -> dict[str, Any]:
    """Verify the canonical split/dividend snapshot and its active-universe receipt."""
    latest = conn.execute("SELECT MAX(date) FROM v_options").fetchone()[0]
    receipt_name = f"{latest}.json" if latest is not None else "missing.json"
    path = data_dir / "control" / "ingestion" / "corporate_actions" / receipt_name
    errors: list[str] = []

    def receipt_int(value: object, label: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            errors.append(f"{label} is not an integer")
            return -1
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "unavailable",
            "manifest": str(path),
            "continuity_status": "not_enforced",
            "errors": [f"corporate-action receipt unavailable: {exc}"],
        }

    if payload.get("schema") != "quantiv.corporate-action-ingestion.v1":
        errors.append("corporate-action receipt schema is unsupported")
    if payload.get("source") != "dolthub:post-no-preference/stocks":
        errors.append("corporate-action source is not the canonical DoltHub stock feed")
    if payload.get("replay_equivalence") != "verified":
        errors.append("corporate-action replay equivalence is not verified")

    source_options_date = payload.get("source_options_date")
    if latest is None or source_options_date != latest.isoformat():
        errors.append(
            "corporate-action universe does not match the latest options partition"
        )
    try:
        query_start = date.fromisoformat(str(payload.get("query_start")))
        query_end = date.fromisoformat(str(payload.get("query_end")))
        if latest is not None and query_end < latest:
            errors.append("corporate-action query ends before the options source date")
        if query_start > date(2019, 1, 1):
            errors.append("corporate-action history does not cover the ML training window")
    except ValueError:
        errors.append("corporate-action query window is invalid")

    raw_symbols = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT act_symbol
            FROM v_options
            WHERE date = (SELECT MAX(date) FROM v_options)
            ORDER BY act_symbol
            """
        ).fetchall()
    ]
    symbols = sorted(
        {
            canonical_ticker(symbol)
            for symbol in raw_symbols
            if not is_delisted(symbol)
        }
    )
    symbols = [symbol for symbol in symbols if symbol]
    symbol_digest = hashlib.sha256("\n".join(symbols).encode()).hexdigest()
    universe = payload.get("universe") or {}
    if receipt_int(universe.get("symbols"), "corporate-action universe count") != len(symbols):
        errors.append("corporate-action universe row count does not match active options")
    if universe.get("symbols_sha256") != symbol_digest:
        errors.append("corporate-action universe hash does not match active options")

    verified_paths: dict[str, Path] = {}
    dataset_summary: dict[str, Any] = {}
    expected_columns = {
        "splits": ("to_factor > 0 AND for_factor > 0",),
        "dividends": ("amount >= 0",),
    }
    for name, valid_predicates in expected_columns.items():
        dataset = (payload.get("datasets") or {}).get(name) or {}
        partition_value = dataset.get("partition")
        partition = (data_dir / str(partition_value)).resolve() if partition_value else None
        if (
            partition is None
            or data_dir.resolve() not in partition.parents
            or not partition.exists()
        ):
            errors.append(f"{name} canonical partition is missing")
            continue
        verified_paths[name] = partition
        if _sha256_file(partition) != dataset.get("partition_sha256"):
            errors.append(f"{name} partition hash does not match receipt")
        try:
            rows, duplicates, invalid = conn.execute(
                f"""
                SELECT COUNT(*),
                       COUNT(*) - COUNT(DISTINCT (act_symbol, ex_date)),
                       COUNT(*) FILTER (
                           WHERE ({' AND '.join(valid_predicates)}) IS NOT TRUE
                       )
                FROM read_parquet(?)
                """,
                [str(partition)],
            ).fetchone()
            rows = int(rows or 0)
            duplicates = int(duplicates or 0)
            invalid = int(invalid or 0)
            receipt_rows = receipt_int(
                dataset.get("rows"), f"{name} receipt row count"
            )
            if rows != receipt_rows:
                errors.append(f"{name} Parquet row count does not match receipt")
            if duplicates:
                errors.append(f"{name} contains {duplicates} duplicate action keys")
            if invalid:
                errors.append(f"{name} contains {invalid} invalid action values")
            dataset_summary[name] = {
                "rows": rows,
                "partition": str(partition),
                "partition_sha256": dataset.get("partition_sha256"),
                "content_sha256": dataset.get("content_sha256"),
            }
        except Exception as exc:
            errors.append(f"{name} partition cannot be inspected: {exc}")
        batches = dataset.get("batches") or []
        if not batches or any(
            batch.get("completion") != "short_page"
            or receipt_int(batch.get("pages"), f"{name} batch page count") < 1
            for batch in batches
        ):
            errors.append(f"{name} does not prove exhaustive pagination")
        if batches:
            batch_rows = sum(
                receipt_int(batch.get("rows"), f"{name} batch row count")
                for batch in batches
            )
            batch_symbols = sum(
                receipt_int(batch.get("symbols"), f"{name} batch symbol count")
                for batch in batches
            )
            if batch_rows != receipt_int(dataset.get("rows"), f"{name} row count"):
                errors.append(f"{name} batch rows do not reconcile to the receipt")
            if batch_symbols != len(symbols):
                errors.append(f"{name} batches do not cover the active universe")
        if partition is not None and partition.stem != dataset.get("content_sha256"):
            errors.append(f"{name} content address does not match the receipt")

    split_crossings = 0
    dividend_crossings = 0
    if set(verified_paths) == {"splits", "dividends"}:
        try:
            split_crossings, dividend_crossings = conn.execute(
                """
                WITH historical AS (
                    SELECT act_symbol, CAST(date AS DATE) AS earnings_date
                    FROM v_earnings
                    WHERE CAST(date AS DATE) < CURRENT_DATE
                ), pre AS (
                    SELECT e.act_symbol, e.earnings_date, p.date AS pre_date,
                           ROW_NUMBER() OVER (
                               PARTITION BY e.act_symbol, e.earnings_date
                               ORDER BY p.date DESC
                           ) AS rn
                    FROM historical e
                    JOIN v_ohlcv p ON p.act_symbol = e.act_symbol
                     AND p.date < e.earnings_date
                     AND p.date >= e.earnings_date - INTERVAL '5' DAY
                ), post AS (
                    SELECT e.act_symbol, e.earnings_date, p.date AS post_date,
                           ROW_NUMBER() OVER (
                               PARTITION BY e.act_symbol, e.earnings_date
                               ORDER BY p.date ASC
                           ) AS rn
                    FROM historical e
                    JOIN v_ohlcv p ON p.act_symbol = e.act_symbol
                     AND p.date > e.earnings_date
                     AND p.date <= e.earnings_date + INTERVAL '5' DAY
                ), windows AS (
                    SELECT pre.act_symbol, pre.pre_date, post.post_date
                    FROM pre JOIN post USING (act_symbol, earnings_date)
                    WHERE pre.rn = 1 AND post.rn = 1
                )
                SELECT
                    (SELECT COUNT(*) FROM windows w JOIN read_parquet(?) s
                       ON s.act_symbol = w.act_symbol
                      AND s.ex_date > w.pre_date AND s.ex_date <= w.post_date),
                    (SELECT COUNT(*) FROM windows w JOIN read_parquet(?) d
                       ON d.act_symbol = w.act_symbol
                      AND d.ex_date > w.pre_date AND d.ex_date <= w.post_date)
                """,
                [str(verified_paths["splits"]), str(verified_paths["dividends"])],
            ).fetchone()
            split_crossings = int(split_crossings or 0)
            dividend_crossings = int(dividend_crossings or 0)
        except Exception as exc:
            errors.append(f"corporate-action event-window audit failed: {exc}")

    return {
        "status": "failed" if errors else "passed",
        "manifest": str(path),
        "generated_at": payload.get("generated_at"),
        "source": payload.get("source"),
        "source_options_date": source_options_date,
        "query_start": payload.get("query_start"),
        "query_end": payload.get("query_end"),
        "universe_symbols": len(symbols),
        "universe_sha256": symbol_digest,
        "datasets": dataset_summary,
        "split_event_window_crossings": split_crossings,
        "dividend_event_window_crossings": dividend_crossings,
        "replay_equivalence": payload.get("replay_equivalence"),
        "adjustment_contract": payload.get("adjustment_contract"),
        "continuity_status": "enforced" if not errors else "not_enforced",
        "errors": errors,
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
            quote_quality={"status": "not_enforced"},
            source_reconciliation={
                "status": "failed",
                "errors": ["DuckDB is unavailable"],
            },
        )
        return _publish_manifest(
            manifest,
            report_path=report_path,
            strict=args.strict,
        )
    try:
        quote_quality = _quote_quality(conn)
        source_reconciliation = _source_reconciliation(conn, data_dir)
        try:
            quarantine_path, quarantine_rows = export_quarantine(
                conn, data_dir / "quarantine" / "options"
            )
            quarantine = {
                "status": "enforced",
                "mode": "compact_parquet_ledger",
                "records": quarantine_rows,
                "artifact": str(quarantine_path),
                "retention_days": 30,
            }
        except Exception as exc:
            quarantine = {
                "status": "failed",
                "mode": "compact_parquet_ledger",
                "records": 0,
                "error": str(exc),
            }
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
            corporate_actions=_corporate_actions(conn, data_dir),
            pipeline_controls={
                "quarantine": quarantine,
                "idempotent_replay": {
                    "status": source_reconciliation.get("replay_equivalence"),
                    "serving_key": [
                        "act_symbol",
                        "earnings_date",
                        "snapshot_date",
                        "model_horizon",
                    ],
                    "mechanism": (
                        "deterministic logical-row digest plus atomic Parquet promotion"
                    ),
                },
            },
            quote_quality=quote_quality,
            source_reconciliation=source_reconciliation,
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