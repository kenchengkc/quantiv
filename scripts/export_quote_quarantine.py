#!/usr/bin/env python3
"""Export the latest rejected option quotes/pairs to compact Parquet evidence."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import os
from pathlib import Path

import duckdb


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RETENTION_DAYS = 30


def export_quarantine(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path,
    *,
    source_date: date | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> tuple[Path, int]:
    selected_date = source_date or conn.execute(
        "SELECT MAX(date) FROM v_options"
    ).fetchone()[0]
    if selected_date is None:
        raise RuntimeError("options view is empty; quarantine cannot be built")
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"quote_quarantine_{selected_date.isoformat()}.parquet"
    temporary = output.with_suffix(output.suffix + ".tmp")
    escaped_temporary = str(temporary).replace("'", "''")
    conn.execute(
        f"""
        COPY (
            SELECT
                'contract'::VARCHAR AS record_type,
                date, act_symbol, expiration, strike, call_put,
                bid, ask, mid, relative_spread, iv, delta,
                option_volume, open_interest,
                quote_timestamp_precision, market_data_mode,
                rejection_reason,
                NULL::DOUBLE AS straddle_bid,
                NULL::DOUBLE AS straddle_ask,
                NULL::DOUBLE AS straddle_mid,
                NULL::DOUBLE AS straddle_relative_spread
            FROM v_option_quote_quarantine
            WHERE date = ?
            UNION ALL BY NAME
            SELECT
                'straddle_pair'::VARCHAR AS record_type,
                date, act_symbol, expiration, strike, NULL::VARCHAR AS call_put,
                NULL::DOUBLE AS bid, NULL::DOUBLE AS ask, NULL::DOUBLE AS mid,
                NULL::DOUBLE AS relative_spread,
                (call_iv + put_iv) / 2.0 AS iv,
                NULL::DOUBLE AS delta,
                NULL::BIGINT AS option_volume,
                NULL::BIGINT AS open_interest,
                quote_timestamp_precision, market_data_mode,
                pair_rejection_reason AS rejection_reason,
                straddle_bid, straddle_ask, straddle_mid,
                straddle_relative_spread
            FROM v_straddle_quote_quarantine
            WHERE date = ?
        ) TO '{escaped_temporary}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """,
        [selected_date, selected_date],
    )
    rows = int(
        conn.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(temporary)]).fetchone()[0]
    )
    temporary.replace(output)

    cutoff = selected_date - timedelta(days=retention_days)
    for candidate in output_dir.glob("quote_quarantine_*.parquet"):
        try:
            candidate_date = date.fromisoformat(candidate.stem.removeprefix("quote_quarantine_"))
        except ValueError:
            continue
        if candidate_date < cutoff:
            candidate.unlink()
    return output, rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duckdb-path", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--date", type=date.fromisoformat, default=None)
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    args = parser.parse_args()

    data_dir = Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
    db_path = args.duckdb_path or Path(
        os.getenv("DUCKDB_PATH", data_dir / "quantiv.duckdb")
    )
    output_dir = args.output_dir or data_dir / "quarantine" / "options"
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        output, rows = export_quarantine(
            conn,
            output_dir,
            source_date=args.date,
            retention_days=args.retention_days,
        )
    finally:
        conn.close()
    print(f"Quote quarantine: {rows:,} rejected records → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
