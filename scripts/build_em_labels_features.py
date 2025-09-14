#!/usr/bin/env python3
"""
Build expected move labels and features for Quantiv ML pipeline.

Creates em_labels, em_features, and em_training tables/views in DuckDB
for training expected move prediction models.

Usage:
  python scripts/build_em_labels_features.py [--duckdb-path /srv/quantiv-data/quantiv.duckdb]
"""

import os
import sys
from pathlib import Path
import argparse
import duckdb
from datetime import datetime

def create_em_labels_table(conn):
    """Create realized move labels table using IV proxy initially."""
    print("[labels] Creating em_labels table...")
    
    # For now, use IV shock as proxy for realized move
    # Later replace with actual price-based realized moves
    labels_sql = """
    CREATE OR REPLACE TABLE em_labels AS
    WITH pre AS (
      SELECT 
        v.act_symbol, 
        e.earnings_date,
        AVG(v.iv_current) AS iv_pre,
        COUNT(*) as pre_days
      FROM v_volhist_norm v
      JOIN v_earnings e USING (act_symbol)
      WHERE v.trade_date BETWEEN CAST(e.earnings_date AS DATE) - INTERVAL '10' DAY
                             AND CAST(e.earnings_date AS DATE) - INTERVAL '1' DAY
      GROUP BY 1, 2
      HAVING COUNT(*) >= 3  -- Require at least 3 days of pre-earnings data
    ),
    post AS (
      SELECT 
        v.act_symbol, 
        e.earnings_date,
        AVG(v.iv_current) AS iv_post,
        COUNT(*) as post_days
      FROM v_volhist_norm v
      JOIN v_earnings e USING (act_symbol)
      WHERE v.trade_date BETWEEN CAST(e.earnings_date AS DATE) + INTERVAL '1' DAY
                             AND CAST(e.earnings_date AS DATE) + INTERVAL '5' DAY
      GROUP BY 1, 2
      HAVING COUNT(*) >= 2  -- Require at least 2 days of post-earnings data
    )
    SELECT
      COALESCE(pre.act_symbol, post.act_symbol) AS act_symbol,
      COALESCE(pre.earnings_date, post.earnings_date) AS earnings_date,
      pre.iv_pre,
      post.iv_post,
      pre.pre_days,
      post.post_days,
      CASE 
        WHEN pre.iv_pre IS NOT NULL AND post.iv_post IS NOT NULL AND pre.iv_pre > 0
        THEN ABS(post.iv_post - pre.iv_pre) / pre.iv_pre
        ELSE NULL 
      END AS proxy_realized_move,
      CASE
        WHEN pre.iv_pre IS NOT NULL AND post.iv_post IS NOT NULL AND pre.iv_pre > 0
        THEN (post.iv_post - pre.iv_pre) / pre.iv_pre
        ELSE NULL
      END AS signed_iv_change,
      now() AS created_at
    FROM pre 
    FULL OUTER JOIN post USING (act_symbol, earnings_date)
    WHERE COALESCE(pre.act_symbol, post.act_symbol) IS NOT NULL
      AND COALESCE(pre.earnings_date, post.earnings_date) IS NOT NULL;
    """
    
    try:
        conn.execute(labels_sql)
        print("[labels] ✓ Created em_labels table")
        
        # Show stats
        result = conn.execute("SELECT COUNT(*) FROM em_labels").fetchone()
        total_count = result[0] if result else 0
        
        result = conn.execute("SELECT COUNT(*) FROM em_labels WHERE proxy_realized_move IS NOT NULL").fetchone()
        valid_count = result[0] if result else 0
        
        print(f"[labels] Total earnings events: {total_count}")
        print(f"[labels] Valid labels (with IV data): {valid_count}")
        
    except Exception as e:
        print(f"[labels] ⚠ Failed to create em_labels: {e}")

