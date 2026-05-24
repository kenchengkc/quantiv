#!/usr/bin/env python3
"""
Feature Engineering v3 — Professional-grade earnings move prediction features.

Key improvements over v2:
  1. IV Event Decomposition — strips diffusive vol from earnings event vol
     using front/back month IV term structure (σ²_event = T*(IV²_front - IV²_back))
  2. Historical earnings move features — per-symbol trailing realized moves
  3. Straddle accuracy history — how well has the straddle predicted past moves?
  4. Extended date range — uses options-only features for 2019-2023 (no OHLCV needed)
  5. Proper realized move from OHLCV where available, options-inferred S_hat otherwise

Target: realized_move_pct = |close_post / close_pre - 1|

Usage:
  python apps/ml/feature_engineering_v3.py
  python apps/ml/feature_engineering_v3.py --start-date 2019-06-01 --end-date 2026-03-31
"""

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

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
    # Held alongside features but not used as a model input. Lets the trainer
    # compute sample weights (e.g. time-decay) without re-querying source data.
    earnings_date: pd.Series = field(default_factory=pd.Series)
    metadata: Dict[str, Any] = field(default_factory=dict)


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data")))


def connect_duckdb() -> duckdb.DuckDBPyConnection:
    db_path = os.getenv("DUCKDB_PATH", str(get_data_dir() / "quantiv.duckdb"))
    conn = duckdb.connect(db_path, read_only=False)
    views = [r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
    ).fetchall()]
    required = ["v_options_raw", "v_earnings", "v_straddle_features"]
    for v in required:
        if v not in views:
            raise RuntimeError(f"Required view '{v}' missing. Run: python scripts/setup_duckdb_from_parquet.py")

    # v_ohlcv and v_realized_vol are optional — create empty stubs if missing
    # so LEFT JOINs work even without OHLCV data
    if "v_ohlcv" not in views:
        logger.warning("v_ohlcv not found — realized moves will use S_hat fallback only")
        conn.execute("""
            CREATE OR REPLACE VIEW v_ohlcv AS
            SELECT NULL::VARCHAR AS act_symbol, NULL::DATE AS date,
                   NULL::DOUBLE AS close, NULL::DOUBLE AS high,
                   NULL::DOUBLE AS low, NULL::DOUBLE AS open
            WHERE 1=0
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
    # v_vix — daily CBOE VIX close from FRED. Used to derive market regime
    # features (vix level, change, percentile rank) at each snapshot date.
    if "v_vix" not in views:
        data_root = get_data_dir()
        vix_path = data_root / "parquet" / "vix" / "vix.parquet"
        if vix_path.exists():
            conn.execute(f"""
                CREATE OR REPLACE VIEW v_vix AS
                SELECT CAST(date AS DATE) AS date,
                       CAST(vix_close AS DOUBLE) AS vix_close
                FROM read_parquet('{vix_path}')
            """)
        else:
            logger.warning("v_vix parquet not found — VIX/macro features will be NULL")
            conn.execute("""
                CREATE OR REPLACE VIEW v_vix AS
                SELECT NULL::DATE AS date, NULL::DOUBLE AS vix_close
                WHERE 1=0
            """)

    # v_volhist — daily HV/IV snapshots (current + week/month/year-ago) joined
    # per (snapshot_date, act_symbol) to give vol-regime context features.
    # Empty stub when the parquet tree isn't present so LEFT JOINs still work.
    if "v_volhist" not in views:
        data_root = get_data_dir()
        volhist_glob = str(data_root / "parquet" / "volatility_history" / "year=*" / "month=*" / "*.parquet")
        import glob as _glob
        if _glob.glob(volhist_glob):
            conn.execute(f"""
                CREATE OR REPLACE VIEW v_volhist AS
                SELECT
                    CAST(date AS DATE) AS date,
                    act_symbol,
                    CAST(hv_current     AS DOUBLE) AS hv_current,
                    CAST(hv_week_ago    AS DOUBLE) AS hv_week_ago,
                    CAST(hv_month_ago   AS DOUBLE) AS hv_month_ago,
                    CAST(hv_year_high   AS DOUBLE) AS hv_year_high,
                    CAST(hv_year_low    AS DOUBLE) AS hv_year_low,
                    CAST(iv_current     AS DOUBLE) AS iv_current,
                    CAST(iv_week_ago    AS DOUBLE) AS iv_week_ago,
                    CAST(iv_month_ago   AS DOUBLE) AS iv_month_ago,
                    CAST(iv_year_high   AS DOUBLE) AS iv_year_high,
                    CAST(iv_year_low    AS DOUBLE) AS iv_year_low
                FROM read_parquet('{volhist_glob}')
            """)
        else:
            logger.warning("v_volhist parquet not found — vol-history features will be NULL")
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
    return conn


def extract_training_data(conn: duckdb.DuckDBPyConnection,
                          start_date: str, end_date: str) -> Dict[int, FeatureSet]:
    """Build feature sets using professional-grade feature engineering.

    Works in two phases:
      Phase A: Options-only features (works for full 2019-2026 range)
      Phase B: OHLCV-enriched features (2023+ only, joined where available)
    """

    logger.info(f"Extracting features: {start_date} → {end_date}")

    sql = """
    WITH earnings AS (
        SELECT
            act_symbol,
            date AS earnings_date,
            timing
        FROM v_earnings
        WHERE date BETWEEN ?::DATE AND ?::DATE
    ),

    -- ═══════════════════════════════════════════════════════════════
    -- REALIZED MOVE: prefer OHLCV close prices, fall back to S_hat
    -- ═══════════════════════════════════════════════════════════════

    -- Method 1: OHLCV-based realized move (most accurate, 2023+)
    pre_close_ohlcv AS (
        SELECT e.act_symbol, e.earnings_date,
               rv.close AS pre_price,
               ROW_NUMBER() OVER (
                   PARTITION BY e.act_symbol, e.earnings_date
                   ORDER BY rv.date DESC
               ) AS rn
        FROM earnings e
        JOIN v_ohlcv rv ON rv.act_symbol = e.act_symbol
            AND rv.date < e.earnings_date
            AND rv.date >= e.earnings_date - INTERVAL '5' DAY
        WHERE rv.close > 0
    ),
    post_close_ohlcv AS (
        SELECT e.act_symbol, e.earnings_date,
               rv.close AS post_price,
               ROW_NUMBER() OVER (
                   PARTITION BY e.act_symbol, e.earnings_date
                   ORDER BY rv.date ASC
               ) AS rn
        FROM earnings e
        JOIN v_ohlcv rv ON rv.act_symbol = e.act_symbol
            AND rv.date > e.earnings_date
            AND rv.date <= e.earnings_date + INTERVAL '5' DAY
        WHERE rv.close > 0
    ),
    realized_ohlcv AS (
        SELECT pre.act_symbol, pre.earnings_date,
               pre.pre_price, post.post_price,
               ABS(post.post_price / NULLIF(pre.pre_price, 0) - 1.0) AS realized_move_pct,
               'ohlcv' AS realized_source
        FROM pre_close_ohlcv pre
        JOIN post_close_ohlcv post USING (act_symbol, earnings_date)
        WHERE pre.rn = 1 AND post.rn = 1
          AND pre.pre_price > 0 AND post.post_price > 0
    ),

    -- Method 2: Options S_hat based realized move (2019-2022 fallback)
    -- S_hat = strike where |call_delta| ≈ 0.5
    s_hat_estimates AS (
        SELECT
            act_symbol,
            date,
            arg_min(strike, ABS(ABS(COALESCE(delta, 0)) - 0.5)) AS s_hat
        FROM v_options_raw
        WHERE call_put = 'Call' AND delta IS NOT NULL AND bid > 0
        GROUP BY act_symbol, date
    ),
    pre_shat AS (
        SELECT e.act_symbol, e.earnings_date,
               s.s_hat AS pre_price,
               ROW_NUMBER() OVER (
                   PARTITION BY e.act_symbol, e.earnings_date
                   ORDER BY s.date DESC
               ) AS rn
        FROM earnings e
        JOIN s_hat_estimates s ON s.act_symbol = e.act_symbol
            AND s.date < e.earnings_date
            AND s.date >= e.earnings_date - INTERVAL '5' DAY
        WHERE s.s_hat > 0
    ),
    post_shat AS (
        SELECT e.act_symbol, e.earnings_date,
               s.s_hat AS post_price,
               ROW_NUMBER() OVER (
                   PARTITION BY e.act_symbol, e.earnings_date
                   ORDER BY s.date ASC
               ) AS rn
        FROM earnings e
        JOIN s_hat_estimates s ON s.act_symbol = e.act_symbol
            AND s.date > e.earnings_date
            AND s.date <= e.earnings_date + INTERVAL '5' DAY
        WHERE s.s_hat > 0
    ),
    realized_shat AS (
        SELECT pre.act_symbol, pre.earnings_date,
               pre.pre_price, post.post_price,
               ABS(post.post_price / NULLIF(pre.pre_price, 0) - 1.0) AS realized_move_pct,
               'shat' AS realized_source
        FROM pre_shat pre
        JOIN post_shat post USING (act_symbol, earnings_date)
        WHERE pre.rn = 1 AND post.rn = 1
          AND pre.pre_price > 0 AND post.post_price > 0
    ),

    -- Combine: prefer OHLCV, fall back to S_hat
    realized AS (
        SELECT * FROM realized_ohlcv
        UNION ALL
        SELECT * FROM realized_shat
        WHERE (act_symbol, earnings_date) NOT IN (
            SELECT act_symbol, earnings_date FROM realized_ohlcv
        )
    ),

    -- ═══════════════════════════════════════════════════════════════
    -- HISTORICAL EARNINGS FEATURES (per symbol trailing history)
    -- ═══════════════════════════════════════════════════════════════
    earnings_history AS (
        SELECT
            act_symbol,
            earnings_date,
            realized_move_pct,
            ROW_NUMBER() OVER (
                PARTITION BY act_symbol ORDER BY earnings_date
            ) AS event_num
        FROM realized
    ),
    trailing_stats AS (
        SELECT
            h.act_symbol,
            h.earnings_date,
            -- Last 4 earnings
            AVG(prev.realized_move_pct) FILTER (
                WHERE prev.event_num >= h.event_num - 4 AND prev.event_num < h.event_num
            ) AS hist_move_avg_4q,
            MEDIAN(prev.realized_move_pct) FILTER (
                WHERE prev.event_num >= h.event_num - 4 AND prev.event_num < h.event_num
            ) AS hist_move_med_4q,
            STDDEV(prev.realized_move_pct) FILTER (
                WHERE prev.event_num >= h.event_num - 4 AND prev.event_num < h.event_num
            ) AS hist_move_std_4q,
            -- Last 8 earnings
            AVG(prev.realized_move_pct) FILTER (
                WHERE prev.event_num >= h.event_num - 8 AND prev.event_num < h.event_num
            ) AS hist_move_avg_8q,
            MEDIAN(prev.realized_move_pct) FILTER (
                WHERE prev.event_num >= h.event_num - 8 AND prev.event_num < h.event_num
            ) AS hist_move_med_8q,
            -- Count of historical events available
            COUNT(prev.realized_move_pct) FILTER (
                WHERE prev.event_num < h.event_num
            ) AS hist_event_count
        FROM earnings_history h
        LEFT JOIN earnings_history prev
            ON prev.act_symbol = h.act_symbol
            AND prev.event_num < h.event_num
        GROUP BY h.act_symbol, h.earnings_date
    ),

    -- ═══════════════════════════════════════════════════════════════
    -- IV EVENT DECOMPOSITION
    -- Strips diffusive vol from earnings event vol using term structure.
    -- σ²_event_daily = IV²_front - IV²_back (annualized IVs squared)
    -- event_move = σ_event * √(DTE_front / 365)
    -- ═══════════════════════════════════════════════════════════════
    event_vol AS (
        SELECT
            sf.act_symbol,
            sf.date AS snapshot_date,
            e.earnings_date,
            sf.atm_iv AS front_iv,
            sf.dte AS front_dte,
            sf.expiration AS front_expiry,
            -- Find back-month IV (next expiry after the front one)
            back.atm_iv AS back_iv,
            back.dte AS back_dte,
            -- Event vol decomposition (professional term-structure method)
            -- Total variance = σ²_diffusive × T + σ²_event
            -- Front expiry contains event: IV²_front × T_front = σ²_diff × T_front + σ²_event
            -- Back expiry is pure diffusive: IV²_back × T_back = σ²_diff × T_back
            -- Therefore: σ²_event = IV²_front × T_front - IV²_back × T_front
            --                     ≈ (IV²_front - IV²_back) × T_front
            -- event_move = √(σ²_event)
            CASE
                WHEN sf.atm_iv > 0 AND back.atm_iv > 0
                     AND sf.atm_iv > back.atm_iv
                THEN SQRT(
                    GREATEST(0,
                        sf.atm_iv * sf.atm_iv * (sf.dte / 365.0)
                        - back.atm_iv * back.atm_iv * (back.dte / 365.0)
                    )
                )
                ELSE NULL
            END AS event_move_implied,
            -- IV crush: how much IV will drop post-earnings
            CASE
                WHEN sf.atm_iv > 0 AND back.atm_iv > 0
                THEN (sf.atm_iv - back.atm_iv) / sf.atm_iv
                ELSE NULL
            END AS iv_crush_pct
        FROM v_straddle_features sf
        JOIN earnings e
            ON e.act_symbol = sf.act_symbol
            AND sf.date < e.earnings_date
            AND (e.earnings_date - sf.date) BETWEEN 1 AND 25
            AND (
                (LOWER(COALESCE(e.timing, '')) = 'amc' AND sf.expiration > e.earnings_date)
                OR (LOWER(COALESCE(e.timing, '')) != 'amc' AND sf.expiration >= e.earnings_date)
            )
        LEFT JOIN v_straddle_features back
            ON back.act_symbol = sf.act_symbol
            AND back.date = sf.date
            AND back.expiration > sf.expiration
            AND back.dte > sf.dte
            AND back.atm_iv > 0
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY sf.act_symbol, sf.date, e.earnings_date
            ORDER BY sf.expiration ASC, back.dte ASC
        ) = 1
    ),

    -- Market regime: VIX level + 30d change + 252d percentile, plus SPY
    -- 60d drift and TLT/SPY 30d ratio (risk-off proxy). LightGBM uses
    -- these to condition predictions on the prevailing vol regime.
    macro AS (
        WITH spy AS (SELECT date, close FROM v_ohlcv WHERE act_symbol = 'SPY'),
             tlt AS (SELECT date, close FROM v_ohlcv WHERE act_symbol = 'TLT')
        SELECT
            spy.date,
            vix.vix_close                                AS vix_current,
            vix.vix_close
              - LAG(vix.vix_close, 21) OVER (ORDER BY spy.date) AS vix_change_30d,
            PERCENT_RANK() OVER (
                ORDER BY vix.vix_close
                ROWS BETWEEN 252 PRECEDING AND CURRENT ROW
            )                                            AS vix_pct_252d,
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
        LEFT JOIN tlt        ON tlt.date = spy.date
    ),

    -- ═══════════════════════════════════════════════════════════════
    -- MAIN FEATURE ASSEMBLY
    -- Uses v_straddle_features (options-only, full 2019-2026 range)
    -- LEFT JOINs v_realized_vol (OHLCV, 2023+ only — NULL pre-2023)
    -- ═══════════════════════════════════════════════════════════════
    snapshots AS (
        SELECT
            sf.act_symbol,
            e.earnings_date,
            e.timing,
            sf.date AS snapshot_date,
            (e.earnings_date - sf.date) AS lead_days,

            -- Core options features (always available)
            sf.atm_iv,
            sf.straddle_mid / NULLIF(sf.atm_strike, 0) AS straddle_pct,
            sf.em_iv / NULLIF(sf.atm_strike, 0) AS em_iv_pct,
            sf.dte,

            -- IV event decomposition
            ev.event_move_implied,
            ev.iv_crush_pct,
            ev.front_iv,
            ev.back_iv,

            -- Straddle vs event vol ratio
            CASE WHEN ev.event_move_implied > 0 AND sf.atm_strike > 0
                 THEN ev.event_move_implied / (sf.straddle_mid / NULLIF(sf.atm_strike, 0))
                 ELSE NULL
            END AS event_vol_fraction,

            -- Realized vol features (NULL for pre-2023 data, LightGBM handles NaN)
            rv.parkinson_rv_10d,
            rv.parkinson_rv_20d,
            rv.parkinson_rv_60d,
            rv.cc_rv_10d,
            rv.cc_rv_20d,
            rv.vol_of_vol_20d,

            -- IV / RV ratios (NULL pre-2023)
            sf.atm_iv / NULLIF(rv.parkinson_rv_20d, 0) AS iv_rv_ratio_20d,
            sf.atm_iv / NULLIF(rv.parkinson_rv_60d, 0) AS iv_rv_ratio_60d,
            sf.atm_iv / NULLIF(rv.cc_rv_20d, 0)        AS iv_cc_rv_ratio_20d,
            rv.parkinson_rv_10d / NULLIF(rv.parkinson_rv_60d, 0) AS rv_term_ratio,

            -- Market context (NULL pre-2023)
            rv.volume_ratio_20d,
            rv.drift_5d,
            COALESCE(rv.close, sf.atm_strike) AS spot_price,

            -- Historical earnings features
            ts.hist_move_avg_4q,
            ts.hist_move_med_4q,
            ts.hist_move_std_4q,
            ts.hist_move_avg_8q,
            ts.hist_move_med_8q,
            ts.hist_event_count,

            -- Straddle accuracy: how well did straddle predict past moves?
            CASE WHEN ts.hist_move_avg_4q > 0
                 AND sf.straddle_mid / NULLIF(sf.atm_strike, 0) > 0
                 THEN ts.hist_move_avg_4q / (sf.straddle_mid / NULLIF(sf.atm_strike, 0))
                 ELSE NULL
            END AS hist_straddle_accuracy,

            -- Market regime (VIX + SPY/TLT macro context)
            mc.vix_current,
            mc.vix_change_30d,
            mc.vix_pct_252d,
            mc.spy_drift_60d,
            mc.tlt_spy_ratio_30d,

            -- Volatility regime (from v_volhist; NULL pre-2019 or where missing)
            CASE
                WHEN vh.iv_year_high > vh.iv_year_low
                THEN (vh.iv_current - vh.iv_year_low) / NULLIF(vh.iv_year_high - vh.iv_year_low, 0)
                ELSE NULL
            END AS iv_rank,
            CASE
                WHEN vh.hv_year_high > vh.hv_year_low
                THEN (vh.hv_current - vh.hv_year_low) / NULLIF(vh.hv_year_high - vh.hv_year_low, 0)
                ELSE NULL
            END AS hv_rank,
            (vh.iv_current - vh.iv_week_ago)  AS iv_mom_week,
            (vh.iv_current - vh.iv_month_ago) AS iv_mom_month,

            -- Target
            r.realized_move_pct,
            r.realized_source
        FROM earnings e
        JOIN v_straddle_features sf
            ON sf.act_symbol = e.act_symbol
            AND sf.date < e.earnings_date
            AND (e.earnings_date - sf.date) BETWEEN 1 AND 25
            AND (
                (LOWER(COALESCE(e.timing, '')) = 'amc' AND sf.expiration > e.earnings_date)
                OR (LOWER(COALESCE(e.timing, '')) != 'amc' AND sf.expiration >= e.earnings_date)
            )
        JOIN realized r
            ON r.act_symbol = e.act_symbol
            AND r.earnings_date = e.earnings_date
        LEFT JOIN v_realized_vol rv
            ON rv.act_symbol = sf.act_symbol
            AND rv.date = sf.date
        LEFT JOIN event_vol ev
            ON ev.act_symbol = sf.act_symbol
            AND ev.snapshot_date = sf.date
            AND ev.earnings_date = e.earnings_date
        LEFT JOIN trailing_stats ts
            ON ts.act_symbol = e.act_symbol
            AND ts.earnings_date = e.earnings_date
        LEFT JOIN v_volhist vh
            ON vh.act_symbol = sf.act_symbol
            AND vh.date = sf.date
        LEFT JOIN macro mc
            ON mc.date = sf.date
        WHERE sf.atm_iv > 0 AND sf.atm_strike > 0
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY sf.act_symbol, sf.date, e.earnings_date
            ORDER BY sf.expiration ASC
        ) = 1
    )

    SELECT * FROM snapshots
    ORDER BY act_symbol, earnings_date, lead_days
    """

    df = conn.execute(sql, [start_date, end_date]).fetchdf()
    logger.info(f"Raw snapshot rows: {len(df):,}")

    if df.empty:
        logger.warning("No data returned — check that options + OHLCV + earnings overlap")
        return {}

    logger.info(f"Realized move sources: {df['realized_source'].value_counts().to_dict()}")

    # ---------- Build per-horizon feature sets ----------
    feature_sets: Dict[int, FeatureSet] = {}

    for horizon in HORIZONS:
        hdf = df[df["lead_days"] == horizon].copy()
        if len(hdf) < 30:
            logger.warning(f"T-{horizon}: only {len(hdf)} samples, skipping (need ≥30)")
            continue

        feature_cols = [
            # Core options
            "atm_iv", "straddle_pct", "em_iv_pct", "dte",
            # Event vol decomposition (professional-grade)
            "event_move_implied", "iv_crush_pct", "event_vol_fraction",
            # Realized vol (Parkinson + close-to-close)
            "parkinson_rv_10d", "parkinson_rv_20d", "parkinson_rv_60d",
            "cc_rv_10d", "cc_rv_20d", "vol_of_vol_20d",
            # IV / RV ratios (vol risk premium)
            "iv_rv_ratio_20d", "iv_rv_ratio_60d", "iv_cc_rv_ratio_20d",
            "rv_term_ratio",
            # Market context
            "volume_ratio_20d", "drift_5d",
            # Historical earnings pattern
            "hist_move_avg_4q", "hist_move_med_4q", "hist_move_std_4q",
            "hist_move_avg_8q", "hist_move_med_8q", "hist_event_count",
            "hist_straddle_accuracy",
            # Vol regime (from v_volhist — NULL if volatility_history parquet absent)
            "iv_rank", "hv_rank", "iv_mom_week", "iv_mom_month",
            # Market regime (from VIX + SPY/TLT — NULL pre-1990 or pre-2023)
            "vix_current", "vix_change_30d", "vix_pct_252d",
            "spy_drift_60d", "tlt_spy_ratio_30d",
        ]

        hdf["log_spot"] = np.log(hdf["spot_price"].clip(lower=1))
        hdf["timing_bmo"] = (hdf["timing"] == "bmo").astype(float)
        hdf["timing_amc"] = (hdf["timing"] == "amc").astype(float)
        hdf["earnings_month"] = pd.to_datetime(hdf["earnings_date"]).dt.month
        hdf["earnings_dow"] = pd.to_datetime(hdf["earnings_date"]).dt.dayofweek

        feature_cols += ["log_spot", "timing_bmo", "timing_amc",
                         "earnings_month", "earnings_dow"]

        X = hdf[feature_cols].copy()
        y = hdf["realized_move_pct"].copy()
        ed = pd.to_datetime(hdf["earnings_date"]).dt.date

        # LightGBM handles NaN natively — only remove inf and extreme outliers
        X = X.replace([np.inf, -np.inf], np.nan)
        valid = y.notna() & (y > 0) & (y < 1.0)
        X = X[valid].reset_index(drop=True)
        y = y[valid].reset_index(drop=True)
        ed = ed[valid].reset_index(drop=True)

        if len(X) < 30:
            logger.warning(f"T-{horizon}: only {len(X)} valid samples after cleaning")
            continue

        nan_cols = X.isna().mean()
        mostly_null = nan_cols[nan_cols > 0.5]
        if len(mostly_null) > 0:
            logger.info(f"T-{horizon}: high-NaN columns (will be handled by LightGBM): "
                        f"{mostly_null.index.tolist()}")

        feature_sets[horizon] = FeatureSet(
            horizon=horizon,
            features=X,
            target=y,
            earnings_date=ed,
            metadata={
                "n_samples": len(X),
                "target_mean": float(y.mean()),
                "target_median": float(y.median()),
                "target_std": float(y.std()),
                "target_p10": float(y.quantile(0.10)),
                "target_p90": float(y.quantile(0.90)),
                "straddle_pct_mean": float(X["straddle_pct"].mean()),
                "event_move_coverage": float(X["event_move_implied"].notna().mean()),
                "hist_move_coverage": float(X["hist_move_avg_4q"].notna().mean()),
                "ohlcv_coverage": float(
                    (hdf[valid]["realized_source"] == "ohlcv").mean()
                    if "realized_source" in hdf.columns else 0
                ),
                "feature_cols": feature_cols,
            }
        )
        logger.info(
            f"T-{horizon}: {len(X)} samples, "
            f"target mean={y.mean():.4f}, med={y.median():.4f}, "
            f"event_vol coverage={X['event_move_implied'].notna().mean():.0%}, "
            f"hist_move coverage={X['hist_move_avg_4q'].notna().mean():.0%}"
        )

    return feature_sets


def save_training_data(feature_sets: Dict[int, FeatureSet], output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    for horizon, fs in feature_sets.items():
        training_df = fs.features.copy()
        training_df["target"] = fs.target
        # Sidecar — used by model_trainer_v3 to derive sample weights.
        # Trainer pops it before fitting; never seen by LightGBM as a feature.
        if len(fs.earnings_date) == len(fs.features):
            training_df["__earnings_date"] = fs.earnings_date.values
        path = output_dir / f"training_T{horizon}.parquet"
        training_df.to_parquet(path, index=False)

        meta_path = output_dir / f"metadata_T{horizon}.json"
        with open(meta_path, "w") as f:
            json.dump(fs.metadata, f, indent=2)

        logger.info(f"Saved T-{horizon}: {len(training_df)} rows → {path}")


def main():
    parser = argparse.ArgumentParser(description="Extract ML training features (v3)")
    parser.add_argument("--start-date", default="2019-06-01",
                        help="Start date (default: 2019-06-01 to allow trailing history)")
    parser.add_argument("--end-date", default="2026-03-31")
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

    print(f"\n{'='*70}")
    print("TRAINING DATA SUMMARY (v3)")
    print(f"{'='*70}")
    for h, fs in sorted(feature_sets.items()):
        m = fs.metadata
        print(f"  T-{h:2d}: {m['n_samples']:5d} samples | "
              f"target μ={m['target_mean']:.4f} med={m['target_median']:.4f} | "
              f"straddle%={m['straddle_pct_mean']:.4f} | "
              f"event_vol={m['event_move_coverage']:.0%} | "
              f"hist_move={m['hist_move_coverage']:.0%}")


if __name__ == "__main__":
    main()
