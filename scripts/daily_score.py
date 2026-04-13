#!/usr/bin/env python3
"""
Daily scoring pipeline — generate expected move predictions for upcoming earnings.

Steps:
  1. Load trained LightGBM models
  2. Query v_iv_rv_features + v_earnings for upcoming earnings (next 14 days)
  3. Generate predictions with confidence bands
  4. Write results to DuckDB table + Parquet

Usage:
  python scripts/daily_score.py
  python scripts/daily_score.py --days-ahead 21
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import duckdb
import joblib
import numpy as np
import pandas as pd

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))


def load_models(models_dir: Path) -> Dict[int, dict]:
    """Load all trained models + metadata."""
    models = {}
    for meta_path in sorted(models_dir.glob("metadata_T*.json")):
        with open(meta_path) as f:
            meta = json.load(f)
        horizon = meta["horizon"]
        model_path = models_dir / f"lgbm_T{horizon}.joblib"
        if model_path.exists():
            models[horizon] = {
                "model": joblib.load(model_path),
                "metadata": meta,
                "feature_cols": meta["feature_cols"],
                "residual_std": meta.get("residual_std", 0.03),
            }
            logger.info(f"Loaded T-{horizon} model (MAE={meta.get('val_mae', '?')})")
    return models


def get_upcoming_features(conn: duckdb.DuckDBPyConnection, days_ahead: int) -> pd.DataFrame:
    """Get features for symbols with upcoming earnings."""
    sql = f"""
    SELECT
        f.act_symbol,
        e.date AS earnings_date,
        e.timing,
        f.date AS snapshot_date,
        (e.date - f.date) AS lead_days,
        f.atm_iv,
        f.em_straddle / NULLIF(f.spot_price, 0) AS straddle_pct,
        f.em_iv / NULLIF(f.spot_price, 0) AS em_iv_pct,
        f.dte,
        f.parkinson_rv_10d,
        f.parkinson_rv_20d,
        f.parkinson_rv_60d,
        f.cc_rv_10d,
        f.cc_rv_20d,
        f.vol_of_vol_20d,
        f.iv_rv_ratio_20d,
        f.iv_rv_ratio_60d,
        f.iv_cc_rv_ratio_20d,
        f.rv_term_ratio,
        f.volume_ratio_20d,
        f.drift_5d,
        f.spot_price,
        f.em_straddle,
        f.em_iv
    FROM v_earnings e
    JOIN v_iv_rv_features f
        ON f.act_symbol = e.act_symbol
        AND f.date < e.date
        AND (e.date - f.date) BETWEEN 1 AND 25
    WHERE e.date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '{days_ahead}' DAY
        AND f.atm_iv > 0
        AND f.spot_price > 0
    ORDER BY e.date, f.act_symbol, lead_days
    """
    return conn.execute(sql).fetchdf()


def score(df: pd.DataFrame, models: Dict[int, dict]) -> pd.DataFrame:
    """Generate predictions for each row using the matching horizon model."""
    results = []

    for horizon, m in models.items():
        hdf = df[df["lead_days"] == horizon].copy()
        if hdf.empty:
            continue

        feature_cols = m["feature_cols"]

        # Add engineered features matching training
        hdf["log_spot"] = np.log(hdf["spot_price"].clip(lower=1))
        hdf["timing_bmo"] = (hdf["timing"] == "bmo").astype(float)
        hdf["timing_amc"] = (hdf["timing"] == "amc").astype(float)
        hdf["earnings_month"] = pd.to_datetime(hdf["earnings_date"]).dt.month
        hdf["earnings_dow"] = pd.to_datetime(hdf["earnings_date"]).dt.dayofweek

        # Align columns
        missing = set(feature_cols) - set(hdf.columns)
        for c in missing:
            hdf[c] = 0

        X = hdf[feature_cols].replace([np.inf, -np.inf], np.nan).fillna(0)

        # Predict
        pred = m["model"].predict(X)
        residual_std = m["residual_std"]

        hdf["em_ml_pct"] = pred
        hdf["em_ml_abs"] = pred * hdf["spot_price"]
        hdf["band68_low_pct"] = pred - residual_std
        hdf["band68_high_pct"] = pred + residual_std
        hdf["band95_low_pct"] = pred - 2 * residual_std
        hdf["band95_high_pct"] = pred + 2 * residual_std
        hdf["em_math_pct"] = hdf["straddle_pct"]
        hdf["correction_factor"] = pred / hdf["straddle_pct"].clip(lower=0.001)
        hdf["model_horizon"] = horizon
        hdf["scored_at"] = datetime.now().isoformat()

        results.append(hdf)

    if not results:
        return pd.DataFrame()

    return pd.concat(results, ignore_index=True)


def save_forecasts(df: pd.DataFrame, data_dir: Path):
    """Save forecasts to Parquet and write to DuckDB."""
    if df.empty:
        logger.warning("No forecasts to save")
        return

    # Select output columns
    out_cols = [
        "act_symbol", "earnings_date", "timing", "snapshot_date",
        "model_horizon", "spot_price", "atm_iv",
        "em_math_pct", "em_ml_pct", "em_ml_abs",
        "correction_factor",
        "band68_low_pct", "band68_high_pct",
        "band95_low_pct", "band95_high_pct",
        "iv_rv_ratio_20d", "parkinson_rv_20d", "vol_of_vol_20d",
        "scored_at",
    ]
    out = df[[c for c in out_cols if c in df.columns]].copy()

    # Parquet
    forecast_dir = data_dir / "forecasts"
    forecast_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    parquet_path = forecast_dir / f"forecasts_{today}.parquet"
    out.to_parquet(parquet_path, index=False)
    logger.info(f"Saved {len(out)} forecasts → {parquet_path}")

    # Also write to DuckDB for API serving
    db_path = os.getenv("DUCKDB_PATH", str(data_dir / "quantiv.duckdb"))
    conn = duckdb.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS ml_forecasts AS SELECT * FROM out WHERE 1=0")
    conn.execute("INSERT INTO ml_forecasts SELECT * FROM out")
    n = conn.execute("SELECT COUNT(*) FROM ml_forecasts").fetchone()[0]
    conn.close()
    logger.info(f"DuckDB ml_forecasts: {n:,} total rows")


def main():
    parser = argparse.ArgumentParser(description="Daily scoring pipeline")
    parser.add_argument("--days-ahead", type=int, default=14)
    args = parser.parse_args()

    data_dir = get_data_dir()
    models_dir = data_dir / "models"

    # Load models
    models = load_models(models_dir)
    if not models:
        logger.error(f"No models found in {models_dir}. Run model_trainer_v2.py first.")
        return

    # Connect to DuckDB
    db_path = os.getenv("DUCKDB_PATH", str(data_dir / "quantiv.duckdb"))
    conn = duckdb.connect(db_path, read_only=True)

    # Get features
    df = get_upcoming_features(conn, args.days_ahead)
    conn.close()
    logger.info(f"Found {len(df)} feature rows for upcoming earnings")

    if df.empty:
        logger.warning("No upcoming earnings with features found")
        return

    # Score
    forecasts = score(df, models)
    logger.info(f"Generated {len(forecasts)} forecasts")

    # Save
    save_forecasts(forecasts, data_dir)

    # Print summary
    if not forecasts.empty:
        summary = (
            forecasts.groupby(["act_symbol", "earnings_date"])
            .agg(
                spot=("spot_price", "first"),
                em_math=("em_math_pct", "first"),
                em_ml=("em_ml_pct", "first"),
                correction=("correction_factor", "first"),
            )
            .sort_values("earnings_date")
            .head(20)
        )
        print(f"\n{'='*60}")
        print("UPCOMING EARNINGS FORECASTS")
        print(f"{'='*60}")
        print(summary.to_string())


if __name__ == "__main__":
    main()