def create_em_features_table(conn):
    """Create feature table with pre-earnings snapshots."""
    print("[features] Creating em_features table...")
    
    features_sql = """
    CREATE OR REPLACE TABLE em_features AS
    WITH vol_features AS (
      SELECT
        e.act_symbol,
        e.earnings_date,
        -- Get the most recent volatility data before earnings (T-1 or T-2)
        FIRST_VALUE(v.iv_current) OVER (
          PARTITION BY e.act_symbol, e.earnings_date 
          ORDER BY v.trade_date DESC
        ) AS iv_t1,
        FIRST_VALUE(v.hv_current) OVER (
          PARTITION BY e.act_symbol, e.earnings_date 
          ORDER BY v.trade_date DESC
        ) AS hv_t1,
        FIRST_VALUE(v.iv_week_ago) OVER (
          PARTITION BY e.act_symbol, e.earnings_date 
          ORDER BY v.trade_date DESC
        ) AS iv_week_ago,
        FIRST_VALUE(v.iv_month_ago) OVER (
          PARTITION BY e.act_symbol, e.earnings_date 
          ORDER BY v.trade_date DESC
        ) AS iv_month_ago,
        FIRST_VALUE(v.iv_year_high) OVER (
          PARTITION BY e.act_symbol, e.earnings_date 
          ORDER BY v.trade_date DESC
        ) AS iv_year_high,
        FIRST_VALUE(v.iv_year_low) OVER (
          PARTITION BY e.act_symbol, e.earnings_date 
          ORDER BY v.trade_date DESC
        ) AS iv_year_low,
        FIRST_VALUE(v.trade_date) OVER (
          PARTITION BY e.act_symbol, e.earnings_date 
          ORDER BY v.trade_date DESC
        ) AS vol_data_date,
        ROW_NUMBER() OVER (
          PARTITION BY e.act_symbol, e.earnings_date 
          ORDER BY v.trade_date DESC
        ) AS rn
      FROM v_earnings e
      LEFT JOIN v_volhist_norm v 
        ON e.act_symbol = v.act_symbol
        AND v.trade_date <= CAST(e.earnings_date AS DATE) - INTERVAL '1' DAY
        AND v.trade_date >= CAST(e.earnings_date AS DATE) - INTERVAL '10' DAY
    ),
    vol_clean AS (
      SELECT * FROM vol_features WHERE rn = 1
    ),
    opt_features AS (
      -- Aggregate options data from T-1 (or closest available date)
      WITH options_pre AS (
        SELECT 
          o.*,
          e.earnings_date,
          ABS(DATE_DIFF('day', CAST(o.trade_date AS DATE), CAST(e.earnings_date AS DATE))) as days_to_earnings
        FROM v_options_norm o
        JOIN v_earnings e ON e.act_symbol = o.act_symbol
        WHERE o.trade_date <= CAST(e.earnings_date AS DATE) - INTERVAL '1' DAY
          AND o.trade_date >= CAST(e.earnings_date AS DATE) - INTERVAL '5' DAY
      ),
      options_closest AS (
        SELECT *,
          ROW_NUMBER() OVER (
            PARTITION BY act_symbol, earnings_date 
            ORDER BY days_to_earnings ASC
          ) as date_rank
        FROM options_pre
      ),
      atm_strikes AS (
        -- Estimate ATM strike as median strike for each symbol/date
        SELECT 
          act_symbol, 
          earnings_date, 
          trade_date,
          PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY strike) AS atm_strike_est
        FROM options_closest 
        WHERE date_rank = 1
        GROUP BY 1, 2, 3
      )
      SELECT
        o.act_symbol,
        o.earnings_date,
        o.trade_date as options_data_date,
        -- Basic option metrics
        AVG(o.vol) AS avg_iv_t1,
        AVG(ABS(o.delta)) AS avg_abs_delta_t1,
        AVG(o.gamma) AS avg_gamma_t1,
        AVG(o.vega) AS avg_vega_t1,
        AVG(o.theta) AS avg_theta_t1,
        COUNT(*) AS total_contracts,
        COUNT(CASE WHEN o.call_put = 'C' THEN 1 END) AS call_count,
        COUNT(CASE WHEN o.call_put = 'P' THEN 1 END) AS put_count,
        -- ATM-focused metrics
        AVG(CASE WHEN ABS(o.strike - a.atm_strike_est) <= 5.0 THEN o.vol END) AS atm_iv_t1,
        AVG(CASE WHEN ABS(o.strike - a.atm_strike_est) <= 5.0 THEN o.gamma END) AS atm_gamma_t1,
        AVG(CASE WHEN ABS(o.strike - a.atm_strike_est) <= 5.0 THEN o.vega END) AS atm_vega_t1,
        -- Skew metrics (call IV - put IV for similar strikes)
        AVG(CASE WHEN o.call_put = 'C' AND ABS(o.strike - a.atm_strike_est) <= 10.0 THEN o.vol END) -
        AVG(CASE WHEN o.call_put = 'P' AND ABS(o.strike - a.atm_strike_est) <= 10.0 THEN o.vol END) AS iv_skew,
        -- Strike range
        MIN(o.strike) AS min_strike,
        MAX(o.strike) AS max_strike,
        a.atm_strike_est
      FROM options_closest o
      JOIN atm_strikes a USING (act_symbol, earnings_date, trade_date)
      WHERE o.date_rank = 1
      GROUP BY 1, 2, 3, a.atm_strike_est
    )
    SELECT
      COALESCE(v.act_symbol, o.act_symbol) AS act_symbol,
      COALESCE(v.earnings_date, o.earnings_date) AS earnings_date,
      -- Volatility features
      v.iv_t1,
      v.hv_t1,
      v.iv_week_ago,
      v.iv_month_ago,
      v.iv_year_high,
      v.iv_year_low,
      v.vol_data_date,
      -- Derived volatility features
      CASE WHEN v.iv_t1 IS NOT NULL AND v.hv_t1 IS NOT NULL 
           THEN v.iv_t1 - v.hv_t1 ELSE NULL END AS iv_hv_spread,
      CASE WHEN v.iv_t1 IS NOT NULL AND v.iv_year_high IS NOT NULL AND v.iv_year_high > 0
           THEN v.iv_t1 / v.iv_year_high ELSE NULL END AS iv_percentile_est,
      -- Options features
      o.avg_iv_t1,
      o.avg_abs_delta_t1,
      o.avg_gamma_t1,
      o.avg_vega_t1,
      o.avg_theta_t1,
      o.total_contracts,
      o.call_count,
      o.put_count,
      o.atm_iv_t1,
      o.atm_gamma_t1,
      o.atm_vega_t1,
      o.iv_skew,
      o.min_strike,
      o.max_strike,
      o.atm_strike_est,
      o.options_data_date,
      -- Derived options features
      CASE WHEN o.call_count > 0 AND o.put_count > 0 
           THEN CAST(o.call_count AS DOUBLE) / (o.call_count + o.put_count) 
           ELSE NULL END AS call_put_ratio,
      now() AS created_at
    FROM vol_clean v
    FULL OUTER JOIN opt_features o USING (act_symbol, earnings_date)
    WHERE COALESCE(v.act_symbol, o.act_symbol) IS NOT NULL
      AND COALESCE(v.earnings_date, o.earnings_date) IS NOT NULL;
    """
    
    try:
        conn.execute(features_sql)
        print("[features] ✓ Created em_features table")
        
        # Show stats
        result = conn.execute("SELECT COUNT(*) FROM em_features").fetchone()
        total_count = result[0] if result else 0
        
        result = conn.execute("SELECT COUNT(*) FROM em_features WHERE iv_t1 IS NOT NULL").fetchone()
        vol_count = result[0] if result else 0
        
        result = conn.execute("SELECT COUNT(*) FROM em_features WHERE avg_iv_t1 IS NOT NULL").fetchone()
        opt_count = result[0] if result else 0
        
        print(f"[features] Total earnings events: {total_count}")
        print(f"[features] With volatility features: {vol_count}")
        print(f"[features] With options features: {opt_count}")
        
    except Exception as e:
        print(f"[features] ⚠ Failed to create em_features: {e}")

