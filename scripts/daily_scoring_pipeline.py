#!/usr/bin/env python3
"""
Daily scoring pipeline for Quantiv expected move predictions.

This script should be run daily (via cron) to:
1. Refresh features for upcoming earnings (next 14 days)
2. Load the latest trained model
3. Generate predictions for upcoming earnings
4. Save results to DuckDB and Parquet outputs

Usage:
  python scripts/daily_scoring_pipeline.py [--duckdb-path /srv/quantiv-data/quantiv.duckdb]
"""

import os
import sys
from pathlib import Path
import argparse
import duckdb
import pandas as pd
import numpy as np
import pickle
from datetime import datetime, timedelta

def refresh_upcoming_features(conn):
    """Refresh features for upcoming earnings (next 14 days)."""
    print("[features] Refreshing features for upcoming earnings...")
    
    # Create table for upcoming earnings features
    upcoming_sql = """
    CREATE OR REPLACE TABLE em_features_upcoming AS
    WITH upcoming_earnings AS (
      SELECT * FROM v_earnings
      WHERE earnings_date BETWEEN current_date() AND current_date() + INTERVAL '14' DAY
    ),
    vol_features AS (
      SELECT
        e.act_symbol,
        e.earnings_date,
        -- Get the most recent volatility data
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
      FROM upcoming_earnings e
      LEFT JOIN v_volhist_norm v 
        ON e.act_symbol = v.act_symbol
        AND v.trade_date <= current_date()
        AND v.trade_date >= current_date() - INTERVAL '10' DAY
    ),
    vol_clean AS (
      SELECT * FROM vol_features WHERE rn = 1
    ),
    opt_features AS (
      WITH options_recent AS (
        SELECT 
          o.*,
          e.earnings_date,
          ABS(EXTRACT('day' FROM current_date() - o.trade_date)) as days_ago
        FROM v_options_norm o
        JOIN upcoming_earnings e ON e.act_symbol = o.act_symbol
        WHERE o.trade_date <= current_date()
          AND o.trade_date >= current_date() - INTERVAL '5' DAY
      ),
      options_latest AS (
        SELECT *,
          ROW_NUMBER() OVER (
            PARTITION BY act_symbol, earnings_date 
            ORDER BY days_ago ASC
          ) as date_rank
        FROM options_recent
      ),
      atm_strikes AS (
        SELECT 
          act_symbol, 
          earnings_date, 
          trade_date,
          PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY strike) AS atm_strike_est
        FROM options_latest 
        WHERE date_rank = 1
        GROUP BY 1, 2, 3
      )
      SELECT
        o.act_symbol,
        o.earnings_date,
        o.trade_date as options_data_date,
        AVG(o.vol) AS avg_iv_t1,
        AVG(ABS(o.delta)) AS avg_abs_delta_t1,
        AVG(o.gamma) AS avg_gamma_t1,
        AVG(o.vega) AS avg_vega_t1,
        AVG(o.theta) AS avg_theta_t1,
        COUNT(*) AS total_contracts,
        COUNT(CASE WHEN o.call_put = 'C' THEN 1 END) AS call_count,
        COUNT(CASE WHEN o.call_put = 'P' THEN 1 END) AS put_count,
        AVG(CASE WHEN ABS(o.strike - a.atm_strike_est) <= 5.0 THEN o.vol END) AS atm_iv_t1,
        AVG(CASE WHEN ABS(o.strike - a.atm_strike_est) <= 5.0 THEN o.gamma END) AS atm_gamma_t1,
        AVG(CASE WHEN ABS(o.strike - a.atm_strike_est) <= 5.0 THEN o.vega END) AS atm_vega_t1,
        AVG(CASE WHEN o.call_put = 'C' AND ABS(o.strike - a.atm_strike_est) <= 10.0 THEN o.vol END) -
        AVG(CASE WHEN o.call_put = 'P' AND ABS(o.strike - a.atm_strike_est) <= 10.0 THEN o.vol END) AS iv_skew,
        MIN(o.strike) AS min_strike,
        MAX(o.strike) AS max_strike,
        a.atm_strike_est
      FROM options_latest o
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
      -- Additional derived features for scoring
      CASE WHEN v.iv_t1 IS NOT NULL AND v.hv_t1 IS NOT NULL AND v.hv_t1 > 0
           THEN v.iv_t1 / v.hv_t1 ELSE NULL END as iv_hv_ratio,
      CASE WHEN o.atm_iv_t1 IS NOT NULL AND v.iv_t1 IS NOT NULL AND v.iv_t1 > 0
           THEN o.atm_iv_t1 / v.iv_t1 ELSE NULL END as atm_iv_ratio,
      current_timestamp() AS created_at
    FROM vol_clean v
    FULL OUTER JOIN opt_features o USING (act_symbol, earnings_date)
    WHERE COALESCE(v.act_symbol, o.act_symbol) IS NOT NULL
      AND COALESCE(v.earnings_date, o.earnings_date) IS NOT NULL;
    """
    
    try:
        conn.execute(upcoming_sql)
        
        # Get count of upcoming earnings
        result = conn.execute("SELECT COUNT(*) FROM em_features_upcoming").fetchone()
        count = result[0] if result else 0
        
        result = conn.execute("SELECT COUNT(*) FROM em_features_upcoming WHERE iv_t1 IS NOT NULL").fetchone()
        with_data = result[0] if result else 0
        
        print(f"[features] ✓ Refreshed features for {count} upcoming earnings")
        print(f"[features] ✓ {with_data} have volatility data")
        
        return count > 0
        
    except Exception as e:
        print(f"[features] ⚠ Failed to refresh upcoming features: {e}")
        return False

