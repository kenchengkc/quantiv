#!/usr/bin/env python3
"""Score upcoming earnings with the trained models.

Writes a best-guess expected move plus high/low bands to Parquet and DuckDB.

Usage:
  python scripts/daily_score.py
  python scripts/daily_score.py --days-ahead 21
"""

import argparse
import json
import logging
import math
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
ML_PACKAGE_ROOT = REPO_ROOT / "apps" / "ml"
if str(ML_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_PACKAGE_ROOT))

from ml.model_artifact import (  # noqa: E402 - standalone script path setup
    load_native_model,
    point_model_name,
    quantile_model_name,
)
from ml.model_bundle import (  # noqa: E402 - standalone script path setup
    ModelBundleError,
    resolve_champion_bundle,
)
from ml.corporate_actions import (  # noqa: E402 - standalone script path setup
    adjusted_post_price_sql,
    ensure_corporate_action_views,
)
from ml.pipeline_validation import (  # noqa: E402 - standalone script path setup
    FORECAST_REQUIRED_COLUMNS,
)
from ml.quantiles import rearrange_quantile_array  # noqa: E402 - standalone script path setup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _sanitize_for_json(value: Any) -> Any:
    """Recursively replace non-finite floats with None so the result is
    strict-JSON safe. We can't rely on pandas' `.where(..., None)` because
    float64 columns coerce the None back to NaN — we have to walk the dict
    tree after `to_dict()` instead.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_json(v) for v in value]
    return value


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))


def load_models(models_dir: Path) -> Dict[int, dict]:
    """Load the best-guess model and the high/low band models for each target."""
    models = {}
    bundle_id = None
    manifest_path = models_dir / "manifest.json"
    if manifest_path.exists():
        bundle_id = json.loads(manifest_path.read_text()).get("bundle_id")
    for meta_path in sorted(models_dir.glob("metadata_T*.json")):
        with open(meta_path) as f:
            meta = json.load(f)
        horizon = meta["horizon"]
        model_path = models_dir / point_model_name(horizon)
        if not model_path.exists():
            continue

        estimator = load_native_model(model_path)
        native_features = list(estimator.feature_name())
        feature_cols = list(meta.get("feature_cols") or native_features)
        if not feature_cols:
            logger.warning(f"T-{horizon} model unusable (no estimator or feature schema) — skipping")
            continue
        if feature_cols != native_features:
            raise ValueError(f"T-{horizon} native model schema does not match metadata")

        entry = {
            "model": estimator,
            "metadata": meta,
            "feature_cols": feature_cols,
            "residual_std": meta.get("residual_std", 0.03),
            "quantile_models": {},
            "bundle_id": bundle_id,
        }

        quantiles = meta.get("quantiles", [0.10, 0.25, 0.50, 0.75, 0.90])
        for alpha in quantiles:
            q_path = models_dir / quantile_model_name(horizon, int(alpha * 100))
            if not q_path.exists():
                continue
            q_estimator = load_native_model(q_path)
            if list(q_estimator.feature_name()) != feature_cols:
                raise ValueError(f"T-{horizon} q{int(alpha * 100):02d} schema mismatch")
            entry["quantile_models"][alpha] = q_estimator

        models[horizon] = entry
        q_str = f" + {len(entry['quantile_models'])} quantile" if entry["quantile_models"] else ""
        logger.info(f"Loaded T-{horizon} model (MAE={meta.get('val_mae', '?')}){q_str}")

    return models


def get_upcoming_features(conn: duckdb.DuckDBPyConnection, days_ahead: int) -> pd.DataFrame:
    """Features for symbols with earnings in the next `days_ahead` days.

    Same columns as feature_engineering.py (options, past earnings, realized vol).
    """
    ensure_corporate_action_views(conn)
    adjusted_post = adjusted_post_price_sql(
        symbol="pre.act_symbol",
        pre_date="pre.pre_date",
        post_date="post.post_date",
        post_price="post.post_price",
    )
    sql = f"""
    WITH upcoming AS (
        SELECT act_symbol, date AS earnings_date, timing
        FROM v_earnings
        WHERE date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '{days_ahead}' DAY
    ),

    -- Past earnings moves for history features
    pre_close_ohlcv AS (
        SELECT e.act_symbol, e.date AS earnings_date,
               pre.date AS pre_date, pre.close AS pre_price,
               ROW_NUMBER() OVER (
                   PARTITION BY e.act_symbol, e.date ORDER BY pre.date DESC
               ) AS rn
        FROM v_earnings e
        JOIN v_ohlcv pre ON pre.act_symbol = e.act_symbol
            AND pre.date < e.date AND pre.date >= e.date - INTERVAL '5' DAY
        WHERE e.date < CURRENT_DATE AND pre.close > 0
    ),
    post_close_ohlcv AS (
        SELECT e.act_symbol, e.date AS earnings_date,
               post.date AS post_date, post.close AS post_price,
               ROW_NUMBER() OVER (
                   PARTITION BY e.act_symbol, e.date ORDER BY post.date ASC
               ) AS rn
        FROM v_earnings e
        JOIN v_ohlcv post ON post.act_symbol = e.act_symbol
            AND post.date > e.date AND post.date <= e.date + INTERVAL '5' DAY
        WHERE e.date < CURRENT_DATE AND post.close > 0
    ),
    past_realized_ohlcv AS (
        SELECT pre.act_symbol, pre.earnings_date,
               ABS({adjusted_post} / NULLIF(pre.pre_price, 0) - 1.0)
                   AS realized_move_pct
        FROM pre_close_ohlcv pre
        JOIN post_close_ohlcv post USING (act_symbol, earnings_date)
        WHERE pre.rn = 1 AND post.rn = 1
    ),
    history_for_upcoming AS (
        SELECT
            u.act_symbol,
            u.earnings_date,
            p.earnings_date AS historical_earnings_date,
            p.realized_move_pct,
            ROW_NUMBER() OVER (
                PARTITION BY u.act_symbol, u.earnings_date
                ORDER BY p.earnings_date DESC
            ) AS recency_rank
        FROM upcoming u
        LEFT JOIN past_realized_ohlcv p
            ON p.act_symbol = u.act_symbol
            AND p.earnings_date < u.earnings_date
    ),
    trailing_stats AS (
        SELECT
            act_symbol,
            earnings_date,
            AVG(realized_move_pct) FILTER (WHERE recency_rank <= 4) AS hist_move_avg_4q,
            MEDIAN(realized_move_pct) FILTER (WHERE recency_rank <= 4) AS hist_move_med_4q,
            STDDEV(realized_move_pct) FILTER (WHERE recency_rank <= 4) AS hist_move_std_4q,
            AVG(realized_move_pct) FILTER (WHERE recency_rank <= 8) AS hist_move_avg_8q,
            MEDIAN(realized_move_pct) FILTER (WHERE recency_rank <= 8) AS hist_move_med_8q,
            QUANTILE_CONT(realized_move_pct, 0.75) FILTER (
                WHERE recency_rank <= 8
            ) AS hist_move_p75_8q,
            QUANTILE_CONT(realized_move_pct, 0.90) FILTER (
                WHERE recency_rank <= 8
            ) AS hist_move_p90_8q,
            KURTOSIS(realized_move_pct) FILTER (
                WHERE recency_rank <= 8
            ) AS hist_move_kurt_8q,
            MAX(realized_move_pct) FILTER (
                WHERE recency_rank <= 12
            ) AS hist_move_max_12q,
            ARG_MAX(realized_move_pct, historical_earnings_date) AS hist_move_last,
            COUNT(realized_move_pct) AS hist_event_count
        FROM history_for_upcoming
        GROUP BY act_symbol, earnings_date
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
            AND (
                (LOWER(COALESCE(u.timing, '')) = 'amc' AND sf.expiration > u.earnings_date)
                OR (LOWER(COALESCE(u.timing, '')) != 'amc' AND sf.expiration >= u.earnings_date)
            )
        LEFT JOIN v_straddle_features back
            ON back.act_symbol = sf.act_symbol
            AND back.date = sf.date
            AND back.expiration > sf.expiration
            AND back.dte > sf.dte
            AND back.atm_iv > 0
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY sf.act_symbol, sf.date, u.earnings_date
            ORDER BY sf.expiration ASC, back.dte ASC
        ) = 1
    ),

    -- Same point-in-time macro context used during training.
    macro AS (
        WITH spy AS (SELECT date, close FROM v_ohlcv WHERE act_symbol = 'SPY'),
             tlt AS (SELECT date, close FROM v_ohlcv WHERE act_symbol = 'TLT')
        SELECT
            spy.date,
            vix.vix_close AS vix_current,
            vix.vix_close
              - LAG(vix.vix_close, 21) OVER (ORDER BY spy.date) AS vix_change_30d,
            PERCENT_RANK() OVER (
                ORDER BY vix.vix_close
                ROWS BETWEEN 252 PRECEDING AND CURRENT ROW
            ) AS vix_pct_252d,
            spy.close
              / NULLIF(LAG(spy.close, 60) OVER (ORDER BY spy.date), 0) - 1
              AS spy_drift_60d,
            (tlt.close
              / NULLIF(LAG(tlt.close, 30) OVER (ORDER BY spy.date), 0))
              / NULLIF(spy.close
              / NULLIF(LAG(spy.close, 30) OVER (ORDER BY spy.date), 0), 0) - 1
              AS tlt_spy_ratio_30d
        FROM spy
        LEFT JOIN v_vix vix ON vix.date = spy.date
        LEFT JOIN tlt ON tlt.date = spy.date
    )

    SELECT
        sf.act_symbol,
        u.earnings_date,
        u.timing,
        sf.date AS snapshot_date,
        (u.earnings_date - sf.date) AS lead_days,

        -- Core options
        sf.atm_iv,
        sf.atm_strike,
        sf.atm_strike AS call_strike,
        sf.atm_strike AS put_strike,
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
        sf.call_bid,
        sf.call_ask,
        sf.call_mid,
        sf.call_relative_spread,
        sf.call_volume,
        sf.call_open_interest,
        sf.call_quote_timestamp,
        sf.put_bid,
        sf.put_ask,
        sf.put_mid,
        sf.put_relative_spread,
        sf.put_volume,
        sf.put_open_interest,
        sf.put_quote_timestamp,
        sf.straddle_bid,
        sf.straddle_ask,
        sf.straddle_mid,
        sf.straddle_relative_spread,
        sf.quote_timestamp_precision,
        sf.market_data_mode,
        sf.quote_quality_status,
        sf.liquidity_tier,
        sf.liquidity_tier_method,
        sf.quote_rejection_reason,

        -- Historical earnings stats
        ts.hist_move_avg_4q,
        ts.hist_move_med_4q,
        ts.hist_move_std_4q,
        ts.hist_move_avg_8q,
        ts.hist_move_med_8q,
        ts.hist_move_p75_8q,
        ts.hist_move_p90_8q,
        ts.hist_move_max_12q,
        ts.hist_move_kurt_8q,
        ts.hist_move_last,
        ts.hist_event_count,
        CASE WHEN ts.hist_move_avg_4q > 0
             AND sf.straddle_mid / NULLIF(sf.atm_strike, 0) > 0
             THEN ts.hist_move_avg_4q / (sf.straddle_mid / NULLIF(sf.atm_strike, 0))
             ELSE NULL
        END AS hist_straddle_accuracy,

        -- Market conditions and volatility-history features must match the
        -- training matrix; omitting them silently changes live inference.
        mc.vix_current,
        mc.vix_change_30d,
        mc.vix_pct_252d,
        mc.spy_drift_60d,
        mc.tlt_spy_ratio_30d,
        CASE
            WHEN vh.iv_year_high > vh.iv_year_low
            THEN (vh.iv_current - vh.iv_year_low)
                 / NULLIF(vh.iv_year_high - vh.iv_year_low, 0)
            ELSE NULL
        END AS iv_rank,
        CASE
            WHEN vh.hv_year_high > vh.hv_year_low
            THEN (vh.hv_current - vh.hv_year_low)
                 / NULLIF(vh.hv_year_high - vh.hv_year_low, 0)
            ELSE NULL
        END AS hv_rank,
        (vh.iv_current - vh.iv_week_ago) AS iv_mom_week,
        (vh.iv_current - vh.iv_month_ago) AS iv_mom_month

    FROM upcoming u
    JOIN v_straddle_features sf
        ON sf.act_symbol = u.act_symbol
        AND sf.date < u.earnings_date
        AND (u.earnings_date - sf.date) BETWEEN 1 AND 25
        AND (
            (LOWER(COALESCE(u.timing, '')) = 'amc' AND sf.expiration > u.earnings_date)
            OR (LOWER(COALESCE(u.timing, '')) != 'amc' AND sf.expiration >= u.earnings_date)
        )
    LEFT JOIN v_realized_vol rv
        ON rv.act_symbol = sf.act_symbol AND rv.date = sf.date
    LEFT JOIN event_vol ev
        ON ev.act_symbol = sf.act_symbol
        AND ev.snapshot_date = sf.date
        AND ev.earnings_date = u.earnings_date
    LEFT JOIN trailing_stats ts
        ON ts.act_symbol = u.act_symbol
        AND ts.earnings_date = u.earnings_date
    LEFT JOIN macro mc ON mc.date = sf.date
    LEFT JOIN v_volhist vh
        ON vh.act_symbol = sf.act_symbol AND vh.date = sf.date
    WHERE sf.atm_iv > 0 AND sf.atm_strike > 0
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY sf.act_symbol, sf.date, u.earnings_date
        ORDER BY sf.expiration ASC
    ) = 1
    ORDER BY u.earnings_date, sf.act_symbol, lead_days
    """
    return conn.execute(sql).fetchdf()


def score(df: pd.DataFrame, models: Dict[int, dict]) -> pd.DataFrame:
    """Best-guess expected move plus high/low bands."""
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
        # JSONB column by scripts/import_recent_to_postgres.py.
        #
        # NaN/Inf handling: walk each record dict via _sanitize_for_json to
        # replace non-finite floats with None before json.dumps. pandas'
        # `.where(..., None)` doesn't work here — float64 columns coerce
        # None back to NaN. allow_nan=False then crashes loud on any leak
        # rather than emitting the JSON5-ish `NaN` token that Postgres
        # JSONB rejects.
        feature_records = X.to_dict(orient="records")
        hdf["feature_vector"] = [
            json.dumps(_sanitize_for_json(row), default=str, allow_nan=False)
            for row in feature_records
        ]

        # Point prediction
        pred = np.clip(m["model"].predict(X), 0.0, None)
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

        # Quantile heads are trained independently and can occasionally
        # cross. The target is an absolute move, so enforce nonnegative,
        # monotone bands before persisting or serving them.
        quantile_cols = [
            col for col in ("p10", "p25", "p50", "p75", "p90") if col in hdf
        ]
        if quantile_cols:
            hdf.loc[:, quantile_cols] = rearrange_quantile_array(
                hdf[quantile_cols].to_numpy(dtype=float)
            )

        # Math baselines
        hdf["em_math_pct"] = hdf["straddle_pct"]
        hdf["em_event_vol_pct"] = hdf.get("event_move_implied", np.nan)
        hdf["correction_factor"] = pred / hdf["straddle_pct"].clip(lower=0.001)
        hdf["model_horizon"] = horizon
        hdf["model_bundle_id"] = (
            m.get("bundle_id")
            or (m.get("metadata") or {}).get("version")
            or "unversioned"
        )
        hdf["scored_at"] = datetime.now().isoformat()

        results.append(hdf)

    if not results:
        return pd.DataFrame()

    return pd.concat(results, ignore_index=True)


# Serving (DuckDB view, frontend JSON, Neon import) always reads the newest
# forecasts_YYYY-MM-DD.parquet. Older snapshots are only useful for a short
# debug window; without a cap they accumulate forever on R2 via pull+score+sync.
FORECAST_RETENTION_DAYS = 14
_FORECAST_SNAPSHOT_RE = re.compile(r"^forecasts_(\d{4}-\d{2}-\d{2})\.parquet$")


def prune_forecast_snapshots(
    forecast_dir: Path,
    *,
    keep_days: int = FORECAST_RETENTION_DAYS,
    today: date | None = None,
) -> int:
    """Delete dated forecast snapshots older than keep_days. Returns count removed."""
    cutoff = (today or date.today()) - timedelta(days=keep_days)
    removed = 0
    if not forecast_dir.exists():
        return 0
    for path in forecast_dir.glob("forecasts_*.parquet"):
        match = _FORECAST_SNAPSHOT_RE.match(path.name)
        if not match:
            continue
        try:
            snapshot_day = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if snapshot_day < cutoff:
            path.unlink()
            removed += 1
            logger.info("Pruned stale forecast snapshot %s", path.name)
    return removed


def save_forecasts(
    df: pd.DataFrame,
    data_dir: Path,
    *,
    output_path: Path | None = None,
):
    forecast_dir = data_dir / "forecasts"
    forecast_dir.mkdir(parents=True, exist_ok=True)

    if df.empty:
        logger.warning("No forecasts to save")
        if output_path is None:
            prune_forecast_snapshots(forecast_dir)
        return

    missing_required = sorted(FORECAST_REQUIRED_COLUMNS - set(df.columns))
    if missing_required:
        raise ValueError(
            "Refusing to persist an incomplete forecast artifact; "
            f"missing required columns: {missing_required}"
        )

    out_cols = [
        "act_symbol", "earnings_date", "timing", "snapshot_date",
        "model_horizon", "model_bundle_id", "spot_price", "atm_iv",
        "atm_strike", "call_strike", "put_strike",
        "call_bid", "call_ask", "call_mid", "call_relative_spread",
        "call_volume", "call_open_interest",
        "call_quote_timestamp",
        "put_bid", "put_ask", "put_mid", "put_relative_spread",
        "put_volume", "put_open_interest",
        "put_quote_timestamp",
        "straddle_bid", "straddle_ask", "straddle_mid",
        "straddle_relative_spread", "quote_timestamp_precision",
        "market_data_mode", "quote_quality_status", "liquidity_tier",
        "liquidity_tier_method", "quote_rejection_reason",
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
    serving_key_cols = ["act_symbol", "earnings_date", "snapshot_date", "model_horizon"]
    if all(c in out.columns for c in serving_key_cols):
        duplicate_mask = out.duplicated(serving_key_cols, keep=False)
        if duplicate_mask.any():
            duplicate_rows = int(duplicate_mask.sum())
            unique_keys = int(out.loc[duplicate_mask, serving_key_cols].drop_duplicates().shape[0])
            logger.warning(
                "Forecast output has %d rows sharing %d serving keys; "
                "keeping the first event-covering expiry per key",
                duplicate_rows,
                unique_keys,
            )
            out = out.drop_duplicates(serving_key_cols, keep="first")

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(output_path.suffix + ".tmp")
        out.to_parquet(temporary, index=False)
        temporary.replace(output_path)
        logger.info("Saved %d candidate forecasts → %s", len(out), output_path)
        return

    # Production Parquet
    today = datetime.now().strftime("%Y-%m-%d")
    parquet_path = forecast_dir / f"forecasts_{today}.parquet"
    out.to_parquet(parquet_path, index=False)
    logger.info(f"Saved {len(out)} forecasts → {parquet_path}")
    pruned = prune_forecast_snapshots(forecast_dir)
    if pruned:
        logger.info("Pruned %d forecast snapshot(s) older than %d days", pruned, FORECAST_RETENTION_DAYS)

    # DuckDB
    db_path = os.getenv("DUCKDB_PATH", str(data_dir / "quantiv.duckdb"))
    conn = duckdb.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS ml_forecasts AS SELECT * FROM out WHERE 1=0")
    conn.execute("INSERT INTO ml_forecasts SELECT * FROM out")
    n = conn.execute("SELECT COUNT(*) FROM ml_forecasts").fetchone()[0]
    conn.close()
    logger.info(f"DuckDB ml_forecasts: {n:,} total rows")


def main():
    parser = argparse.ArgumentParser(description="Score upcoming earnings with trained models")
    parser.add_argument("--days-ahead", type=int, default=14)
    parser.add_argument("--models-dir", type=Path, default=None)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Write a candidate snapshot without changing production forecasts or DuckDB.",
    )
    args = parser.parse_args()

    data_dir = get_data_dir()
    models_root = data_dir / "models"
    if args.models_dir is not None:
        models_dir = args.models_dir
    else:
        try:
            models_dir = resolve_champion_bundle(models_root)
            logger.info("Scoring with signed champion bundle %s", models_dir.name)
        except ModelBundleError:
            models_dir = models_root
            logger.warning("No signed champion exists; using repository bootstrap models")

    models = load_models(models_dir)
    if not models:
        logger.error(f"No models found in {models_dir}. Run model_trainer.py first.")
        return

    db_path = os.getenv("DUCKDB_PATH", str(data_dir / "quantiv.duckdb"))
    conn = duckdb.connect(db_path, read_only=False)

    # Optional views: empty stand-ins if daily prices are missing.
    views = [r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
    ).fetchall()]
    if "v_ohlcv" not in views:
        logger.warning("v_ohlcv not found — skipping past-move stats that need daily prices")
        conn.execute("""
            CREATE OR REPLACE VIEW v_ohlcv AS
            SELECT NULL::VARCHAR AS act_symbol, NULL::DATE AS date,
                   NULL::DOUBLE AS close WHERE 1=0
        """)
    if "v_realized_vol" not in views:
        logger.warning("v_realized_vol not found — realized-vol features will be empty")
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
    if "v_vix" not in views:
        vix_path = data_dir / "parquet" / "vix" / "vix.parquet"
        if vix_path.exists():
            conn.execute(f"""
                CREATE OR REPLACE VIEW v_vix AS
                SELECT CAST(date AS DATE) AS date,
                       CAST(vix_close AS DOUBLE) AS vix_close
                FROM read_parquet('{vix_path}')
            """)
        else:
            logger.warning("v_vix not found — VIX/macro features will be empty")
            conn.execute("""
                CREATE OR REPLACE VIEW v_vix AS
                SELECT NULL::DATE AS date, NULL::DOUBLE AS vix_close
                WHERE 1=0
            """)
    if "v_volhist" not in views:
        volhist_glob = (
            data_dir / "parquet" / "volatility_history" / "year=*" / "month=*" / "*.parquet"
        )
        if "v_volhist_raw" in views:
            conn.execute("""
                CREATE OR REPLACE VIEW v_volhist AS
                SELECT * EXCLUDE (month, year) FROM v_volhist_raw
            """)
        elif list(data_dir.glob("parquet/volatility_history/year=*/month=*/*.parquet")):
            conn.execute(f"""
                CREATE OR REPLACE VIEW v_volhist AS
                SELECT
                    CAST(date AS DATE) AS date,
                    act_symbol,
                    CAST(hv_current AS DOUBLE) AS hv_current,
                    CAST(hv_week_ago AS DOUBLE) AS hv_week_ago,
                    CAST(hv_month_ago AS DOUBLE) AS hv_month_ago,
                    CAST(hv_year_high AS DOUBLE) AS hv_year_high,
                    CAST(hv_year_low AS DOUBLE) AS hv_year_low,
                    CAST(iv_current AS DOUBLE) AS iv_current,
                    CAST(iv_week_ago AS DOUBLE) AS iv_week_ago,
                    CAST(iv_month_ago AS DOUBLE) AS iv_month_ago,
                    CAST(iv_year_high AS DOUBLE) AS iv_year_high,
                    CAST(iv_year_low AS DOUBLE) AS iv_year_low
                FROM read_parquet('{volhist_glob}')
            """)
        else:
            logger.warning("v_volhist not found — volatility-history features will be empty")
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

    df = get_upcoming_features(conn, args.days_ahead)
    conn.close()
    logger.info(f"Found {len(df)} feature rows for upcoming earnings")

    if df.empty:
        logger.warning("No upcoming earnings with features found")
        return

    forecasts = score(df, models)
    logger.info(f"Generated {len(forecasts)} forecasts")

    save_forecasts(forecasts, data_dir, output_path=args.output_path)

    if not forecasts.empty:
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
        print("UPCOMING EARNINGS FORECASTS")
        print(f"{'='*90}")
        pd.set_option("display.float_format", "{:.4f}".format)
        pd.set_option("display.max_columns", 20)
        pd.set_option("display.width", 120)
        print(summary.to_string())


if __name__ == "__main__":
    main()