def create_training_view(conn):
    """Create training set view joining features and labels."""
    print("[training] Creating em_training view...")
    
    training_sql = """
    CREATE OR REPLACE VIEW em_training AS
    SELECT
      f.*,
      l.proxy_realized_move AS y,
      l.signed_iv_change,
      l.iv_pre,
      l.iv_post,
      l.pre_days,
      l.post_days
    FROM em_features f
    JOIN em_labels l USING (act_symbol, earnings_date)
    WHERE l.proxy_realized_move IS NOT NULL
      AND f.iv_t1 IS NOT NULL  -- Require basic volatility data
    ORDER BY f.earnings_date DESC;
    """
    
    try:
        conn.execute(training_sql)
        print("[training] ✓ Created em_training view")
        
        # Show training set stats
        result = conn.execute("SELECT COUNT(*) FROM em_training").fetchone()
        training_count = result[0] if result else 0
        
        result = conn.execute("SELECT COUNT(DISTINCT act_symbol) FROM em_training").fetchone()
        symbol_count = result[0] if result else 0
        
        result = conn.execute("SELECT MIN(earnings_date), MAX(earnings_date) FROM em_training").fetchone()
        if result and result[0] and result[1]:
            min_date, max_date = result
            print(f"[training] Training samples: {training_count}")
            print(f"[training] Unique symbols: {symbol_count}")
            print(f"[training] Date range: {min_date} to {max_date}")
        else:
            print(f"[training] Training samples: {training_count}")
            print(f"[training] Unique symbols: {symbol_count}")
        
    except Exception as e:
        print(f"[training] ⚠ Failed to create em_training view: {e}")

