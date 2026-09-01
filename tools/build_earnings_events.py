#!/usr/bin/env python3
"""
Build canonical earnings_events table from earnings_calendar.csv
Creates the backbone for lead-time aware EM calculations.
"""

import duckdb
import json
import sys
from pathlib import Path
from typing import Dict, Any

def load_env_file(env_path: Path) -> Dict[str, str]:
    """Load environment variables from .env file."""
    env_vars = {}
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
    return env_vars

def build_earnings_events_table(conn: duckdb.DuckDBPyConnection, earnings_csv: Path):
    """Build canonical earnings_events table from CSV."""
    
    print("📅 Building canonical earnings_events table...")

    header = set()
    if earnings_csv.exists():
        with open(earnings_csv, "r", encoding="utf-8") as f:
            first = f.readline().strip()
        header = {c.strip() for c in first.split(",") if c.strip()}

    fiscal_year_expr = (
        "COALESCE(TRY_CAST(fiscal_year AS BIGINT), EXTRACT(YEAR FROM CAST(date AS DATE)))"
        if "fiscal_year" in header
        else "EXTRACT(YEAR FROM CAST(date AS DATE))"
    )
    fiscal_q_expr = (
        """
            CASE
                WHEN UPPER(TRIM(fiscal_q)) IN ('Q1','Q2','Q3','Q4') THEN UPPER(TRIM(fiscal_q))
                WHEN EXTRACT(MONTH FROM CAST(date AS DATE)) IN (1,2,3) THEN 'Q1'
                WHEN EXTRACT(MONTH FROM CAST(date AS DATE)) IN (4,5,6) THEN 'Q2'
                WHEN EXTRACT(MONTH FROM CAST(date AS DATE)) IN (7,8,9) THEN 'Q3'
                ELSE 'Q4'
            END
        """
        if "fiscal_q" in header
        else """
            CASE
                WHEN EXTRACT(MONTH FROM CAST(date AS DATE)) IN (1,2,3) THEN 'Q1'
                WHEN EXTRACT(MONTH FROM CAST(date AS DATE)) IN (4,5,6) THEN 'Q2'
                WHEN EXTRACT(MONTH FROM CAST(date AS DATE)) IN (7,8,9) THEN 'Q3'
                ELSE 'Q4'
            END
        """
    )
    source_expr = "COALESCE(NULLIF(source, ''), 'earnings_calendar_csv')" if "source" in header else "'earnings_calendar_csv'"
    eps_actual_expr = "TRY_CAST(eps_actual AS DOUBLE)" if "eps_actual" in header else "CAST(NULL AS DOUBLE)"
    eps_estimate_expr = "TRY_CAST(eps_estimate AS DOUBLE)" if "eps_estimate" in header else "CAST(NULL AS DOUBLE)"
    revenue_actual_expr = "TRY_CAST(revenue_actual AS DOUBLE)" if "revenue_actual" in header else "CAST(NULL AS DOUBLE)"
    revenue_estimate_expr = "TRY_CAST(revenue_estimate AS DOUBLE)" if "revenue_estimate" in header else "CAST(NULL AS DOUBLE)"
    
    # Create earnings_events table
    conn.execute(f"""
        CREATE OR REPLACE TABLE earnings_events AS
        SELECT 
            act_symbol as ticker,
            CAST(date AS DATE) as earnings_dt,
            CASE
                WHEN UPPER(timing) = 'BMO' THEN 'before_market_open'
                WHEN UPPER(timing) = 'AMC' THEN 'after_market_close'
                WHEN UPPER(timing) = 'DMH' THEN 'during_market_hours'
                ELSE 'unknown'
            END as timing,
            {source_expr} as source,
            true as confirmed_flag,
            {fiscal_year_expr} as fiscal_year,
            {fiscal_q_expr} as fiscal_q,
            {eps_actual_expr} as eps_actual,
            {eps_estimate_expr} as eps_estimate,
            {revenue_actual_expr} as revenue_actual,
            {revenue_estimate_expr} as revenue_estimate
        FROM read_csv_auto(?)
        WHERE date IS NOT NULL 
        AND act_symbol IS NOT NULL
    """, [str(earnings_csv)])
    
    # Create index for fast lookups
    conn.execute("CREATE INDEX IF NOT EXISTS idx_earnings_ticker_dt ON earnings_events(ticker, earnings_dt)")

    # `timing_source` flags whether each row's timing came from the
    # original calendar feed ('reported') or was filled in by the
    # history-based inference below. Lets us audit accuracy later.
    conn.execute("""
        ALTER TABLE earnings_events
        ADD COLUMN timing_source VARCHAR DEFAULT 'reported'
    """)
    conn.execute("""
        UPDATE earnings_events
        SET timing_source = 'unknown'
        WHERE timing = 'unknown'
    """)

    count = conn.execute("SELECT COUNT(*) FROM earnings_events").fetchone()[0]
    print(f"✅ Created earnings_events table with {count:,} events")

    # Backfill 'unknown' timings using each ticker's reporting history.
    infer_unknown_timings(conn)

    # Show sample data
    sample = conn.execute("""
        SELECT ticker, earnings_dt, timing, fiscal_q
        FROM earnings_events
        ORDER BY earnings_dt DESC
        LIMIT 5
    """).fetchall()

    print("Sample events:")
    for row in sample:
        print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]}")


