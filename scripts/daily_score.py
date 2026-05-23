#!/usr/bin/env python3
"""
Daily scoring pipeline v3 — generate expected move predictions for upcoming earnings.

Steps:
  1. Load trained LightGBM point + quantile models
  2. Query features for upcoming earnings (next 14 days)
  3. Generate predictions with true quantile confidence bands
  4. Compute em_math using both straddle and event vol decomposition
  5. Write results to DuckDB table + Parquet

Output columns:
  act_symbol, earnings_date, spot, em_math, em_event_vol, em_ml,
  correction, p10, p25, p50, p75, p90

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
    """Load point + quantile models for each horizon."""
    models = {}
    for meta_path in sorted(models_dir.glob("metadata_T*.json")):
        with open(meta_path) as f:
            meta = json.load(f)
        horizon = meta["horizon"]
        model_path = models_dir / f"lgbm_T{horizon}.joblib"
        if not model_path.exists():
            continue

        # Model joblib is a dict {model, calibrator, hyperparameters, feature_names, ...}.
        # Unwrap the actual estimator so score() can call .predict(). Older metadata
        # JSONs included a `feature_cols` field, but the post-2025-Q4 retrain dropped
        # it — use the joblib's `feature_names` as the source of truth and only fall
        # back to the metadata's `feature_importance` keys for very old artifacts.
        model_data = joblib.load(model_path)
        if isinstance(model_data, dict):
            estimator = model_data.get("model")
            calibrator = model_data.get("calibrator")
            joblib_features = list(model_data.get("feature_names") or [])
        else:
            estimator = model_data
            calibrator = None
            joblib_features = list(getattr(estimator, "feature_name_", []) or [])

        feature_cols = (
            joblib_features
            or list((meta.get("feature_importance") or {}).keys())
            or meta.get("feature_cols")
        )
        if estimator is None or not feature_cols:
            logger.warning(f"T-{horizon} model unusable (no estimator or feature schema) — skipping")
            continue

        entry = {
            "model": estimator,
            "calibrator": calibrator,
            "metadata": meta,
            "feature_cols": feature_cols,
            "residual_std": meta.get("residual_std", 0.03),
            "quantile_models": {},
        }

        # Load quantile models if available. Same wrap pattern as the point
        # model — joblib payload may be a dict or a raw estimator.
        quantiles = meta.get("quantiles", [0.10, 0.25, 0.50, 0.75, 0.90])
        for alpha in quantiles:
            q_path = models_dir / f"lgbm_T{horizon}_q{int(alpha*100):02d}.joblib"
            if not q_path.exists():
                continue
            q_payload = joblib.load(q_path)
            q_estimator = q_payload.get("model") if isinstance(q_payload, dict) else q_payload
            if q_estimator is not None:
                entry["quantile_models"][alpha] = q_estimator

        models[horizon] = entry
        q_str = f" + {len(entry['quantile_models'])} quantile" if entry["quantile_models"] else ""
        logger.info(f"Loaded T-{horizon} model (MAE={meta.get('val_mae', '?')}){q_str}")

    return models


def get_upcoming_features(conn: duckdb.DuckDBPyConnection, days_ahead: int) -> pd.DataFrame:
    """Get features for symbols with upcoming earnings.

    Uses the same feature set as feature_engineering_v3:
    - Core options features from v_straddle_features
    - Event vol decomposition from front/back month IV
    - Historical earnings patterns
    - Realized vol features (where OHLCV available)
    """
    sql = f"""
    WITH upcoming AS (
        SELECT act_symbol, date AS earnings_date, timing
        FROM v_earnings
        WHERE date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '{days_ahead}' DAY
    ),

    -- Historical realized moves for trailing stats
    past_realized_ohlcv AS (
        SELECT e.act_symbol, e.date AS earnings_date,
               ABS(post.close / NULLIF(pre.close, 0) - 1.0) AS realized_move_pct
        FROM v_earnings e
        JOIN v_ohlcv pre ON pre.act_symbol = e.act_symbol
            AND pre.date < e.date AND pre.date >= e.date - INTERVAL '5' DAY
        JOIN v_ohlcv post ON post.act_symbol = e.act_symbol
            AND post.date > e.date AND post.date <= e.date + INTERVAL '5' DAY
        WHERE e.date < CURRENT_DATE AND pre.close > 0 AND post.close > 0
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY e.act_symbol, e.date ORDER BY pre.date DESC, post.date ASC
        ) = 1
    ),
    trailing_stats AS (
        SELECT
            u.act_symbol,
            u.earnings_date,
            AVG(p.realized_move_pct) AS hist_move_avg_4q,
            MEDIAN(p.realized_move_pct) AS hist_move_med_4q,
            STDDEV(p.realized_move_pct) AS hist_move_std_4q,
            (SELECT AVG(x.realized_move_pct) FROM (
                SELECT p2.realized_move_pct
                FROM past_realized_ohlcv p2
                WHERE p2.act_symbol = u.act_symbol
                  AND p2.earnings_date < u.earnings_date
                ORDER BY p2.earnings_date DESC LIMIT 8
             ) x) AS hist_move_avg_8q,
            (SELECT MEDIAN(x.realized_move_pct) FROM (
                SELECT p2.realized_move_pct
                FROM past_realized_ohlcv p2
                WHERE p2.act_symbol = u.act_symbol
                  AND p2.earnings_date < u.earnings_date
                ORDER BY p2.earnings_date DESC LIMIT 8
             ) x) AS hist_move_med_8q,
            COUNT(p.realized_move_pct) AS hist_event_count
        FROM upcoming u
        LEFT JOIN past_realized_ohlcv p
            ON p.act_symbol = u.act_symbol
            AND p.earnings_date < u.earnings_date
            AND p.earnings_date >= u.earnings_date - INTERVAL '2' YEAR
        GROUP BY u.act_symbol, u.earnings_date
    ),

    -- Event vol decomposition
    event_vol AS (
        SELECT
            sf.act_symbol,
            sf.date AS snapshot_date,
            u.earnings_date,
            sf.atm_iv AS front_iv,
            sf.dte AS front_dte,
            back.atm_iv AS back_iv,
            back.dte AS back_dte,
            CASE
                WHEN sf.atm_iv > 0 AND back.atm_iv > 0 AND sf.atm_iv > back.atm_iv
                THEN SQRT(GREATEST(0,
                    sf.atm_iv * sf.atm_iv * (sf.dte / 365.0)
                    - back.atm_iv * back.atm_iv * (back.dte / 365.0)))
                ELSE NULL
            END AS event_move_implied,
            CASE
                WHEN sf.atm_iv > 0 AND back.atm_iv > 0
                THEN (sf.atm_iv - back.atm_iv) / sf.atm_iv
                ELSE NULL
            END AS iv_crush_pct
        FROM v_straddle_features sf
        JOIN upcoming u ON u.act_symbol = sf.act_symbol
            AND sf.date < u.earnings_date
            AND (u.earnings_date - sf.date) BETWEEN 1 AND 25
            AND sf.expiration >= u.earnings_date
        LEFT JOIN v_straddle_features back
            ON back.act_symbol = sf.act_symbol
            AND back.date = sf.date
            AND back.expiration > sf.expiration
            AND back.dte > sf.dte
            AND back.atm_iv > 0
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY sf.act_symbol, sf.date, u.earnings_date
            ORDER BY back.dte ASC
        ) = 1
    )

    SELECT
        sf.act_symbol,
        u.earnings_date,
        u.timing,
        sf.date AS snapshot_date,
        (u.earnings_date - sf.date) AS lead_days,

        -- Core options
        sf.atm_iv,
        sf.straddle_mid / NULLIF(sf.atm_strike, 0) AS straddle_pct,
        sf.em_iv / NULLIF(sf.atm_strike, 0) AS em_iv_pct,
        sf.dte,

        -- Event vol decomposition
        ev.event_move_implied,
        ev.iv_crush_pct,
        ev.front_iv,
        ev.back_iv,
        CASE WHEN ev.event_move_implied > 0 AND sf.atm_strike > 0
             THEN ev.event_move_implied / (sf.straddle_mid / NULLIF(sf.atm_strike, 0))
             ELSE NULL
        END AS event_vol_fraction,

        -- Realized vol
        rv.parkinson_rv_10d,
        rv.parkinson_rv_20d,
        rv.parkinson_rv_60d,
        rv.cc_rv_10d,
        rv.cc_rv_20d,
        rv.vol_of_vol_20d,

        -- IV / RV ratios
        sf.atm_iv / NULLIF(rv.parkinson_rv_20d, 0) AS iv_rv_ratio_20d,
        sf.atm_iv / NULLIF(rv.parkinson_rv_60d, 0) AS iv_rv_ratio_60d,
        sf.atm_iv / NULLIF(rv.cc_rv_20d, 0)        AS iv_cc_rv_ratio_20d,
        rv.parkinson_rv_10d / NULLIF(rv.parkinson_rv_60d, 0) AS rv_term_ratio,

        -- Market context
        rv.volume_ratio_20d,
        rv.drift_5d,
        COALESCE(rv.close, sf.atm_strike) AS spot_price,

        sf.em_straddle,
        sf.em_iv,

        -- Historical earnings stats
        ts.hist_move_avg_4q,
        ts.hist_move_med_4q,
        ts.hist_move_std_4q,
        ts.hist_move_avg_8q,
        ts.hist_move_med_8q,
        ts.hist_event_count,
        CASE WHEN ts.hist_move_avg_4q > 0
             AND sf.straddle_mid / NULLIF(sf.atm_strike, 0) > 0
             THEN ts.hist_move_avg_4q / (sf.straddle_mid / NULLIF(sf.atm_strike, 0))
             ELSE NULL
        END AS hist_straddle_accuracy

    FROM upcoming u
    JOIN v_straddle_features sf
        ON sf.act_symbol = u.act_symbol
        AND sf.date < u.earnings_date
        AND (u.earnings_date - sf.date) BETWEEN 1 AND 25
    LEFT JOIN v_realized_vol rv
        ON rv.act_symbol = sf.act_symbol AND rv.date = sf.date
    LEFT JOIN event_vol ev
        ON ev.act_symbol = sf.act_symbol
        AND ev.snapshot_date = sf.date
        AND ev.earnings_date = u.earnings_date
    LEFT JOIN trailing_stats ts
        ON ts.act_symbol = u.act_symbol
        AND ts.earnings_date = u.earnings_date
    WHERE sf.atm_iv > 0 AND sf.atm_strike > 0
    ORDER BY u.earnings_date, sf.act_symbol, lead_days
    """
    return conn.execute(sql).fetchdf()


def score(df: pd.DataFrame, models: Dict[int, dict]) -> pd.DataFrame:
    """Generate predictions with point estimate + quantile bands."""
    results = []

    for horizon, m in models.items():
        hdf = df[df["lead_days"] == horizon].copy()
        if hdf.empty:
            continue

        feature_cols = m["feature_cols"]

        hdf["log_spot"] = np.log(hdf["spot_price"].clip(lower=1))
        hdf["timing_bmo"] = (hdf["timing"] == "bmo").astype(float)
        hdf["timing_amc"] = (hdf["timing"] == "amc").astype(float)
        hdf["earnings_month"] = pd.to_datetime(hdf["earnings_date"]).dt.month
        hdf["earnings_dow"] = pd.to_datetime(hdf["earnings_date"]).dt.dayofweek

        missing = set(feature_cols) - set(hdf.columns)
        for c in missing:
            hdf[c] = np.nan

        X = hdf[feature_cols].replace([np.inf, -np.inf], np.nan)

        # Persist the exact feature vector used at scoring time so the
        # /api/ml/predict route can re-run inference later with a live spot
        # substituted in. Stored as a JSON string per row → loaded into a
        # JSONB column by scripts/import_recent_to_postgres.py. NaN values
        # become null in JSON, which the route's spot-substitution helper
        # then re-fills against the joblib feature schema.
        feature_records = X.to_dict(orient="records")
        hdf["feature_vector"] = [json.dumps(row, default=str) for row in feature_records]

        # Point prediction
        pred = m["model"].predict(X)
        hdf["em_ml_pct"] = pred
        hdf["em_ml_abs"] = pred * hdf["spot_price"]

        # Quantile predictions
        q_models = m.get("quantile_models", {})
        if q_models:
            for alpha, qm in q_models.items():
                col = f"p{int(alpha * 100):02d}"
                hdf[col] = qm.predict(X)
        else:
            # Fallback: Gaussian bands from residual_std
            residual_std = m["residual_std"]
            hdf["p10"] = pred - 1.28 * residual_std
            hdf["p25"] = pred - 0.67 * residual_std
            hdf["p50"] = pred
            hdf["p75"] = pred + 0.67 * residual_std
            hdf["p90"] = pred + 1.28 * residual_std

        # Math baselines
        hdf["em_math_pct"] = hdf["straddle_pct"]
        hdf["em_event_vol_pct"] = hdf.get("event_move_implied", np.nan)
        hdf["correction_factor"] = pred / hdf["straddle_pct"].clip(lower=0.001)
        hdf["model_horizon"] = horizon
        hdf["scored_at"] = datetime.now().isoformat()

        results.append(hdf)

    if not results:
        return pd.DataFrame()

    return pd.concat(results, ignore_index=True)


def save_forecasts(df: pd.DataFrame, data_dir: Path):
    if df.empty:
        logger.warning("No forecasts to save")
        return

    out_cols = [
        "act_symbol", "earnings_date", "timing", "snapshot_date",
        "model_horizon", "spot_price", "atm_iv",
        "em_math_pct", "em_event_vol_pct", "em_ml_pct", "em_ml_abs",
        "correction_factor",
        "p10", "p25", "p50", "p75", "p90",
        "iv_crush_pct", "event_vol_fraction",
        "hist_move_avg_4q", "hist_straddle_accuracy",
        "iv_rv_ratio_20d", "parkinson_rv_20d", "vol_of_vol_20d",
        "scored_at",
        # Feature vector used at scoring time — consumed by the API's
        # re-inference path. JSON string in the Parquet so DuckDB and
        # Pandas readers don't have to know the schema.
        "feature_vector",
    ]
    out = df[[c for c in out_cols if c in df.columns]].copy()

    # Parquet
    forecast_dir = data_dir / "forecasts"
    forecast_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    parquet_path = forecast_dir / f"forecasts_{today}.parquet"
    out.to_parquet(parquet_path, index=False)
    logger.info(f"Saved {len(out)} forecasts → {parquet_path}")

    # DuckDB
    db_path = os.getenv("DUCKDB_PATH", str(data_dir / "quantiv.duckdb"))
    conn = duckdb.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS ml_forecasts AS SELECT * FROM out WHERE 1=0")
    conn.execute("INSERT INTO ml_forecasts SELECT * FROM out")
    n = conn.execute("SELECT COUNT(*) FROM ml_forecasts").fetchone()[0]
    conn.close()
    logger.info(f"DuckDB ml_forecasts: {n:,} total rows")


def main():
    parser = argparse.ArgumentParser(description="Daily scoring pipeline v3")
    parser.add_argument("--days-ahead", type=int, default=14)
    args = parser.parse_args()

    data_dir = get_data_dir()
    models_dir = data_dir / "models"

    models = load_models(models_dir)
    if not models:
        logger.error(f"No models found in {models_dir}. Run model_trainer_v3.py first.")
        return

    db_path = os.getenv("DUCKDB_PATH", str(data_dir / "quantiv.duckdb"))
    conn = duckdb.connect(db_path, read_only=False)

    # Ensure optional views exist (empty stubs if no OHLCV data)
    views = [r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
    ).fetchall()]
    if "v_ohlcv" not in views:
        logger.warning("v_ohlcv not found — will skip OHLCV-based trailing stats")
        conn.execute("""
            CREATE OR REPLACE VIEW v_ohlcv AS
            SELECT NULL::VARCHAR AS act_symbol, NULL::DATE AS date,
                   NULL::DOUBLE AS close WHERE 1=0
        """)
    if "v_realized_vol" not in views:
        logger.warning("v_realized_vol not found — RV features will be NULL")
        conn.execute("""
            CREATE OR REPLACE VIEW v_realized_vol AS
            SELECT NULL::VARCHAR AS act_symbol, NULL::DATE AS date,
                   NULL::DOUBLE AS close,
                   NULL::DOUBLE AS parkinson_rv_10d, NULL::DOUBLE AS parkinson_rv_20d,
                   NULL::DOUBLE AS parkinson_rv_60d,
                   NULL::DOUBLE AS cc_rv_10d, NULL::DOUBLE AS cc_rv_20d,
                   NULL::DOUBLE AS vol_of_vol_20d,
                   NULL::DOUBLE AS volume_ratio_20d, NULL::DOUBLE AS drift_5d
            WHERE 1=0
        """)

    df = get_upcoming_features(conn, args.days_ahead)
    conn.close()
    logger.info(f"Found {len(df)} feature rows for upcoming earnings")

    if df.empty:
        logger.warning("No upcoming earnings with features found")
        return

    forecasts = score(df, models)
    logger.info(f"Generated {len(forecasts)} forecasts")

    save_forecasts(forecasts, data_dir)

    if not forecasts.empty:
        # Print the clean dashboard output
        has_quantiles = "p10" in forecasts.columns and "p90" in forecasts.columns
        summary = (
            forecasts.groupby(["act_symbol", "earnings_date"])
            .agg(
                spot=("spot_price", "first"),
                em_math=("em_math_pct", "first"),
                em_event=("em_event_vol_pct", "first") if "em_event_vol_pct" in forecasts.columns else ("em_math_pct", "first"),
                em_ml=("em_ml_pct", "first"),
                correction=("correction_factor", "first"),
                **({
                    "p10": ("p10", "first"),
                    "p50": ("p50", "first"),
                    "p90": ("p90", "first"),
                } if has_quantiles else {}),
            )
            .sort_values("earnings_date")
            .head(30)
        )
        print(f"\n{'='*90}")
        print("UPCOMING EARNINGS FORECASTS (v3)")
        print(f"{'='*90}")
        pd.set_option("display.float_format", "{:.4f}".format)
        pd.set_option("display.max_columns", 20)
        pd.set_option("display.width", 120)
        print(summary.to_string())


if __name__ == "__main__":
    main()