def create_feature_summary_views(conn):
    """Create summary views for feature analysis."""
    print("[summary] Creating feature summary views...")
    
    summary_sql = """
    -- Feature completeness summary
    CREATE OR REPLACE VIEW v_feature_completeness AS
    SELECT
      'iv_t1' as feature_name,
      COUNT(*) as total_rows,
      COUNT(iv_t1) as non_null_count,
      ROUND(COUNT(iv_t1) * 100.0 / COUNT(*), 2) as completeness_pct
    FROM em_features
    UNION ALL
    SELECT 'hv_t1', COUNT(*), COUNT(hv_t1), ROUND(COUNT(hv_t1) * 100.0 / COUNT(*), 2) FROM em_features
    UNION ALL
    SELECT 'avg_iv_t1', COUNT(*), COUNT(avg_iv_t1), ROUND(COUNT(avg_iv_t1) * 100.0 / COUNT(*), 2) FROM em_features
    UNION ALL
    SELECT 'atm_iv_t1', COUNT(*), COUNT(atm_iv_t1), ROUND(COUNT(atm_iv_t1) * 100.0 / COUNT(*), 2) FROM em_features
    UNION ALL
    SELECT 'iv_skew', COUNT(*), COUNT(iv_skew), ROUND(COUNT(iv_skew) * 100.0 / COUNT(*), 2) FROM em_features
    ORDER BY completeness_pct DESC;
    
    -- Training set by year
    CREATE OR REPLACE VIEW v_training_by_year AS
    SELECT
      EXTRACT(YEAR FROM earnings_date) as year,
      COUNT(*) as sample_count,
      COUNT(DISTINCT act_symbol) as unique_symbols,
      ROUND(AVG(y), 4) as avg_realized_move,
      ROUND(STDDEV(y), 4) as std_realized_move
    FROM em_training
    GROUP BY 1
    ORDER BY 1;
    """
    
    try:
        conn.execute(summary_sql)
        print("[summary] ✓ Created feature summary views")
    except Exception as e:
        print(f"[summary] ⚠ Failed to create summary views: {e}")

def run_validation_checks(conn):
    """Run validation checks on the created tables and views."""
    print("[validation] Running validation checks...")
    
    checks = [
        ("em_labels row count", "SELECT COUNT(*) FROM em_labels"),
        ("em_features row count", "SELECT COUNT(*) FROM em_features"),
        ("em_training row count", "SELECT COUNT(*) FROM em_training"),
        ("Labels with valid proxy", "SELECT COUNT(*) FROM em_labels WHERE proxy_realized_move IS NOT NULL"),
        ("Features with IV data", "SELECT COUNT(*) FROM em_features WHERE iv_t1 IS NOT NULL"),
        ("Features with options data", "SELECT COUNT(*) FROM em_features WHERE avg_iv_t1 IS NOT NULL"),
    ]
    
    for check_name, query in checks:
        try:
            result = conn.execute(query).fetchone()
            count = result[0] if result else 0
            print(f"  {check_name:25}: {count:,}")
        except Exception as e:
            print(f"  {check_name:25}: ERROR - {e}")
    
    # Show feature completeness
    print("\nFeature completeness:")
    try:
        result = conn.execute("SELECT * FROM v_feature_completeness").fetchall()
        for feature, total, non_null, pct in result:
            print(f"  {feature:15}: {pct:5.1f}% ({non_null:,}/{total:,})")
    except Exception as e:
        print(f"  Feature completeness: ERROR - {e}")

def main():
    parser = argparse.ArgumentParser(description="Build EM labels and features for Quantiv")
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=Path("/srv/quantiv-data/quantiv.duckdb"),
        help="Path to DuckDB file"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local data/ directory"
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip validation checks"
    )
    
    args = parser.parse_args()
    
    if args.local:
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        duckdb_path = project_root / "data" / "quantiv.duckdb"
    else:
        duckdb_path = args.duckdb_path
    
    if not duckdb_path.exists():
        print(f"[error] DuckDB file not found: {duckdb_path}")
        print("Run setup_duckdb_views.py and setup_earnings_calendar.py first.")
        sys.exit(1)
    
    print(f"[setup] Building EM labels and features")
    print(f"[setup] DuckDB: {duckdb_path}")
    print(f"[setup] Timestamp: {datetime.now().isoformat()}")
    
    try:
        conn = duckdb.connect(str(duckdb_path))
        
        # Create labels, features, and training view
        create_em_labels_table(conn)
        create_em_features_table(conn)
        create_training_view(conn)
        create_feature_summary_views(conn)
        
        # Run validation unless skipped
        if not args.skip_validation:
            run_validation_checks(conn)
        
        conn.close()
        print("[success] EM labels and features created successfully")
        
    except Exception as e:
        print(f"[error] Failed to build EM labels and features: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
