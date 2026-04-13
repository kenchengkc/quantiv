#!/usr/bin/env python3
"""
Feature Engineering v2 — uses the new DuckDB views (v_iv_rv_features + v_earnings).

Produces training datasets for LightGBM models at each lead-time horizon:
  T-1, T-2, T-3, T-7, T-14, T-21 days before earnings.

Target: realized_move_pct = |close_post / close_pre - 1|
Features: ATM IV, straddle %, Parkinson RV, IV/RV ratio, vol-of-vol, drift, etc.

Usage:
  python apps/ml/feature_engineering_v2.py
  python apps/ml/feature_engineering_v2.py --start-date 2023-04-13 --end-date 2025-12-31
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

import duckdb
import numpy as np
import pandas as pd

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HORIZONS = [1, 2, 3, 7, 14, 21]


@dataclass
class FeatureSet:
    horizon: int
    features: pd.DataFrame
    target: pd.Series
    metadata: Dict[str, Any] = field(default_factory=dict)


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data")))


def connect_duckdb() -> duckdb.DuckDBPyConnection:
    """Connect to the project DuckDB with all views already set up."""
    db_path = os.getenv("DUCKDB_PATH", str(get_data_dir() / "quantiv.duckdb"))
    conn = duckdb.connect(db_path, read_only=True)
    # Verify key views exist
    views = [r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
    ).fetchall()]
    for v in ["v_iv_rv_features", "v_earnings", "v_realized_vol"]:
        if v not in views:
            raise RuntimeError(f"Required view '{v}' missing. Run: python scripts/setup_duckdb_from_parquet.py")
    return conn


# ---------------------------------------------------------------------------
# Extract training data
# ---------------------------------------------------------------------------
def extract_training_data(conn: duckdb.DuckDBPyConnection,
                          start_date: str, end_date: str) -> Dict[int, FeatureSet]:
    """Build feature sets for all horizons from the DuckDB views.

    The query:
      1. Joins earnings events with v_iv_rv_features at each lead time
      2. Computes the realized move target from OHLCV close prices
      3. Enriches with Parkinson RV, IV/RV ratios, vol-of-vol, etc.
    """

    logger.info(f"Extracting features: {start_date} → {end_date}")

    # ---------- Main query ----------
    # For each earnings event, find the options snapshot at T-k and the OHLCV
    # closes before/after earnings to compute the realized move.
    sql = """
    WITH earnings AS (
        SELECT
            act_symbol,
            date AS earnings_date,
            timing
        FROM v_earnings
        WHERE date BETWEEN ?::DATE AND ?::DATE
    ),

    -- Pre-earnings close: last close on or before earnings_date - 1
    pre_close AS (
        SELECT
            e.act_symbol,
            e.earnings_date,
            rv.close AS pre_price,
            rv.date AS pre_date,
            ROW_NUMBER() OVER (
                PARTITION BY e.act_symbol, e.earnings_date
                ORDER BY rv.date DESC
            ) AS rn
        FROM earnings e
        JOIN v_realized_vol rv
            ON rv.act_symbol = e.act_symbol
            AND rv.date < e.earnings_date
            AND rv.date >= e.earnings_date - INTERVAL '5' DAY
    ),

    -- Post-earnings close: first close on or after earnings_date + 1
    post_close AS (
        SELECT
            e.act_symbol,
            e.earnings_date,
            rv.close AS post_price,
            rv.date AS post_date,
            ROW_NUMBER() OVER (
                PARTITION BY e.act_symbol, e.earnings_date
                ORDER BY rv.date ASC
            ) AS rn
        FROM earnings e
        JOIN v_realized_vol rv
            ON rv.act_symbol = e.act_symbol
            AND rv.date > e.earnings_date
            AND rv.date <= e.earnings_date + INTERVAL '5' DAY
    ),

    -- Realized moves
    realized AS (
        SELECT
            pre.act_symbol,
            pre.earnings_date,
            pre.pre_price,
            post.post_price,
            ABS(post.post_price / NULLIF(pre.pre_price, 0) - 1.0) AS realized_move_pct
        FROM pre_close pre
        JOIN post_close post
            ON pre.act_symbol = post.act_symbol
            AND pre.earnings_date = post.earnings_date
        WHERE pre.rn = 1 AND post.rn = 1
            AND pre.pre_price > 0 AND post.post_price > 0
    ),

    -- Join features at each lead time
    snapshots AS (
        SELECT
            f.act_symbol,
            e.earnings_date,
            e.timing,
            f.date AS snapshot_date,
            (e.earnings_date - f.date) AS lead_days,
            -- Options features
            f.atm_iv,
            f.em_straddle / NULLIF(f.spot_price, 0) AS straddle_pct,
            f.em_iv / NULLIF(f.spot_price, 0) AS em_iv_pct,
            f.dte,
            -- Realized vol features
            f.parkinson_rv_10d,
            f.parkinson_rv_20d,
            f.parkinson_rv_60d,
            f.cc_rv_10d,
            f.cc_rv_20d,
            f.vol_of_vol_20d,
            -- IV / RV ratios
            f.iv_rv_ratio_20d,
            f.iv_rv_ratio_60d,
            f.iv_cc_rv_ratio_20d,
            f.rv_term_ratio,
            -- Market context
            f.volume_ratio_20d,
            f.drift_5d,
            f.spot_price,
            -- Target
            r.realized_move_pct
        FROM earnings e
        JOIN v_iv_rv_features f
            ON f.act_symbol = e.act_symbol
            AND f.date < e.earnings_date
            AND (e.earnings_date - f.date) BETWEEN 1 AND 25
        JOIN realized r
            ON r.act_symbol = e.act_symbol
            AND r.earnings_date = e.earnings_date
        WHERE f.atm_iv > 0 AND f.spot_price > 0
    )

    SELECT * FROM snapshots
    ORDER BY act_symbol, earnings_date, lead_days
    """

    df = conn.execute(sql, [start_date, end_date]).fetchdf()
    logger.info(f"Raw snapshot rows: {len(df):,}")

    if df.empty:
        logger.warning("No data returned — check that options + OHLCV + earnings overlap")
        return {}

    # ---------- Build per-horizon feature sets ----------
    feature_sets: Dict[int, FeatureSet] = {}

    for horizon in HORIZONS:
        hdf = df[df["lead_days"] == horizon].copy()
        if len(hdf) < 20:
            logger.warning(f"T-{horizon}: only {len(hdf)} samples, skipping (need ≥20)")
            continue

        # Feature columns
        feature_cols = [
            "atm_iv", "straddle_pct", "em_iv_pct", "dte",
            "parkinson_rv_10d", "parkinson_rv_20d", "parkinson_rv_60d",
            "cc_rv_10d", "cc_rv_20d", "vol_of_vol_20d",
            "iv_rv_ratio_20d", "iv_rv_ratio_60d", "iv_cc_rv_ratio_20d",
            "rv_term_ratio", "volume_ratio_20d", "drift_5d",
        ]

        # Additional engineered features
        hdf["log_spot"] = np.log(hdf["spot_price"].clip(lower=1))
        hdf["timing_bmo"] = (hdf["timing"] == "bmo").astype(float)
        hdf["timing_amc"] = (hdf["timing"] == "amc").astype(float)
        hdf["earnings_month"] = pd.to_datetime(hdf["earnings_date"]).dt.month
        hdf["earnings_dow"] = pd.to_datetime(hdf["earnings_date"]).dt.dayofweek

        feature_cols += ["log_spot", "timing_bmo", "timing_amc",
                         "earnings_month", "earnings_dow"]

        X = hdf[feature_cols].copy()
        y = hdf["realized_move_pct"].copy()

        # Clean
        X = X.replace([np.inf, -np.inf], np.nan)
        valid = X.notna().all(axis=1) & y.notna() & (y > 0) & (y < 1.0)
        X = X[valid].reset_index(drop=True)
        y = y[valid].reset_index(drop=True)

        if len(X) < 20:
            logger.warning(f"T-{horizon}: only {len(X)} valid samples after cleaning")
            continue

        feature_sets[horizon] = FeatureSet(
            horizon=horizon,
            features=X,
            target=y,
            metadata={
                "n_samples": len(X),
                "target_mean": float(y.mean()),
                "target_median": float(y.median()),
                "target_std": float(y.std()),
                "straddle_pct_mean": float(X["straddle_pct"].mean()),
                "iv_rv_ratio_mean": float(X["iv_rv_ratio_20d"].mean()),
                "feature_cols": feature_cols,
            }
        )
        logger.info(f"T-{horizon}: {len(X)} samples, target mean={y.mean():.4f}, median={y.median():.4f}")

    return feature_sets


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
def save_training_data(feature_sets: Dict[int, FeatureSet], output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    for horizon, fs in feature_sets.items():
        training_df = fs.features.copy()
        training_df["target"] = fs.target
        path = output_dir / f"training_T{horizon}.parquet"
        training_df.to_parquet(path, index=False)

        meta_path = output_dir / f"metadata_T{horizon}.json"
        with open(meta_path, "w") as f:
            json.dump(fs.metadata, f, indent=2)

        logger.info(f"Saved T-{horizon}: {len(training_df)} rows → {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Extract ML training features (v2)")
    parser.add_argument("--start-date", default="2023-04-13")
    parser.add_argument("--end-date", default="2026-04-10")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    conn = connect_duckdb()
    feature_sets = extract_training_data(conn, args.start_date, args.end_date)
    conn.close()

    if not feature_sets:
        logger.error("No feature sets generated")
        return

    output_dir = Path(args.output_dir) if args.output_dir else get_data_dir() / "ml_training"
    save_training_data(feature_sets, output_dir)

    print("\n=== Training Data Summary ===")
    for h, fs in sorted(feature_sets.items()):
        m = fs.metadata
        print(f"  T-{h:2d}: {m['n_samples']:5d} samples | "
              f"target μ={m['target_mean']:.4f} med={m['target_median']:.4f} | "
              f"straddle%={m['straddle_pct_mean']:.4f} | "
              f"IV/RV={m['iv_rv_ratio_mean']:.2f}")


if __name__ == "__main__":
    main()
