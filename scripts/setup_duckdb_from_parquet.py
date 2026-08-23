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
import json
import os
from pathlib import Path
import duckdb


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))


def get_duckdb_path(args_path: str | None = None) -> Path:
    if args_path:
        return Path(args_path)
    return Path(os.getenv("DUCKDB_PATH", str(get_data_dir() / "quantiv.duckdb")))


def quote_quality_policy() -> dict[str, object]:
    """Load the checked-in EOD market-data acceptance contract."""
    path = Path(__file__).resolve().parent.parent / "config" / "option_quote_quality.json"
    policy = json.loads(path.read_text())
    required = {
        "market_data_mode",
        "timestamp_precision",
        "max_leg_relative_spread",
        "max_straddle_relative_spread",
        "min_bid",
        "min_ask",
        "min_iv",
        "max_iv",
        "min_dte",
        "max_dte",
    }
    missing = sorted(required - set(policy))
    if missing:
        raise ValueError(f"quote-quality policy is missing keys: {missing}")
    return policy


def setup_views(conn: duckdb.DuckDBPyConnection, data_dir: Path):
    policy = quote_quality_policy()
    min_bid = float(policy["min_bid"])
    min_ask = float(policy["min_ask"])
    min_iv = float(policy["min_iv"])
    max_iv = float(policy["max_iv"])
    min_dte = int(policy["min_dte"])
    max_dte = int(policy["max_dte"])
    max_leg_spread = float(policy["max_leg_relative_spread"])
    max_straddle_spread = float(policy["max_straddle_relative_spread"])
    max_quote_time_skew_seconds = int(policy["max_quote_time_skew_seconds"])
    min_option_volume = int(policy["min_option_volume_when_available"])
    min_open_interest = int(policy["min_open_interest_when_available"])
    timestamp_precision = str(policy["timestamp_precision"])
    market_data_mode = str(policy["market_data_mode"])
    parquet_glob = str(data_dir / "parquet" / "options_chain" / "year=*" / "month=*" / "*.parquet")
    earnings_csv = data_dir / "earnings_calendar.csv"
    volhist_csv = data_dir / "volatility_history.csv"

    print(f"[views] Parquet glob: {parquet_glob}")

    # ── Raw options view ──────────────────────────────────────────────
    parquet_scan = (
        f"read_parquet('{parquet_glob}', "
        "hive_partitioning=true, union_by_name=true)"
    )
    parquet_columns = {
        str(row[0])
        for row in conn.execute(f"DESCRIBE SELECT * FROM {parquet_scan}").fetchall()
    }
    compatibility_columns = {
        "quote_timestamp": "CAST(NULL AS TIMESTAMP)",
        "option_volume": "CAST(NULL AS BIGINT)",
        "open_interest": "CAST(NULL AS BIGINT)",
    }
    missing_expressions = [
        f"{expression} AS {name}"
        for name, expression in compatibility_columns.items()
        if name not in parquet_columns
    ]
    select_list = "*, " + ", ".join(missing_expressions) if missing_expressions else "*"
    conn.execute(f"""
        CREATE OR REPLACE VIEW v_options_raw AS
        SELECT {select_list} FROM {parquet_scan}
    """)
    count = conn.execute("SELECT COUNT(*) FROM v_options_raw").fetchone()[0]
    print(f"[views] v_options_raw: {count:,} rows")

    # ── Cleaned options view with computed columns ────────────────────
    conn.execute(f"""
        CREATE OR REPLACE VIEW v_options AS
        WITH normalized AS (
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
            DATE_DIFF('day', date, expiration) AS dte,
            quote_timestamp AS source_quote_timestamp,
            option_volume,
            open_interest,
            CASE WHEN quote_timestamp IS NULL THEN '{timestamp_precision}'
                 ELSE 'timestamp' END::VARCHAR AS quote_timestamp_precision,
            '{market_data_mode}'::VARCHAR AS market_data_mode,
            CASE
                WHEN bid IS NOT NULL AND ask IS NOT NULL AND (bid + ask) > 0
                THEN (ask - bid) / ((bid + ask) / 2.0)
                ELSE NULL
            END AS relative_spread,
            CASE WHEN vol IS NOT NULL AND vol > 0 THEN vol ELSE NULL END AS iv_clean
        FROM v_options_raw
        WHERE act_symbol IS NOT NULL
          AND strike > 0
        )
        SELECT
            *,
            CASE
                WHEN bid IS NULL OR ask IS NULL THEN 'missing_market_side'
                WHEN bid < 0 OR ask < 0 THEN 'negative_market_side'
                WHEN bid < {min_bid} OR ask < {min_ask} THEN 'zero_or_noncommercial_side'
                WHEN ask < bid THEN 'crossed_market'
                WHEN iv IS NULL OR iv < {min_iv} OR iv > {max_iv} THEN 'invalid_iv'
                WHEN source_quote_timestamp IS NOT NULL
                  AND CAST(source_quote_timestamp AS DATE) != date THEN 'stale_quote_timestamp'
                WHEN option_volume IS NOT NULL AND open_interest IS NOT NULL
                  AND option_volume < {min_option_volume}
                  AND open_interest < {min_open_interest} THEN 'illiquid_contract'
                WHEN dte < {min_dte} OR dte > {max_dte} THEN 'dte_out_of_policy'
                WHEN relative_spread > {max_leg_spread} THEN 'excessive_leg_spread'
                ELSE NULL
            END AS quote_rejection_reason,
            CASE
                WHEN bid IS NULL OR ask IS NULL
                  OR bid < {min_bid} OR ask < {min_ask}
                  OR ask < bid
                  OR iv IS NULL OR iv < {min_iv} OR iv > {max_iv}
                  OR (source_quote_timestamp IS NOT NULL
                      AND CAST(source_quote_timestamp AS DATE) != date)
                  OR (option_volume IS NOT NULL AND open_interest IS NOT NULL
                      AND option_volume < {min_option_volume}
                      AND open_interest < {min_open_interest})
                  OR dte < {min_dte} OR dte > {max_dte}
                  OR relative_spread > {max_leg_spread}
                THEN 'rejected'
                ELSE 'eligible'
            END AS quote_quality_status,
            CASE
                WHEN relative_spread <= 0.10
                  AND (open_interest IS NULL OR open_interest >= 500)
                  AND (option_volume IS NULL OR option_volume >= 100) THEN 'tight'
                WHEN relative_spread <= 0.25 THEN 'standard'
                WHEN relative_spread <= {max_leg_spread} THEN 'wide_accepted'
                ELSE 'rejected'
            END AS liquidity_tier,
            CASE WHEN option_volume IS NOT NULL OR open_interest IS NOT NULL
                 THEN 'spread_volume_open_interest'
                 ELSE 'quote_spread_proxy' END::VARCHAR AS liquidity_tier_method
        FROM normalized
    """)
    print("[views] v_options: created")

    # ── Quote quarantine (row-level failures and unpaired contracts) ──
    conn.execute("""
        CREATE OR REPLACE VIEW v_option_quote_quarantine AS
        SELECT *, quote_rejection_reason AS rejection_reason
        FROM v_options
        WHERE quote_quality_status = 'rejected'
        UNION ALL BY NAME
        SELECT q.*, 'missing_same_strike_opposite_leg' AS rejection_reason
        FROM v_options q
        WHERE q.quote_quality_status = 'eligible'
          AND NOT EXISTS (
              SELECT 1
              FROM v_options opposite
              WHERE opposite.date = q.date
                AND opposite.act_symbol = q.act_symbol
                AND opposite.expiration = q.expiration
                AND opposite.strike = q.strike
                AND opposite.call_put != q.call_put
                AND opposite.call_put IN ('Call', 'Put')
          )
    """)
    print("[views] v_option_quote_quarantine: created")

    # Pair first, then rank. Calls and puts can never come from different strikes.
    conn.execute(f"""
        CREATE OR REPLACE VIEW v_straddle_candidates AS
        WITH pairs AS (
            SELECT
                c.date,
                c.act_symbol,
                c.expiration,
                c.dte,
                c.strike,
                c.bid AS call_bid,
                c.ask AS call_ask,
                c.mid AS call_mid,
                c.relative_spread AS call_relative_spread,
                c.iv AS call_iv,
                c.delta AS call_delta,
                c.gamma AS call_gamma,
                c.vega AS call_vega,
                c.theta AS call_theta,
                c.option_volume AS call_volume,
                c.open_interest AS call_open_interest,
                c.source_quote_timestamp AS call_quote_timestamp,
                p.bid AS put_bid,
                p.ask AS put_ask,
                p.mid AS put_mid,
                p.relative_spread AS put_relative_spread,
                p.iv AS put_iv,
                p.delta AS put_delta,
                p.option_volume AS put_volume,
                p.open_interest AS put_open_interest,
                p.source_quote_timestamp AS put_quote_timestamp,
                c.bid + p.bid AS straddle_bid,
                c.ask + p.ask AS straddle_ask,
                c.mid + p.mid AS straddle_mid,
                (c.ask + p.ask - c.bid - p.bid)
                    / NULLIF(c.mid + p.mid, 0) AS straddle_relative_spread,
                ABS(COALESCE(c.delta, 0.5) - 0.5)
                    + ABS(COALESCE(p.delta, -0.5) + 0.5) AS atm_delta_distance,
                c.quote_timestamp_precision,
                c.market_data_mode,
                CASE
                    WHEN c.quote_quality_status != 'eligible'
                        THEN 'call_' || c.quote_rejection_reason
                    WHEN p.quote_quality_status != 'eligible'
                        THEN 'put_' || p.quote_rejection_reason
                    WHEN (c.source_quote_timestamp IS NULL)
                         != (p.source_quote_timestamp IS NULL)
                        THEN 'unsynchronized_quote_timestamp_availability'
                    WHEN c.source_quote_timestamp IS NOT NULL
                     AND ABS(EPOCH(c.source_quote_timestamp)
                             - EPOCH(p.source_quote_timestamp)) > {max_quote_time_skew_seconds}
                        THEN 'quote_timestamp_skew'
                    WHEN (c.ask + p.ask - c.bid - p.bid)
                           / NULLIF(c.mid + p.mid, 0) > {max_straddle_spread}
                        THEN 'excessive_straddle_spread'
                    ELSE NULL
                END AS pair_rejection_reason
            FROM v_options c
            JOIN v_options p
              ON p.date = c.date
             AND p.act_symbol = c.act_symbol
             AND p.expiration = c.expiration
             AND p.strike = c.strike
             AND c.call_put = 'Call'
             AND p.call_put = 'Put'
        ), ranked AS (
            SELECT *,
                CASE WHEN pair_rejection_reason IS NULL
                     THEN 'eligible' ELSE 'rejected' END AS pair_quality_status,
                ROW_NUMBER() OVER (
                    PARTITION BY date, act_symbol, expiration
                    ORDER BY
                        CASE WHEN pair_rejection_reason IS NULL THEN 0 ELSE 1 END,
                        atm_delta_distance,
                        straddle_relative_spread,
                        strike
                ) AS pair_rank
            FROM pairs
        )
        SELECT * FROM ranked
    """)
    print("[views] v_straddle_candidates: created")

    conn.execute("""
        CREATE OR REPLACE VIEW v_straddle_quote_quarantine AS
        SELECT *
        FROM v_straddle_candidates
        WHERE pair_quality_status = 'rejected'
    """)
    print("[views] v_straddle_quote_quarantine: created")

    # Backward-compatible selected-leg view, sourced from one common-strike pair.
    conn.execute("""
        CREATE OR REPLACE VIEW v_atm_options AS
        SELECT q.*
        FROM v_options q
        JOIN v_straddle_candidates selected
          ON selected.date = q.date
         AND selected.act_symbol = q.act_symbol
         AND selected.expiration = q.expiration
         AND selected.strike = q.strike
         AND selected.pair_rank = 1
         AND selected.pair_quality_status = 'eligible'
        WHERE q.call_put IN ('Call', 'Put')
    """)
    print("[views] v_atm_options: created")

    # ── Straddle features for expected move calculation ────────────────
    conn.execute("""
        CREATE OR REPLACE VIEW v_straddle_features AS
        SELECT
            date,
            act_symbol,
            expiration,
            dte,
            strike AS atm_strike,
            call_iv AS atm_call_iv,
            put_iv AS atm_put_iv,
            (call_iv + put_iv) / 2.0 AS atm_iv,
            call_bid,
            call_ask,
            call_mid,
            call_relative_spread,
            call_volume,
            call_open_interest,
            call_quote_timestamp,
            put_bid,
            put_ask,
            put_mid,
            put_relative_spread,
            put_volume,
            put_open_interest,
            put_quote_timestamp,
            straddle_bid,
            straddle_ask,
            straddle_mid,
            straddle_relative_spread,
            quote_timestamp_precision,
            market_data_mode,
            'passed'::VARCHAR AS quote_quality_status,
            CASE
                WHEN GREATEST(call_relative_spread, put_relative_spread) <= 0.10 THEN 'tight'
                WHEN GREATEST(call_relative_spread, put_relative_spread) <= 0.25 THEN 'standard'
                ELSE 'wide_accepted'
            END AS liquidity_tier,
            CASE WHEN call_volume IS NOT NULL OR call_open_interest IS NOT NULL
                       OR put_volume IS NOT NULL OR put_open_interest IS NOT NULL
                 THEN 'spread_volume_open_interest'
                 ELSE 'quote_spread_proxy' END::VARCHAR AS liquidity_tier_method,
            CAST(NULL AS VARCHAR) AS quote_rejection_reason,
            -- Expected move (straddle method): EM ≈ straddle_mid
            straddle_mid AS em_straddle,
            -- Expected move (IV method): EM ≈ S * σ * √(T/365)
            -- We don't have spot price directly, so use strike as proxy for ATM
            strike * ((call_iv + put_iv) / 2.0) * SQRT(dte / 365.0) AS em_iv,
            -- Greeks
            call_delta,
            put_delta,
            call_gamma,
            call_vega,
            call_theta
        FROM v_straddle_candidates
        WHERE pair_rank = 1
          AND pair_quality_status = 'eligible'
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

    # ── VIX (CBOE Volatility Index, authoritative CBOE history) ─────
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