def load_latest_model(models_dir):
    """Load the latest trained model."""
    print(f"[model] Loading latest model from {models_dir}")
    
    latest_path = models_dir / "em_model_latest.pkl"
    
    if not latest_path.exists():
        # Try to find any model file
        model_files = list(models_dir.glob("em_model_*.pkl"))
        if not model_files:
            print(f"[model] ⚠ No model files found in {models_dir}")
            return None
        
        # Use the most recent model
        latest_path = max(model_files, key=lambda p: p.stat().st_mtime)
        print(f"[model] Using most recent model: {latest_path.name}")
    
    try:
        with open(latest_path, 'rb') as f:
            model_data = pickle.load(f)
        
        print(f"[model] ✓ Loaded model: {model_data['model_type']}")
        return model_data
        
    except Exception as e:
        print(f"[model] ⚠ Failed to load model: {e}")
        return None

def generate_predictions(conn, model_data):
    """Generate predictions for upcoming earnings."""
    print("[predict] Generating predictions for upcoming earnings...")
    
    # Load upcoming features
    query = """
    SELECT 
      act_symbol,
      earnings_date,
      iv_t1,
      hv_t1,
      iv_week_ago,
      iv_month_ago,
      iv_hv_spread,
      iv_percentile_est,
      avg_iv_t1,
      atm_iv_t1,
      iv_skew,
      avg_gamma_t1,
      avg_vega_t1,
      call_put_ratio,
      total_contracts,
      iv_hv_ratio,
      atm_iv_ratio
    FROM em_features_upcoming
    WHERE iv_t1 IS NOT NULL AND iv_t1 > 0
    ORDER BY earnings_date
    """
    
    df = pd.read_sql_query(query, conn)
    
    if len(df) == 0:
        print("[predict] ⚠ No upcoming earnings with valid features")
        return False
    
    print(f"[predict] Scoring {len(df)} upcoming earnings")
    
    # Generate predictions based on model type
    model_type = model_data['model_type']
    
    if model_type == 'heuristic':
        # Simple heuristic: y_pred = alpha * iv_t1
        alpha = model_data['alpha']
        predictions = alpha * df['iv_t1'].fillna(0)
        
    elif model_type == 'linear_regression':
        # Linear regression
        model = model_data['model']
        scaler = model_data['scaler']
        features = model_data['features']
        
        # Prepare features
        X = df[features].copy()
        for col in X.columns:
            X[col] = X[col].fillna(X[col].median())
        
        # Scale and predict
        X_scaled = scaler.transform(X)
        predictions = model.predict(X_scaled)
        
    elif model_type == 'lightgbm':
        # LightGBM
        model = model_data['model']
        features = model_data['features']
        
        # Prepare features
        X = df[features].copy()
        for col in X.columns:
            X[col] = X[col].fillna(X[col].median())
        
        # Predict
        predictions = model.predict(X, num_iteration=model.best_iteration)
        
    else:
        print(f"[predict] ⚠ Unknown model type: {model_type}")
        return False
    
    # Ensure predictions are positive and reasonable
    predictions = np.maximum(predictions, 0.001)  # Minimum 0.1% move
    predictions = np.minimum(predictions, 1.0)    # Maximum 100% move
    
    # Create results dataframe
    results_df = df[['act_symbol', 'earnings_date']].copy()
    results_df['predicted_move'] = predictions
    results_df['model_type'] = model_type
    results_df['model_version'] = datetime.now().strftime("%Y%m%d")
    results_df['generated_at'] = datetime.now()
    results_df['confidence'] = 'medium'  # Could be enhanced with prediction intervals
    
    # Add some context features for the API
    results_df['iv_current'] = df['iv_t1']
    results_df['iv_percentile'] = df['iv_percentile_est']
    results_df['days_to_earnings'] = (pd.to_datetime(df['earnings_date']) - datetime.now()).dt.days
    
    print(f"[predict] ✓ Generated {len(results_df)} predictions")
    print(f"[predict] Prediction range: {predictions.min():.3f} to {predictions.max():.3f}")
    
    return results_df

