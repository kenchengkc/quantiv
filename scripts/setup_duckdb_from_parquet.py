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
        print("[views] ⚠ No earnings data found (run: python scripts/sync_dolthub.py --earnings)")

    # ── Volatility history (prefer Parquet, fall back to CSV) ───────
    volhist_parquet_dir = data_dir / "parquet" / "volatility_history"
    if volhist_parquet_dir.exists() and any(volhist_parquet_dir.rglob("*.parquet")):
        volhist_glob = str(volhist_parquet_dir / "year=*" / "month=*" / "*.parquet")
        conn.execute(f"""
            CREATE OR REPLACE VIEW v_volhist_raw AS
            SELECT * FROM read_parquet('{volhist_glob}', hive_partitioning=true)
        """)
        vcount = conn.execute("SELECT COUNT(*) FROM v_volhist_raw").fetchone()[0]
        print(f"[views] v_volhist_raw: {vcount:,} rows (from Parquet)")
    elif volhist_csv.exists():
        conn.execute(f"""
            CREATE OR REPLACE VIEW v_volhist_raw AS
            SELECT * FROM read_csv_auto('{volhist_csv}')
        """)
        vcount = conn.execute("SELECT COUNT(*) FROM v_volhist_raw").fetchone()[0]
        print(f"[views] v_volhist_raw: {vcount:,} rows (from {volhist_csv.name}, fallback)")
    else:
        print("[views] ⚠ Volatility history not found (neither Parquet nor CSV)")

    # ── VIX (CBOE Volatility Index, from FRED) ──────────────────────
    vix_path = data_dir / "parquet" / "vix" / "vix.parquet"
    if vix_path.exists():
        conn.execute(f"""
            CREATE OR REPLACE VIEW v_vix AS
            SELECT CAST(date AS DATE) AS date, CAST(vix_close AS DOUBLE) AS vix_close
            FROM read_parquet('{vix_path}')
        """)
        vix_count = conn.execute("SELECT COUNT(*) FROM v_vix").fetchone()[0]
        print(f"[views] v_vix: {vix_count:,} rows")
    else:
        print("[views] ⚠ VIX not found (run: python scripts/sync_vix.py)")

    # ── OHLCV stock prices ───────────────────────────────────────────
    ohlcv_glob = str(data_dir / "parquet" / "ohlcv" / "year=*" / "month=*" / "*.parquet")
    ohlcv_dir = data_dir / "parquet" / "ohlcv"
    if ohlcv_dir.exists() and any(ohlcv_dir.rglob("*.parquet")):
        conn.execute(f"""
            CREATE OR REPLACE VIEW v_ohlcv AS
            SELECT * FROM read_parquet('{ohlcv_glob}', hive_partitioning=true)
        """)
        ocount = conn.execute("SELECT COUNT(*) FROM v_ohlcv").fetchone()[0]
        print(f"[views] v_ohlcv: {ocount:,} rows")

        # ── Realized volatility estimators ────────────────────────────
        # Computes rolling Parkinson, close-to-close, and Yang-Zhang estimates
        conn.execute("""
            CREATE OR REPLACE VIEW v_realized_vol AS
            WITH daily AS (
                SELECT
                    act_symbol,
                    date,
                    open, high, low, close, volume,
                    LN(high / NULLIF(low, 0)) AS hl_log,
                    LN(close / NULLIF(
                        LAG(close) OVER (PARTITION BY act_symbol ORDER BY date), 0
                    )) AS cc_log_return
                FROM v_ohlcv
                WHERE close > 0 AND high > 0 AND low > 0 AND open > 0
            ),
            -- Stage 1: rolling vol estimates (no nesting)
            stage1 AS (
                SELECT
                    act_symbol, date, close, volume,

                    -- Parkinson RV (annualized)
                    SQRT(252.0 / (4.0 * 5  * LN(2)) * SUM(hl_log * hl_log) OVER w5 ) AS parkinson_rv_5d,
                    SQRT(252.0 / (4.0 * 10 * LN(2)) * SUM(hl_log * hl_log) OVER w10) AS parkinson_rv_10d,
                    SQRT(252.0 / (4.0 * 20 * LN(2)) * SUM(hl_log * hl_log) OVER w20) AS parkinson_rv_20d,
                    SQRT(252.0 / (4.0 * 60 * LN(2)) * SUM(hl_log * hl_log) OVER w60) AS parkinson_rv_60d,

                    -- Close-to-close RV (annualized)
                    SQRT(252.0 / 10 * SUM(cc_log_return * cc_log_return) OVER w10) AS cc_rv_10d,
                    SQRT(252.0 / 20 * SUM(cc_log_return * cc_log_return) OVER w20) AS cc_rv_20d,

                    -- Volume ratio
                    volume * 1.0 / NULLIF(AVG(volume) OVER w20, 0) AS volume_ratio_20d,

                    -- 5-day drift
                    EXP(SUM(cc_log_return) OVER w5) - 1.0 AS drift_5d

                FROM daily
                WINDOW
                    w5  AS (PARTITION BY act_symbol ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
                    w10 AS (PARTITION BY act_symbol ORDER BY date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW),
                    w20 AS (PARTITION BY act_symbol ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
                    w60 AS (PARTITION BY act_symbol ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
            )
            -- Stage 2: vol-of-vol uses the 5d Parkinson from stage1
            SELECT
                act_symbol, date, close, volume,
                parkinson_rv_10d, parkinson_rv_20d, parkinson_rv_60d,
                cc_rv_10d, cc_rv_20d,
                volume_ratio_20d, drift_5d,
                STDDEV(parkinson_rv_5d) OVER (
                    PARTITION BY act_symbol ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ) AS vol_of_vol_20d
            FROM stage1
        """)
        print("[views] v_realized_vol: created (parkinson, cc, vol-of-vol, volume ratio, drift)")

        # ── IV vs RV feature view (join options ATM IV with realized vol) ─
        conn.execute("""
            CREATE OR REPLACE VIEW v_iv_rv_features AS
            SELECT
                s.act_symbol,
                s.date,
                s.expiration,
                s.dte,
                s.atm_strike,
                s.atm_iv,
                s.straddle_mid,
                s.em_straddle,
                s.em_iv,
                -- Realized vol
                rv.parkinson_rv_10d,
                rv.parkinson_rv_20d,
                rv.parkinson_rv_60d,
                rv.cc_rv_10d,
                rv.cc_rv_20d,
                rv.vol_of_vol_20d,
                rv.volume_ratio_20d,
                rv.drift_5d,
                rv.close AS spot_price,
                -- IV / RV ratios (key predictive features)
                s.atm_iv / NULLIF(rv.parkinson_rv_20d, 0) AS iv_rv_ratio_20d,
                s.atm_iv / NULLIF(rv.parkinson_rv_60d, 0) AS iv_rv_ratio_60d,
                s.atm_iv / NULLIF(rv.cc_rv_20d, 0) AS iv_cc_rv_ratio_20d,
                -- Vol regime
                rv.parkinson_rv_10d / NULLIF(rv.parkinson_rv_60d, 0) AS rv_term_ratio
            FROM v_straddle_features s
            JOIN v_realized_vol rv
                ON s.act_symbol = rv.act_symbol
                AND s.date = rv.date
        """)
        iv_rv_count = conn.execute("SELECT COUNT(*) FROM v_iv_rv_features").fetchone()[0]
        print(f"[views] v_iv_rv_features: {iv_rv_count:,} rows (options + OHLCV joined)")

    else:
        print("[views] ⚠ No OHLCV data found (run: python scripts/sync_dolthub.py --ohlcv)")

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
