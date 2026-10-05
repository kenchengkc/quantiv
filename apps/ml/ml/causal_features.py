"""Shared EOD causal features and session-specific realized earnings labels.

Legacy model inputs remain in their original builders. This module only builds
quantiv.earnings-causal.v2 observations from data available by each snapshot.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd

from ml.corporate_actions import adjusted_post_price_sql, ensure_corporate_action_views
from ml.model_protocol import FEATURE_PROTOCOL_CAUSAL, TARGET_PROTOCOL_CAUSAL


@lru_cache(maxsize=1)
def _session_calendar() -> tuple[set[date], date, date]:
    path = Path(__file__).resolve().parents[3] / "config" / "market_sessions.json"
    payload = json.loads(path.read_text())
    if payload.get("schema") != "quantiv.market-sessions.v1":
        raise ValueError("unsupported market-session contract")
    closed = {date.fromisoformat(day) for day in payload.get("holidays", [])}
    if not closed:
        raise ValueError("market-session contract has no covered years")
    first = date(min(closed).year, 1, 1)
    last = date.fromisoformat(payload["source"]["verified_through"])
    return closed, first, last


def reaction_sessions(report_day: date, timing: str | None) -> tuple[date, date] | None:
    """Return exact close boundaries only within the verified session contract."""
    closed, first, last = _session_calendar()
    timing = (timing or "").strip().lower()
    if timing not in ("bmo", "amc") or not first <= report_day <= last:
        return None
    if report_day.weekday() >= 5 or report_day in closed:
        return None

    def adjacent(step: int) -> date:
        day = report_day + timedelta(days=step)
        while day.weekday() >= 5 or day in closed:
            day += timedelta(days=step)
        return day

    pre = adjacent(-1) if timing == "bmo" else report_day
    post = report_day if timing == "bmo" else adjacent(1)
    return (pre, post) if first <= pre and post <= last else None


def canonical_earnings_events(events: pd.DataFrame) -> pd.DataFrame:
    """Prefer a unique known timing over unknown duplicates; reject disagreement."""
    events = events.copy()
    events["earnings_date"] = pd.to_datetime(events["earnings_date"])
    events["timing"] = events["timing"].fillna("").astype(str).str.strip().str.lower()
    known = events.loc[events["timing"].isin(["bmo", "amc"])].drop_duplicates()
    if known.duplicated(["act_symbol", "earnings_date"]).any():
        raise ValueError("conflicting earnings reaction timing")
    events.loc[~events["timing"].isin(["bmo", "amc"]), "timing"] = "unknown"
    events["_unknown_timing"] = ~events["timing"].isin(["bmo", "amc"])
    return (
        events.sort_values("_unknown_timing")
        .drop_duplicates(["act_symbol", "earnings_date"])
        .drop(columns="_unknown_timing")
        .reset_index(drop=True)
    )


def _earnings_events(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return canonical_earnings_events(
        conn.execute(
            "SELECT act_symbol, CAST(date AS DATE) AS earnings_date, timing FROM v_earnings"
        ).fetchdf()
    )


def extract_reaction_labels(
    conn: duckdb.DuckDBPyConnection,
    *,
    as_of_date: date,
    events: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Require exact reaction-session closes; unknown timing has no verified label.

    Availability is the post-price date under the source's EOD contract. Missing
    required sessions are excluded instead of silently substituting a later close.
    """
    ensure_corporate_action_views(conn)
    tables = {row[0] for row in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    coverage_filter = ""
    if "v_corporate_action_coverage" in tables:
        coverage_filter = """AND EXISTS (
            SELECT 1 FROM v_corporate_action_coverage coverage
            WHERE coverage.act_symbol=e.act_symbol
              AND e.pre_price_date>=coverage.window_start
              AND e.post_price_date<=coverage.window_end
        )"""
    elif any(row[2] for row in conn.execute("PRAGMA database_list").fetchall()):
        raise ValueError("causal labeling requires explicit corporate-action coverage")
    events = (
        _earnings_events(conn) if events is None else canonical_earnings_events(events)
    )
    expected = []
    for event in events.itertuples(index=False):
        report_day = pd.Timestamp(event.earnings_date).date()
        boundaries = reaction_sessions(report_day, event.timing)
        if boundaries is None:
            continue
        pre, post = boundaries
        if post <= as_of_date:
            expected.append((event.act_symbol, report_day, event.timing, pre, post))
    frame = pd.DataFrame(
        expected,
        columns=[
            "act_symbol",
            "earnings_date",
            "timing",
            "pre_price_date",
            "post_price_date",
        ],
    )
    for column in ("earnings_date", "pre_price_date", "post_price_date"):
        frame[column] = pd.to_datetime(frame[column])
    frame["act_symbol"] = frame["act_symbol"].astype("string")
    frame["timing"] = frame["timing"].astype("string")
    conn.register("_causal_expected_sessions", frame)
    adjusted = adjusted_post_price_sql(
        symbol="e.act_symbol",
        pre_date="e.pre_price_date",
        post_date="e.post_price_date",
        post_price="post.close",
    )
    try:
        labels = conn.execute(f"""
            SELECT e.*, ABS({adjusted} / pre.close - 1.0) AS realized_move_pct,
                   {adjusted} / pre.close - 1.0 AS signed_realized_move_pct,
                   e.post_price_date AS label_available_at,
                   'ohlcv_session_close' AS label_source,
                   '{TARGET_PROTOCOL_CAUSAL}' AS target_protocol
            FROM _causal_expected_sessions e
            JOIN v_ohlcv pre ON pre.act_symbol=e.act_symbol AND pre.date=e.pre_price_date
            JOIN v_ohlcv post ON post.act_symbol=e.act_symbol AND post.date=e.post_price_date
            WHERE pre.close > 0 AND post.close > 0 {coverage_filter}
        """).fetchdf()
        if labels.duplicated(["act_symbol", "earnings_date"]).any():
            raise ValueError("duplicate reaction-session price observations")
        return labels
    finally:
        conn.unregister("_causal_expected_sessions")


def causal_macro_features(
    conn: duckdb.DuckDBPyConnection, *, as_of_date: date
) -> pd.DataFrame:
    """Rank current VIX within its last 252 chronological observed sessions."""
    return conn.execute(
        """
        WITH vix_ordered AS (
            SELECT date, vix_close, ROW_NUMBER() OVER (ORDER BY date) AS rn
            FROM v_vix WHERE date <= ?::DATE AND vix_close IS NOT NULL
        ), ranked AS (
            SELECT v.date, v.vix_close,
                   CASE WHEN COUNT(*) > 1 THEN
                       COUNT(*) FILTER (WHERE h.vix_close < v.vix_close)::DOUBLE / (COUNT(*) - 1)
                       ELSE 0.0 END AS vix_pct_252d
            FROM vix_ordered v JOIN vix_ordered h ON h.rn BETWEEN v.rn - 251 AND v.rn
            GROUP BY v.date, v.vix_close
        ), spy AS (SELECT date, close FROM v_ohlcv WHERE act_symbol='SPY' AND date <= ?::DATE),
        tlt AS (SELECT date, close FROM v_ohlcv WHERE act_symbol='TLT' AND date <= ?::DATE)
        SELECT spy.date, vix.vix_close AS vix_current,
               vix.vix_close - LAG(vix.vix_close, 21) OVER (ORDER BY spy.date) AS vix_change_30d,
               vix.vix_pct_252d,
               spy.close / NULLIF(LAG(spy.close,60) OVER (ORDER BY spy.date),0) - 1 AS spy_drift_60d,
               (tlt.close / NULLIF(LAG(tlt.close,30) OVER (ORDER BY spy.date),0)) /
                   NULLIF(spy.close / NULLIF(LAG(spy.close,30) OVER (ORDER BY spy.date),0),0) - 1 AS tlt_spy_ratio_30d
        FROM spy LEFT JOIN ranked vix ON vix.date=spy.date
        LEFT JOIN tlt ON tlt.date=spy.date ORDER BY spy.date
    """,
        [as_of_date, as_of_date, as_of_date],
    ).fetchdf()


def build_causal_features(
    conn: duckdb.DuckDBPyConnection,
    *,
    start_date: date,
    end_date: date,
    as_of_date: date,
    require_labels: bool = False,
) -> pd.DataFrame:
    """Use an OHLCV observation spine with optional decision-eligible straddles."""
    events = _earnings_events(conn)
    labels = extract_reaction_labels(conn, as_of_date=as_of_date, events=events)
    macro = causal_macro_features(conn, as_of_date=as_of_date)
    conn.register("_causal_labels", labels)
    conn.register("_causal_macro", macro)
    conn.register("_causal_earnings", events)
    label_join = "JOIN" if require_labels else "LEFT JOIN"
    sql = f"""
    WITH upcoming AS (
        SELECT act_symbol, CAST(earnings_date AS DATE) AS earnings_date, timing
        FROM _causal_earnings WHERE earnings_date BETWEEN DATE '{start_date.isoformat()}' AND DATE '{end_date.isoformat()}'
    ), snapshot_spine AS (
        SELECT u.*, CAST(px.date AS DATE) AS snapshot_date, px.close AS snapshot_close
        FROM upcoming u JOIN v_ohlcv px ON px.act_symbol=u.act_symbol
          AND px.date < u.earnings_date AND (u.earnings_date-CAST(px.date AS DATE)) BETWEEN 1 AND 25
          AND px.date <= DATE '{as_of_date.isoformat()}' AND px.close > 0
    ), trailing_stats AS (
        SELECT sp.act_symbol, sp.earnings_date, sp.snapshot_date, history.*
        FROM snapshot_spine sp LEFT JOIN LATERAL (
            SELECT AVG(realized_move_pct) FILTER (WHERE rn<=4) AS hist_move_avg_4q,
                   MEDIAN(realized_move_pct) FILTER (WHERE rn<=4) AS hist_move_med_4q,
                   STDDEV(realized_move_pct) FILTER (WHERE rn<=4) AS hist_move_std_4q,
                   AVG(realized_move_pct) FILTER (WHERE rn<=8) AS hist_move_avg_8q,
                   MEDIAN(realized_move_pct) FILTER (WHERE rn<=8) AS hist_move_med_8q,
                   QUANTILE_CONT(realized_move_pct,.75) FILTER (WHERE rn<=8) AS hist_move_p75_8q,
                   QUANTILE_CONT(realized_move_pct,.90) FILTER (WHERE rn<=8) AS hist_move_p90_8q,
                   KURTOSIS(realized_move_pct) FILTER (WHERE rn<=8) AS hist_move_kurt_8q,
                   MAX(realized_move_pct) AS hist_move_max_12q,
                   MAX(realized_move_pct) FILTER (WHERE rn=1) AS hist_move_last,
                   COALESCE(MAX(available_events), 0) AS hist_event_count
            FROM (
                SELECT realized_move_pct, ROW_NUMBER() OVER (ORDER BY earnings_date DESC) AS rn,
                       COUNT(*) OVER () AS available_events
                FROM _causal_labels h WHERE h.act_symbol=sp.act_symbol
                  AND h.earnings_date<sp.earnings_date AND h.label_available_at<=sp.snapshot_date
                ORDER BY h.earnings_date DESC LIMIT 12
            ) h
        ) history ON TRUE
    ),
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
            AND (u.earnings_date - CAST(sf.date AS DATE)) BETWEEN 1 AND 25
            AND (
                (u.timing = 'bmo' AND sf.expiration >= u.earnings_date)
                OR (u.timing != 'bmo' AND sf.expiration > u.earnings_date)
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
    )
    SELECT
        sp.act_symbol,
        sp.earnings_date,
        sp.timing,
        sp.snapshot_date,
        (sp.earnings_date - sp.snapshot_date) AS lead_days,

        -- Core options (nullable when no strict straddle exists)
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
        COALESCE(rv.close, sp.snapshot_close, sf.atm_strike) AS spot_price,

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
        (vh.iv_current - vh.iv_month_ago) AS iv_mom_month,
        rv.close * rv.volume AS dollar_volume,
        labels.realized_move_pct,
        labels.pre_price_date,
        labels.post_price_date,
        labels.label_available_at,
        labels.label_source,
        labels.target_protocol,
        CASE WHEN sp.timing IN ('bmo', 'amc') THEN 'reported' ELSE 'unknown' END AS timing_confidence,
        CASE WHEN sf.straddle_mid / NULLIF(sf.atm_strike, 0) > 0
             THEN 'strict_options' ELSE 'optionless' END AS __cohort,
        '{FEATURE_PROTOCOL_CAUSAL}' AS feature_protocol

    FROM snapshot_spine sp
    LEFT JOIN v_straddle_features sf
        ON sf.act_symbol = sp.act_symbol
        AND sf.date = sp.snapshot_date
        AND (
            (sp.timing = 'bmo' AND sf.expiration >= sp.earnings_date)
            OR (sp.timing != 'bmo' AND sf.expiration > sp.earnings_date)
        )
    LEFT JOIN v_realized_vol rv
        ON rv.act_symbol = sp.act_symbol AND rv.date = sp.snapshot_date
    LEFT JOIN event_vol ev
        ON ev.act_symbol = sp.act_symbol
        AND ev.snapshot_date = sp.snapshot_date
        AND ev.earnings_date = sp.earnings_date
    LEFT JOIN trailing_stats ts
        ON ts.act_symbol = sp.act_symbol
        AND ts.earnings_date = sp.earnings_date
        AND ts.snapshot_date = sp.snapshot_date
    LEFT JOIN _causal_macro mc ON mc.date = sp.snapshot_date
    {label_join} _causal_labels labels
      ON labels.act_symbol = sp.act_symbol AND labels.earnings_date = sp.earnings_date
    LEFT JOIN v_volhist vh
        ON vh.act_symbol = sp.act_symbol AND vh.date = sp.snapshot_date
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY sp.act_symbol, sp.snapshot_date, sp.earnings_date
        ORDER BY sf.expiration ASC NULLS LAST
    ) = 1
    ORDER BY sp.earnings_date, sp.act_symbol, lead_days
    """
    try:
        return conn.execute(sql).fetchdf()
    finally:
        conn.unregister("_causal_labels")
        conn.unregister("_causal_macro")
        conn.unregister("_causal_earnings")