def save_predictions(conn, predictions_df, outputs_dir):
    """Save predictions to DuckDB and Parquet outputs."""
    print("[save] Saving predictions...")
    
    # Save to DuckDB table
    try:
        # Create/update em_scores table
        conn.execute("DROP TABLE IF EXISTS em_scores_temp")
        conn.register('predictions_temp', predictions_df)
        
        create_scores_sql = """
        CREATE TABLE IF NOT EXISTS em_scores (
          act_symbol VARCHAR,
          earnings_date DATE,
          predicted_move DOUBLE,
          model_type VARCHAR,
          model_version VARCHAR,
          generated_at TIMESTAMP,
          confidence VARCHAR,
          iv_current DOUBLE,
          iv_percentile DOUBLE,
          days_to_earnings INTEGER
        );
        
        -- Remove old predictions for the same earnings dates
        DELETE FROM em_scores 
        WHERE (act_symbol, earnings_date) IN (
          SELECT act_symbol, earnings_date FROM predictions_temp
        );
        
        -- Insert new predictions
        INSERT INTO em_scores 
        SELECT * FROM predictions_temp;
        """
        
        conn.execute(create_scores_sql)
        print("[save] ✓ Saved predictions to em_scores table")
        
        # Create latest view
        latest_view_sql = """
        CREATE OR REPLACE VIEW em_scores_latest AS
        SELECT *
        FROM em_scores
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY act_symbol, earnings_date 
          ORDER BY generated_at DESC
        ) = 1
        ORDER BY earnings_date, act_symbol;
        """
        
        conn.execute(latest_view_sql)
        print("[save] ✓ Created em_scores_latest view")
        
    except Exception as e:
        print(f"[save] ⚠ Failed to save to DuckDB: {e}")
    
    # Save to Parquet output
    try:
        outputs_dir.mkdir(parents=True, exist_ok=True)
        
        today = datetime.now().strftime("%Y-%m-%d")
        parquet_path = outputs_dir / f"em_scores_{today}.parquet"
        
        predictions_df.to_parquet(parquet_path, index=False)
        print(f"[save] ✓ Saved Parquet output: {parquet_path}")
        
        # Also save as latest
        latest_path = outputs_dir / "em_scores_latest.parquet"
        predictions_df.to_parquet(latest_path, index=False)
        print(f"[save] ✓ Saved latest Parquet: {latest_path}")
        
    except Exception as e:
        print(f"[save] ⚠ Failed to save Parquet: {e}")
    
    return True