def infer_unknown_timings(conn: duckdb.DuckDBPyConnection) -> None:
    """Backfill rows where timing = 'unknown' by looking at each
    ticker's recent reporting history.

    Rule cascade (apply in order, stop at first that produces an answer):
      1. The 3 nearest known-timing reports all agree → use that.
      2. Majority of the 5 nearest reports (if 5 are available).
         N=5 is odd so a majority always exists (worst case 3-2).
      3. Majority of the 4 nearest reports (if 4 are available, 5
         wasn't). N=4 can be a 2-2 tie → no majority → fall through to
         Rule 4 instead of leaving the row unknown.
      4. Majority of the 3 nearest reports (2 of 3 agree). Used both
         as a primary path (when fewer than 4 reports exist) and
         as the fallback when Rule 3 hit a 2-2 tie.
    Otherwise leave the row 'unknown'.

    "Nearest" is measured in absolute calendar days from the unknown
    event, so the cascade pulls from both past and future reported
    rows. This is safe because `history_by_ticker` is snapshotted from
    `timing IN ('before_market_open','after_market_close')` BEFORE any
    inferred UPDATE fires (updates are batched at the end of this
    function), so the cascade never reads its own output. Earlier
    versions restricted to strictly-prior reports, which left a
    ticker's earliest unknowns dark even when years of later reported
    history established a consistent timing.

    Only `before_market_open` and `after_market_close` participate in the
    cascade. `during_market_hours` and `unknown` are excluded from the
    history window — they don't predict anything reliable. The fired
    rule is recorded in `timing_source` for auditing.

    Companies almost always report at the same time each quarter (>90%
    by hand-survey), so this fills in the source feed's empty timings
    without sacrificing much accuracy. Rows that resist all four rules
    typically belong to companies in a reporting-time transition or
    with very short histories — those stay 'unknown', which is the
    correct conservative fallback.
    """
    unknowns = conn.execute("""
        SELECT ticker, earnings_dt
        FROM earnings_events
        WHERE timing = 'unknown'
        ORDER BY ticker, earnings_dt
    """).fetchall()
    if not unknowns:
        print("  (no unknown-timing rows to backfill)")
        return

    # Pre-pull all known-timing reports indexed by ticker so the inner
    # loop is a dict lookup, not 1+ SQL query per unknown row.
    known_rows = conn.execute("""
        SELECT ticker, earnings_dt, timing
        FROM earnings_events
        WHERE timing IN ('before_market_open', 'after_market_close')
        ORDER BY ticker, earnings_dt DESC
    """).fetchall()
    history_by_ticker: Dict[str, list] = {}
    for ticker, dt, timing in known_rows:
        history_by_ticker.setdefault(ticker, []).append((dt, timing))

    def majority_of(values: list) -> str | None:
        """Return the value appearing strictly more than half the time,
        or None on a tie. Only meaningful for 3-element lists here."""
        if not values:
            return None
        bmo = sum(1 for v in values if v == 'before_market_open')
        amc = sum(1 for v in values if v == 'after_market_close')
        if bmo > amc:
            return 'before_market_open'
        if amc > bmo:
            return 'after_market_close'
        return None  # tie → no majority

    # Build a batch of updates: (ticker, earnings_dt, inferred_timing, source_tag)
    updates: list[tuple[str, Any, str, str]] = []
    for ticker, unknown_dt in unknowns:
        history = history_by_ticker.get(ticker, [])
        # Rank known reports by absolute calendar distance from the
        # unknown event so the cascade pulls from past AND future
        # reported rows. Safe against inference-on-inference because
        # `history_by_ticker` is a pre-update snapshot of original
        # reported rows only (see function docstring).
        nearest = sorted(history, key=lambda dt_t: abs((dt_t[0] - unknown_dt).days))
        nearest_timings = [t for (_, t) in nearest]
        if len(nearest_timings) < 2:
            continue

        n3 = nearest_timings[:3]
        n4 = nearest_timings[:4]
        n5 = nearest_timings[:5]

        inferred: str | None = None
        source: str | None = None

        # Rule 1 — most recent 3 unanimous.
        if len(n3) == 3 and len(set(n3)) == 1:
            inferred, source = n3[0], 'inferred_all_3'
        # Rule 2 — majority of most recent 5 (if 5 available). With two
        # categories and N=5 (odd), a majority always exists.
        if inferred is None and len(n5) == 5:
            maj = majority_of(n5)
            if maj is not None:
                inferred, source = maj, 'inferred_majority_5'
        # Rule 3 — majority of most recent 4 (if 4 available, 5 wasn't).
        # N=4 can produce a 2-2 tie with no majority — in that case
        # we leave `inferred` as None and fall through to Rule 4.
        if inferred is None and len(n4) == 4:
            maj = majority_of(n4)
            if maj is not None:
                inferred, source = maj, 'inferred_majority_4'
        # Rule 4 — majority of most recent 3 (fallback).
        if inferred is None and len(n3) >= 2:
            maj = majority_of(n3)
            if maj is not None:
                inferred, source = maj, 'inferred_majority_3'

        if inferred is not None:
            updates.append((ticker, unknown_dt, inferred, source))

    if not updates:
        print(f"  ({len(unknowns):,} unknown-timing rows; none satisfied any rule)")
        return

    # Apply updates in one transaction. DuckDB doesn't have batch-UPDATE
    # via executemany on a row-level WHERE, so we INSERT into a temp
    # table and UPDATE FROM it.
    conn.execute("""
        CREATE OR REPLACE TEMP TABLE _timing_infer AS
        SELECT
            NULL::VARCHAR AS ticker,
            NULL::DATE    AS earnings_dt,
            NULL::VARCHAR AS new_timing,
            NULL::VARCHAR AS new_source
        WHERE 1 = 0
    """)
    conn.executemany(
        "INSERT INTO _timing_infer VALUES (?, ?, ?, ?)",
        updates,
    )
    conn.execute("""
        UPDATE earnings_events e
        SET timing = i.new_timing,
            timing_source = i.new_source
        FROM _timing_infer i
        WHERE e.ticker = i.ticker
          AND e.earnings_dt = i.earnings_dt
    """)
    conn.execute("DROP TABLE _timing_infer")

    # Summary: how many rows landed in each rule.
    breakdown = conn.execute("""
        SELECT timing_source, COUNT(*)
        FROM earnings_events
        WHERE timing_source LIKE 'inferred%'
        GROUP BY timing_source
        ORDER BY timing_source
    """).fetchall()
    still_unknown = conn.execute("""
        SELECT COUNT(*) FROM earnings_events WHERE timing = 'unknown'
    """).fetchone()[0]
    print(
        f"  inferred {len(updates):,} of {len(unknowns):,} unknown-timing rows; "
        f"{still_unknown:,} remain 'unknown'"
    )
    for source, n in breakdown:
        print(f"    {source}: {n:,}")

