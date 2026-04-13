#!/usr/bin/env python3
"""
Set up DuckDB views over the date-partitioned Parquet files synced from DoltHub.

This replaces the old Postgres-based pipeline. DuckDB reads Parquet directly —
no import step needed.

Views created:
  - v_options_raw        : raw options chain data from Parquet
  - v_options            : cleaned/typed version with computed columns
  - v_atm_options        : ATM options (closest strike to spot proxy)
  - v_straddle_features  : ATM straddle features for EM calculation
  - v_earnings           : earnings calendar (from CSV)
  - v_volhist_norm       : normalized volatility history (from CSV if available)

Usage:
  python scripts/setup_duckdb_from_parquet.py
  python scripts/setup_duckdb_from_parquet.py --duckdb-path ./data/quantiv.duckdb
"""

import argparse
import os
from pathlib import Path
import duckdb


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))


def get_duckdb_path(args_path: str | None = None) -> Path:
    if args_path:
        return Path(args_path)
    return Path(os.getenv("DUCKDB_PATH", str(get_data_dir() / "quantiv.duckdb")))


def setup_views(conn: duckdb.DuckDBPyConnection, data_dir: Path):
    parquet_glob = str(data_dir / "parquet" / "options_chain" / "year=*" / "month=*" / "*.parquet")
    earnings_csv = data_dir / "earnings_calendar.csv"
    volhist_csv = data_dir / "volatility_history.csv"

    print(f"[views] Parquet glob: {parquet_glob}")

    # ── Raw options view ──────────────────────────────────────────────
    conn.execute(f"""
        CREATE OR REPLACE VIEW v_options_raw AS
        SELECT * FROM read_parquet('{parquet_glob}', hive_partitioning=true)
    """)
    count = conn.execute("SELECT COUNT(*) FROM v_options_raw").fetchone()[0]
    print(f"[views] v_options_raw: {count:,} rows")

    # ── Cleaned options view with computed columns ────────────────────
    conn.execute("""
        CREATE OR REPLACE VIEW v_options AS
        SELECT
            date,
            act_symbol,
            expiration,
            strike,
            call_put,
            bid,
            ask,
            (bid + ask) / 2.0 AS mid,
            vol AS iv,
            delta,
            gamma,
            theta,
            vega,
            rho,
            -- Days to expiration
            DATE_DIFF('day', date, expiration) AS dte,
            -- Moneyness (rough proxy: strike vs mid of bid/ask for ATM detection)
            CASE WHEN vol IS NOT NULL AND vol > 0 THEN vol ELSE NULL END AS iv_clean
        FROM v_options_raw
        WHERE act_symbol IS NOT NULL
          AND strike > 0
    """)
    print("[views] v_options: created")

    # ── ATM options (per symbol/date/expiration) ──────────────────────
    conn.execute("""
        CREATE OR REPLACE VIEW v_atm_options AS
        WITH ranked AS (
            SELECT
                *,
                -- Approximate ATM: use the strike where |delta| is closest to 0.5
                -- Fallback: use the strike closest to the midpoint of all strikes
                ROW_NUMBER() OVER (
                    PARTITION BY date, act_symbol, expiration, call_put
                    ORDER BY ABS(COALESCE(delta, 0) - CASE WHEN call_put = 'Call' THEN 0.5 ELSE -0.5 END)
                ) AS atm_rank
            FROM v_options
            WHERE dte BETWEEN 1 AND 60
              AND iv_clean IS NOT NULL
        )
        SELECT * EXCLUDE(atm_rank)
        FROM ranked
        WHERE atm_rank = 1
    """)
    print("[views] v_atm_options: created")

    # ── Straddle features for expected move calculation ────────────────
    conn.execute("""
        CREATE OR REPLACE VIEW v_straddle_features AS
        SELECT
            c.date,
            c.act_symbol,
            c.expiration,
            c.dte,
            c.strike AS atm_strike,
            c.iv AS atm_call_iv,
            p.iv AS atm_put_iv,
            (COALESCE(c.iv, 0) + COALESCE(p.iv, 0)) / 2.0 AS atm_iv,
            c.mid AS call_mid,
            p.mid AS put_mid,
            COALESCE(c.mid, 0) + COALESCE(p.mid, 0) AS straddle_mid,
            -- Expected move (straddle method): EM ≈ straddle_mid
            COALESCE(c.mid, 0) + COALESCE(p.mid, 0) AS em_straddle,
            -- Expected move (IV method): EM ≈ S * σ * √(T/365)
            -- We don't have spot price directly, so use strike as proxy for ATM
            c.strike * ((COALESCE(c.iv, 0) + COALESCE(p.iv, 0)) / 2.0)
                     * SQRT(c.dte / 365.0) AS em_iv,
            -- Greeks
            c.delta AS call_delta,
            p.delta AS put_delta,
            c.gamma AS call_gamma,
            c.vega AS call_vega,
            c.theta AS call_theta
        FROM v_atm_options c
        JOIN v_atm_options p
            ON  c.date = p.date
            AND c.act_symbol = p.act_symbol
            AND c.expiration = p.expiration
            AND c.call_put = 'Call'
            AND p.call_put = 'Put'
    """)
    print("[views] v_straddle_features: created")

    # ── Earnings calendar ─────────────────────────────────────────────
    earnings_parquet = data_dir / "earnings_calendar.parquet"
    if earnings_parquet.exists():
        conn.execute(f"""
            CREATE OR REPLACE VIEW v_earnings AS
            SELECT * FROM read_parquet('{earnings_parquet}')
        """)
        ecount = conn.execute("SELECT COUNT(*) FROM v_earnings").fetchone()[0]
        print(f"[views] v_earnings: {ecount:,} rows (from {earnings_parquet.name})")
    elif earnings_csv.exists():
        conn.execute(f"""
            CREATE OR REPLACE VIEW v_earnings AS
            SELECT * FROM read_csv_auto('{earnings_csv}')
        """)
        ecount = conn.execute("SELECT COUNT(*) FROM v_earnings").fetchone()[0]
        print(f"[views] v_earnings: {ecount:,} rows (from {earnings_csv.name}, fallback)")
    else:
        print(f"[views] ⚠ No earnings data found (run: python scripts/sync_dolthub.py --earnings)")

    # ── Volatility history ────────────────────────────────────────────
    if volhist_csv.exists():
        conn.execute(f"""
            CREATE OR REPLACE VIEW v_volhist_raw AS
            SELECT * FROM read_csv_auto('{volhist_csv}')
        """)
        vcount = conn.execute("SELECT COUNT(*) FROM v_volhist_raw").fetchone()[0]
        print(f"[views] v_volhist_raw: {vcount:,} rows (from {volhist_csv.name})")
    else:
        print(f"[views] ⚠ Volatility history CSV not found at {volhist_csv}")

    # ── Summary ───────────────────────────────────────────────────────
    print("\n[summary] Available views:")
    views = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_type = 'VIEW' ORDER BY 1").fetchall()
    for (v,) in views:
        print(f"  - {v}")


def main():
    parser = argparse.ArgumentParser(description="Set up DuckDB views over Parquet options data")
    parser.add_argument("--duckdb-path", type=str, default=None)
    args = parser.parse_args()

    data_dir = get_data_dir()
    db_path = get_duckdb_path(args.duckdb_path)

    print(f"Data dir:    {data_dir}")
    print(f"DuckDB path: {db_path}")

    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("INSTALL parquet; LOAD parquet;")
    except Exception:
        pass

    setup_views(conn, data_dir)
    conn.close()
    print("\n✅ DuckDB setup complete")


if __name__ == "__main__":
    main()