def cleanup_old_outputs(outputs_dir, keep_days=30):
    """Clean up old output files."""
    print(f"[cleanup] Removing output files older than {keep_days} days...")
    
    try:
        cutoff_date = datetime.now() - timedelta(days=keep_days)
        removed_count = 0
        
        for file_path in outputs_dir.glob("em_scores_*.parquet"):
            if file_path.name == "em_scores_latest.parquet":
                continue  # Keep the latest file
                
            if file_path.stat().st_mtime < cutoff_date.timestamp():
                file_path.unlink()
                removed_count += 1
        
        print(f"[cleanup] ✓ Removed {removed_count} old files")
        
    except Exception as e:
        print(f"[cleanup] ⚠ Cleanup failed: {e}")

def main():
    parser = argparse.ArgumentParser(description="Daily scoring pipeline for Quantiv EM predictions")
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=Path("/srv/quantiv-data/quantiv.duckdb"),
        help="Path to DuckDB file"
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        help="Directory with trained models (default: {data-root}/models)"
    )
    parser.add_argument(
        "--outputs-dir",
        type=Path,
        help="Directory for output files (default: {data-root}/outputs)"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local data/ directory"
    )
    parser.add_argument(
        "--skip-cleanup",
        action="store_true",
        help="Skip cleanup of old output files"
    )
    
    args = parser.parse_args()
    
    if args.local:
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        data_root = project_root / "data"
        duckdb_path = data_root / "quantiv.duckdb"
        models_dir = data_root / "models"
        outputs_dir = data_root / "outputs"
    else:
        duckdb_path = args.duckdb_path
        data_root = duckdb_path.parent
        models_dir = args.models_dir or (data_root / "models")
        outputs_dir = args.outputs_dir or (data_root / "outputs")
    
    print("QUANTIV DAILY SCORING PIPELINE")
    print("=" * 50)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"DuckDB: {duckdb_path}")
    print(f"Models: {models_dir}")
    print(f"Outputs: {outputs_dir}")
    
    if not duckdb_path.exists():
        print(f"[error] DuckDB file not found: {duckdb_path}")
        sys.exit(1)
    
    if not models_dir.exists():
        print(f"[error] Models directory not found: {models_dir}")
        sys.exit(1)
    
    try:
        # Connect to DuckDB
        conn = duckdb.connect(str(duckdb_path))
        
        # Step 1: Refresh upcoming features
        if not refresh_upcoming_features(conn):
            print("[error] Failed to refresh features")
            sys.exit(1)
        
        # Step 2: Load latest model
        model_data = load_latest_model(models_dir)
        if not model_data:
            print("[error] Failed to load model")
            sys.exit(1)
        
        # Step 3: Generate predictions
        predictions_df = generate_predictions(conn, model_data)
        if predictions_df is False or len(predictions_df) == 0:
            print("[warning] No predictions generated")
            conn.close()
            sys.exit(0)
        
        # Step 4: Save predictions
        save_predictions(conn, predictions_df, outputs_dir)
        
        # Step 5: Cleanup old files
        if not args.skip_cleanup:
            cleanup_old_outputs(outputs_dir)
        
        conn.close()
        
        print("\n" + "=" * 50)
        print("DAILY SCORING COMPLETE")
        print("=" * 50)
        print(f"✓ Generated predictions for {len(predictions_df)} upcoming earnings")
        print(f"✓ Results saved to DuckDB and {outputs_dir}")
        print("\nNext earnings with predictions:")
        
        # Show upcoming predictions
        for _, row in predictions_df.head(5).iterrows():
            print(f"  {row['act_symbol']:6} {row['earnings_date']} - {row['predicted_move']:.1%} move")
        
    except Exception as e:
        print(f"[error] Daily scoring failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