def create_duckdb_views(conn: duckdb.DuckDBPyConnection, data_dir: Path):
    """Create DuckDB views for options_chain and volatility_history."""

    print("🦆 Creating DuckDB views...")

    policy_path = Path(__file__).resolve().parent.parent / "config" / "option_quote_quality.json"
    policy = json.loads(policy_path.read_text())
    min_bid = float(policy["min_bid"])
    min_ask = float(policy["min_ask"])
    min_iv = float(policy["min_iv"])
    max_iv = float(policy["max_iv"])
    min_dte = int(policy["min_dte"])
    max_dte = int(policy["max_dte"])
    max_leg_spread = float(policy["max_leg_relative_spread"])
    max_straddle_spread = float(policy["max_straddle_relative_spread"])
    max_atm_delta_distance = float(policy["max_atm_delta_distance"])
    max_quote_time_skew_seconds = int(policy["max_quote_time_skew_seconds"])
    min_option_volume = int(policy["min_option_volume_when_available"])
    min_open_interest = int(policy["min_open_interest_when_available"])
    timestamp_precision = str(policy["timestamp_precision"])
    market_data_mode = str(policy["market_data_mode"])

    # Options chain view (dedup on ticker/date/expiry/strike/call_put since parquet files can overlap)
    options_pattern = str(data_dir / "parquet" / "options_chain" / "*" / "*" / "*.parquet")
    parquet_scan = (
        f"read_parquet('{options_pattern}', "
        "hive_partitioning=true, union_by_name=true)"
    )
    parquet_columns = {
        str(row[0])
        for row in conn.execute(f"DESCRIBE SELECT * FROM {parquet_scan}").fetchall()
    }
    quote_timestamp_expr = (
        "TRY_CAST(quote_timestamp AS TIMESTAMP)"
        if "quote_timestamp" in parquet_columns
        else "NULL::TIMESTAMP"
    )
    option_volume_expr = (
        "TRY_CAST(option_volume AS BIGINT)"
        if "option_volume" in parquet_columns
        else "NULL::BIGINT"
    )
    open_interest_expr = (
        "TRY_CAST(open_interest AS BIGINT)"
        if "open_interest" in parquet_columns
        else "NULL::BIGINT"
    )
    conn.execute(f"""
        CREATE OR REPLACE VIEW v_options_chain AS
        WITH normalized AS (
            SELECT
                act_symbol AS ticker,
                CAST(date AS DATE) AS as_of_date,
                CAST(expiration AS DATE) AS expiry_date,
                strike,
                CASE
                    WHEN UPPER(call_put) IN ('C', 'CALL') THEN 'C'
                    WHEN UPPER(call_put) IN ('P', 'PUT') THEN 'P'
                    ELSE NULL
                END AS call_put,
                bid,
                ask,
                (bid + ask) / 2.0 AS mid_price,
                (ask - bid) / NULLIF((ask + bid) / 2.0, 0) AS bid_ask_spread_pct,
                DATE_DIFF('day', CAST(date AS DATE), CAST(expiration AS DATE)) AS dte,
                delta,
                gamma,
                theta,
                vega,
                vol AS iv,
                {quote_timestamp_expr} AS source_quote_timestamp,
                {option_volume_expr} AS option_volume,
                {open_interest_expr} AS open_interest,
                CASE
                    WHEN {quote_timestamp_expr} IS NULL THEN '{timestamp_precision}'
                    ELSE 'timestamp'
                END::VARCHAR AS quote_timestamp_precision,
                '{market_data_mode}'::VARCHAR AS market_data_mode
            FROM {parquet_scan}
        )
        SELECT *
        FROM normalized
        WHERE ticker IS NOT NULL
          AND strike > 0
          AND call_put IS NOT NULL
          AND bid >= {min_bid}
          AND ask >= {min_ask}
          AND ask >= bid
          AND iv IS NOT NULL
          AND iv >= {min_iv}
          AND iv <= {max_iv}
          AND dte BETWEEN {min_dte} AND {max_dte}
          AND bid_ask_spread_pct <= {max_leg_spread}
          AND delta IS NOT NULL
          AND (
              (call_put = 'C' AND delta BETWEEN 0 AND 1)
              OR (call_put = 'P' AND delta BETWEEN -1 AND 0)
          )
          AND (
              source_quote_timestamp IS NULL
              OR CAST(source_quote_timestamp AS DATE) = as_of_date
          )
          AND (option_volume IS NULL OR option_volume >= 0)
          AND (open_interest IS NULL OR open_interest >= 0)
          AND (
              (option_volume IS NULL AND open_interest IS NULL)
              OR COALESCE(option_volume, 0) >= {min_option_volume}
              OR COALESCE(open_interest, 0) >= {min_open_interest}
          )
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY ticker, as_of_date, expiry_date, strike, call_put
            ORDER BY bid_ask_spread_pct, bid + ask DESC
        ) = 1
    """)
    
    # v_ohlcv — daily stock prices. Needed by build_frontend_data.screener_extras
    # to compute hist_move_avg_4q (close-to-close moves over the last 4 earnings).
    ohlcv_root = data_dir / "parquet" / "ohlcv"
    if ohlcv_root.exists() and any(ohlcv_root.glob("year=*/month=*/*.parquet")):
        ohlcv_glob = str(ohlcv_root / "year=*" / "month=*" / "*.parquet")
        conn.execute(f"""
            CREATE OR REPLACE VIEW v_ohlcv AS
            SELECT
                CAST(date AS DATE) AS date,
                act_symbol,
                CAST(open  AS DOUBLE) AS open,
                CAST(high  AS DOUBLE) AS high,
                CAST(low   AS DOUBLE) AS low,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS BIGINT) AS volume
            FROM read_parquet('{ohlcv_glob}')
        """)
        print("✅ Created v_ohlcv view")
    else:
        conn.execute("""
            CREATE OR REPLACE VIEW v_ohlcv AS
            SELECT NULL::DATE AS date, NULL::VARCHAR AS act_symbol,
                   NULL::DOUBLE AS open, NULL::DOUBLE AS high,
                   NULL::DOUBLE AS low, NULL::DOUBLE AS close,
                   NULL::BIGINT AS volume
            WHERE 1=0
        """)

    # v_vix — authoritative daily VIX close from CBOE. Used as a market-regime feature.
    vix_path = data_dir / "parquet" / "vix" / "vix.parquet"
    if vix_path.exists():
        conn.execute(f"""
            CREATE OR REPLACE VIEW v_vix AS
            SELECT CAST(date AS DATE) AS date, CAST(vix_close AS DOUBLE) AS vix_close
            FROM read_parquet('{vix_path}')
        """)
    else:
        conn.execute("""
            CREATE OR REPLACE VIEW v_vix AS
            SELECT NULL::DATE AS date, NULL::DOUBLE AS vix_close
            WHERE 1=0
        """)

    # v_volhist — per-ticker per-day HV/IV snapshots. Used downstream to
    # compute IV Rank and vol-momentum stats on the ticker detail page.
    # Empty stub when parquet missing so LEFT JOINs still resolve.
    vol_root = data_dir / "parquet" / "volatility_history"
    if vol_root.exists() and any(vol_root.glob("year=*/month=*/*.parquet")):
        vol_glob = str(vol_root / "year=*" / "month=*" / "*.parquet")
        conn.execute(f"""
            CREATE OR REPLACE VIEW v_volhist AS
            SELECT
                CAST(date AS DATE) AS date,
                act_symbol,
                CAST(hv_current   AS DOUBLE) AS hv_current,
                CAST(hv_week_ago  AS DOUBLE) AS hv_week_ago,
                CAST(hv_month_ago AS DOUBLE) AS hv_month_ago,
                CAST(hv_year_high AS DOUBLE) AS hv_year_high,
                CAST(hv_year_low  AS DOUBLE) AS hv_year_low,
                CAST(iv_current   AS DOUBLE) AS iv_current,
                CAST(iv_week_ago  AS DOUBLE) AS iv_week_ago,
                CAST(iv_month_ago AS DOUBLE) AS iv_month_ago,
                CAST(iv_year_high AS DOUBLE) AS iv_year_high,
                CAST(iv_year_low  AS DOUBLE) AS iv_year_low
            FROM read_parquet('{vol_glob}')
        """)
        print("✅ Created v_volhist view")
    else:
        conn.execute("""
            CREATE OR REPLACE VIEW v_volhist AS
            SELECT NULL::DATE AS date, NULL::VARCHAR AS act_symbol,
                   NULL::DOUBLE AS hv_current, NULL::DOUBLE AS hv_week_ago,
                   NULL::DOUBLE AS hv_month_ago, NULL::DOUBLE AS hv_year_high,
                   NULL::DOUBLE AS hv_year_low,
                   NULL::DOUBLE AS iv_current, NULL::DOUBLE AS iv_week_ago,
                   NULL::DOUBLE AS iv_month_ago, NULL::DOUBLE AS iv_year_high,
                   NULL::DOUBLE AS iv_year_low
            WHERE 1=0
        """)

    # ATM helper view - finds ATM strikes for each ticker/date/expiry combination
    conn.execute("""
        CREATE OR REPLACE VIEW v_atm_strikes AS
        WITH spot_estimates AS (
            -- Estimate spot price from options data (use call delta closest to 0.5)
            SELECT 
                ticker,
                as_of_date,
                expiry_date,
                strike as estimated_spot,
                ROW_NUMBER() OVER (PARTITION BY ticker, as_of_date, expiry_date ORDER BY ABS(delta - 0.5)) as rn
            FROM v_options_chain
            WHERE call_put = 'C'
            AND delta IS NOT NULL
            AND delta BETWEEN 0.3 AND 0.7
        ),
        atm_candidates AS (
            SELECT 
                o.ticker,
                o.as_of_date,
                o.expiry_date,
                s.estimated_spot,
                o.strike,
                ABS(o.strike - s.estimated_spot) as strike_distance,
                ROW_NUMBER() OVER (PARTITION BY o.ticker, o.as_of_date, o.expiry_date ORDER BY ABS(o.strike - s.estimated_spot)) as rn
            FROM v_options_chain o
            JOIN spot_estimates s ON o.ticker = s.ticker 
                AND o.as_of_date = s.as_of_date 
                AND o.expiry_date = s.expiry_date
                AND s.rn = 1
        )
        SELECT 
            ticker,
            as_of_date,
            expiry_date,
            estimated_spot,
            strike as atm_strike,
            strike_distance
        FROM atm_candidates
        WHERE rn = 1
    """)

    # One decision-eligible ATM pair per symbol, observation date, and expiry.
    # This is shared by current and historical frontend evidence so no displayed
    # straddle can bypass the production leg, pair, spread, delta, or DTE gates.
    conn.execute(f"""
        CREATE OR REPLACE VIEW v_eligible_straddles AS
        WITH pairs AS (
            SELECT
                c.ticker,
                c.as_of_date,
                c.expiry_date,
                c.dte,
                c.strike,
                c.bid AS call_bid,
                c.ask AS call_ask,
                c.mid_price AS call_mid,
                c.iv AS call_iv,
                c.delta AS call_delta,
                c.gamma AS call_gamma,
                c.vega AS call_vega,
                c.theta AS call_theta,
                c.source_quote_timestamp AS call_quote_timestamp,
                p.bid AS put_bid,
                p.ask AS put_ask,
                p.mid_price AS put_mid,
                p.iv AS put_iv,
                p.delta AS put_delta,
                p.vega AS put_vega,
                p.source_quote_timestamp AS put_quote_timestamp,
                c.mid_price + p.mid_price AS straddle_mid,
                (c.ask + p.ask - c.bid - p.bid)
                    / NULLIF(c.mid_price + p.mid_price, 0) AS straddle_relative_spread,
                ABS(c.delta - 0.5) + ABS(p.delta + 0.5) AS atm_delta_distance
            FROM v_options_chain c
            JOIN v_options_chain p
              ON p.ticker = c.ticker
             AND p.as_of_date = c.as_of_date
             AND p.expiry_date = c.expiry_date
             AND p.strike = c.strike
             AND c.call_put = 'C'
             AND p.call_put = 'P'
        ), eligible AS (
            SELECT *
            FROM pairs
            WHERE straddle_mid > 0
              AND straddle_relative_spread <= {max_straddle_spread}
              AND atm_delta_distance <= {max_atm_delta_distance}
              AND (call_quote_timestamp IS NULL) = (put_quote_timestamp IS NULL)
              AND (
                  call_quote_timestamp IS NULL
                  OR ABS(EPOCH(call_quote_timestamp) - EPOCH(put_quote_timestamp))
                      <= {max_quote_time_skew_seconds}
              )
        )
        SELECT
            *,
            strike AS estimated_spot,
            strike AS atm_strike,
            straddle_mid / NULLIF(strike, 0) AS straddle_pct,
            (call_iv + put_iv) / 2.0 AS atm_iv,
            'decision_eligible_eod'::VARCHAR AS quote_quality_status,
            ROW_NUMBER() OVER (
                PARTITION BY ticker, as_of_date, expiry_date
                ORDER BY atm_delta_distance, straddle_relative_spread, strike
            ) AS pair_rank
        FROM eligible
        QUALIFY pair_rank = 1
    """)
    
    print("✅ Created v_options_chain, v_atm_strikes, and v_eligible_straddles views")
    
    # Test the views
    options_count = conn.execute("SELECT COUNT(*) FROM v_options_chain").fetchone()[0]
    atm_count = conn.execute("SELECT COUNT(*) FROM v_atm_strikes").fetchone()[0]
    straddle_count = conn.execute("SELECT COUNT(*) FROM v_eligible_straddles").fetchone()[0]
    
    print(f"📊 v_options_chain: {options_count:,} records")
    print(f"📊 v_atm_strikes: {atm_count:,} records")
    print(f"📊 v_eligible_straddles: {straddle_count:,} records")

def main():
    # Setup paths
    repo_root = Path(__file__).parent.parent
    data_dir = repo_root / "data"
    earnings_csv = data_dir / "earnings_calendar.csv"
    env_path = repo_root / "config" / ".env.local"
    
    # Load environment variables
    load_env_file(env_path)
    
    print("🚀 Building earnings events and DuckDB views...")
    
    # Check required files
    if not earnings_csv.exists():
        print(f"❌ Earnings calendar not found: {earnings_csv}")
        sys.exit(1)
    
    # Connect to DuckDB (use in-memory to avoid lock conflicts)
    conn = duckdb.connect()
    
    try:
        # Build earnings events table
        build_earnings_events_table(conn, earnings_csv)
        
        # Create DuckDB views
        create_duckdb_views(conn, data_dir)
        
        print("✅ Successfully built earnings events and views!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
