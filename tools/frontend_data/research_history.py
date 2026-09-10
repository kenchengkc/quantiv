"""Build the historical research universe directly from analytical DuckDB views.

This module deliberately does not consume per-symbol frontend payloads. Display
history is allowed to stay small while the research universe spans every
canonical historical event for which the analytical source has decision-eligible
pre-event options evidence and a timing-aware realized price window.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ml.corporate_actions import adjusted_post_price_sql, ensure_corporate_action_views

from .shared import jsonable

SCHEMA = "quantiv.historical-event-universe.v1"
TRANSFORMATION_VERSION = "source-level-v1"
EVIDENCE_RULE = (
    "decision_eligible_eod pre-event straddle paired with timing-aware, "
    "corporate-action-normalized realized close-to-close move"
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _surprise(actual: Any, estimate: Any) -> float | None:
    a = _finite(actual)
    e = _finite(estimate)
    if a is None or e is None or e == 0:
        return None
    return (a - e) / abs(e)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _research_timing(timing: Any, timing_source: Any) -> str:
    """Use only point-in-time reported timing for historical session selection.

    The canonical table may infer missing timing from reports on both sides of an
    event. That is useful for display, but allowing a future report to choose an
    old event's price/options window would create a historical look-ahead. Inferred
    timings therefore use the conservative unknown-session rule here.
    """
    normalized = str(timing or "unknown")
    source = str(timing_source or "unknown")
    return normalized if source == "reported" else "unknown"


def _event_rows(conn, cutoff: date) -> list[tuple[Any, ...]]:
    ensure_corporate_action_views(conn)
    adjusted_post = adjusted_post_price_sql(
        symbol="e.ticker",
        pre_date="pre.pre_date",
        post_date="post.post_date",
        post_price="post.post_price",
    )
    return conn.execute(
        f"""
        WITH ranked_events AS (
            SELECT
                *,
                COUNT(*) OVER (PARTITION BY ticker, earnings_dt) AS source_row_count,
                ROW_NUMBER() OVER (
                    PARTITION BY ticker, earnings_dt
                    ORDER BY
                        CASE WHEN timing_source = 'reported' THEN 0 ELSE 1 END,
                        CASE WHEN eps_actual IS NOT NULL THEN 0 ELSE 1 END,
                        CASE WHEN revenue_actual IS NOT NULL THEN 0 ELSE 1 END,
                        source
                ) AS event_rank
            FROM earnings_events
            WHERE earnings_dt < ?
        ), events AS (
            SELECT
                ticker,
                earnings_dt,
                timing AS canonical_timing,
                timing_source,
                CASE WHEN timing_source = 'reported' THEN timing ELSE 'unknown' END AS research_timing,
                source,
                source_row_count,
                fiscal_year,
                fiscal_q,
                eps_actual,
                eps_estimate,
                revenue_actual,
                revenue_estimate
            FROM ranked_events
            WHERE event_rank = 1
        )
        SELECT
            e.ticker,
            e.earnings_dt,
            e.canonical_timing,
            e.timing_source,
            e.research_timing,
            e.source,
            e.source_row_count,
            e.fiscal_year,
            e.fiscal_q,
            e.eps_actual,
            e.eps_estimate,
            e.revenue_actual,
            e.revenue_estimate,
            opt.as_of_date,
            opt.expiry_date,
            opt.dte,
            DATE_DIFF('day', opt.as_of_date, e.earnings_dt) AS lead_time_days,
            opt.atm_strike,
            opt.straddle_mid,
            opt.straddle_pct,
            opt.atm_iv,
            opt.quote_quality_status,
            opt.call_quote_timestamp,
            opt.put_quote_timestamp,
            opt.straddle_relative_spread,
            opt.atm_delta_distance,
            pre.pre_date,
            pre.pre_price,
            post.post_date,
            post.post_price,
            {adjusted_post} AS adjusted_post_price,
            (
                SELECT COUNT(*) FROM v_splits s
                WHERE s.act_symbol = e.ticker
                  AND pre.pre_date IS NOT NULL
                  AND post.post_date IS NOT NULL
                  AND s.ex_date > pre.pre_date
                  AND s.ex_date <= post.post_date
            ) AS split_actions,
            (
                SELECT COUNT(*) FROM v_dividends d
                WHERE d.act_symbol = e.ticker
                  AND pre.pre_date IS NOT NULL
                  AND post.post_date IS NOT NULL
                  AND d.ex_date > pre.pre_date
                  AND d.ex_date <= post.post_date
            ) AS dividend_actions
        FROM events e
        LEFT JOIN LATERAL (
            SELECT s.*
            FROM v_eligible_straddles s
            WHERE s.ticker = e.ticker
              AND s.as_of_date >= e.earnings_dt - INTERVAL '14' DAY
              AND (
                  (e.research_timing = 'after_market_close'
                   AND s.as_of_date <= e.earnings_dt
                   AND s.expiry_date > e.earnings_dt)
                  OR
                  (e.research_timing <> 'after_market_close'
                   AND s.as_of_date < e.earnings_dt
                   AND s.expiry_date >= e.earnings_dt)
              )
            ORDER BY s.as_of_date DESC, s.expiry_date, s.atm_delta_distance
            LIMIT 1
        ) opt ON TRUE
        LEFT JOIN LATERAL (
            SELECT p.date AS pre_date, p.close AS pre_price
            FROM v_ohlcv p
            WHERE p.act_symbol = e.ticker
              AND p.close > 0
              AND p.date >= e.earnings_dt - INTERVAL '5' DAY
              AND (
                  (e.research_timing = 'after_market_close' AND p.date <= e.earnings_dt)
                  OR
                  (e.research_timing <> 'after_market_close' AND p.date < e.earnings_dt)
              )
            ORDER BY p.date DESC
            LIMIT 1
        ) pre ON TRUE
        LEFT JOIN LATERAL (
            SELECT p.date AS post_date, p.close AS post_price
            FROM v_ohlcv p
            WHERE p.act_symbol = e.ticker
              AND p.close > 0
              AND p.date <= e.earnings_dt + INTERVAL '5' DAY
              AND (
                  (e.research_timing = 'before_market_open' AND p.date >= e.earnings_dt)
                  OR
                  (e.research_timing <> 'before_market_open' AND p.date > e.earnings_dt)
              )
            ORDER BY p.date ASC
            LIMIT 1
        ) post ON TRUE
        ORDER BY e.earnings_dt DESC, e.ticker
        """,
        [cutoff],
    ).fetchall()


def _source_snapshot(conn, cutoff: date, *, source_revision: str | None) -> dict[str, Any]:
    earnings = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ticker || '|' || CAST(earnings_dt AS VARCHAR)) "
        "FROM earnings_events WHERE earnings_dt < ?",
        [cutoff],
    ).fetchone()
    options = conn.execute(
        "SELECT COUNT(*), MIN(as_of_date), MAX(as_of_date) FROM v_eligible_straddles"
    ).fetchone()
    prices = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM v_ohlcv").fetchone()
    return {
        "kind": "analytical_duckdb",
        "completeness": "source_level",
        "as_of_date": cutoff.isoformat(),
        "source_revision": source_revision,
        "transformation": "tools/frontend_data/research_history.py",
        "transformation_version": TRANSFORMATION_VERSION,
        "earnings_rows_before_cutoff": int(earnings[0] or 0),
        "canonical_event_identities_before_cutoff": int(earnings[1] or 0),
        "eligible_straddle_rows": int(options[0] or 0),
        "eligible_straddle_as_of_min": _iso(options[1]),
        "eligible_straddle_as_of_max": _iso(options[2]),
        "ohlcv_rows": int(prices[0] or 0),
        "ohlcv_date_min": _iso(prices[1]),
        "ohlcv_date_max": _iso(prices[2]),
    }


def build_historical_event_universe(
    conn,
    as_of_date: date,
    *,
    source_revision: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic source-level historical research payload."""
    rows = _event_rows(conn, as_of_date)
    events: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    exclusion_counts: Counter[str] = Counter()

    for row in rows:
        (
            ticker,
            earnings_dt,
            canonical_timing,
            timing_source,
            research_timing,
            event_source,
            source_row_count,
            fiscal_year,
            fiscal_q,
            eps_actual,
            eps_estimate,
            revenue_actual,
            revenue_estimate,
            implied_as_of,
            implied_expiration,
            implied_dte,
            implied_lead_days,
            implied_atm_strike,
            implied_straddle_abs,
            implied,
            implied_atm_iv,
            implied_quality_status,
            call_quote_timestamp,
            put_quote_timestamp,
            straddle_relative_spread,
            atm_delta_distance,
            pre_date,
            pre_price,
            post_date,
            post_price,
            adjusted_post_price,
            split_actions,
            dividend_actions,
        ) = row

        reasons: list[str] = []
        implied_value = _finite(implied)
        pre_value = _finite(pre_price)
        adjusted_post_value = _finite(adjusted_post_price)
        if implied_as_of is None or implied_value is None or implied_value <= 0:
            reasons.append("missing_eligible_pre_event_straddle")
        elif implied_quality_status != "decision_eligible_eod":
            reasons.append("option_quote_quality_not_decision_eligible")
        if pre_date is None or post_date is None or pre_value is None or adjusted_post_value is None:
            reasons.append("missing_realized_price_window")
        elif pre_value <= 0 or adjusted_post_value <= 0:
            reasons.append("invalid_realized_price_window")

        if reasons:
            for reason in reasons:
                exclusion_counts[reason] += 1
            exclusions.append(
                {
                    "ticker": str(ticker),
                    "date": earnings_dt.isoformat(),
                    "reasons": reasons,
                    "timing": str(research_timing or "unknown"),
                    "timing_source": str(timing_source or "unknown"),
                    "source_row_count": int(source_row_count or 1),
                }
            )
            continue

        actual = adjusted_post_value / pre_value - 1.0
        if not math.isfinite(actual):
            exclusion_counts["nonfinite_realized_move"] += 1
            exclusions.append(
                {
                    "ticker": str(ticker),
                    "date": earnings_dt.isoformat(),
                    "reasons": ["nonfinite_realized_move"],
                    "timing": str(research_timing or "unknown"),
                    "timing_source": str(timing_source or "unknown"),
                    "source_row_count": int(source_row_count or 1),
                }
            )
            continue

        realized_abs = abs(actual)
        event_id = _sha256(f"{ticker}|{earnings_dt.isoformat()}".encode())
        events.append(
            {
                "event_id": event_id,
                "ticker": str(ticker),
                "date": earnings_dt.isoformat(),
                "timing": str(research_timing or "unknown"),
                "canonical_timing": str(canonical_timing or "unknown"),
                "timing_source": str(timing_source or "unknown"),
                "fiscal_year": int(fiscal_year) if fiscal_year is not None else None,
                "fiscal_q": str(fiscal_q).upper() if fiscal_q else None,
                "actual": jsonable(actual),
                "realized_abs": jsonable(realized_abs),
                "implied": jsonable(implied_value),
                "implied_as_of": implied_as_of.isoformat(),
                "implied_expiration": _iso(implied_expiration),
                "implied_dte": int(implied_dte) if implied_dte is not None else None,
                "implied_lead_days": int(implied_lead_days) if implied_lead_days is not None else None,
                "implied_atm_strike": jsonable(implied_atm_strike),
                "implied_straddle_abs": jsonable(implied_straddle_abs),
                "implied_atm_iv": jsonable(implied_atm_iv),
                "implied_quality_status": "decision_eligible_eod",
                "eps_surprise_pct": jsonable(_surprise(eps_actual, eps_estimate)),
                "rev_surprise_pct": jsonable(_surprise(revenue_actual, revenue_estimate)),
                "edge": jsonable(realized_abs - implied_value),
                "ratio": jsonable(realized_abs / implied_value),
                "outside_implied": bool(realized_abs > implied_value),
                "event_provenance": {
                    "source": str(event_source or "unknown"),
                    "source_row_count": int(source_row_count or 1),
                    "availability_timestamp": None,
                    "availability_status": "not_available_in_canonical_source",
                },
                "option_observation": {
                    "as_of_date": implied_as_of.isoformat(),
                    "expiration": _iso(implied_expiration),
                    "dte": int(implied_dte) if implied_dte is not None else None,
                    "lead_days": int(implied_lead_days) if implied_lead_days is not None else None,
                    "call_quote_timestamp": _iso(call_quote_timestamp),
                    "put_quote_timestamp": _iso(put_quote_timestamp),
                    "straddle_relative_spread": jsonable(straddle_relative_spread),
                    "atm_delta_distance": jsonable(atm_delta_distance),
                },
                "realized_window": {
                    "pre_date": pre_date.isoformat(),
                    "pre_price": jsonable(pre_value),
                    "post_date": post_date.isoformat(),
                    "post_price_raw": jsonable(post_price),
                    "post_price_adjusted": jsonable(adjusted_post_value),
                    "adjustment_method": "split_and_cash_dividend_total_return_normalization",
                    "split_actions": int(split_actions or 0),
                    "dividend_actions": int(dividend_actions or 0),
                },
            }
        )

    events.sort(key=lambda item: (item["date"], item["ticker"]), reverse=True)
    exclusions.sort(key=lambda item: (item["date"], item["ticker"]), reverse=True)
    identities = [(item["ticker"], item["date"]) for item in events]
    if len(identities) != len(set(identities)):
        raise ValueError("historical research universe contains duplicate event identities")

    source = _source_snapshot(conn, as_of_date, source_revision=source_revision)
    audit = {
        "candidate_event_count": len(rows),
        "eligible_event_count": len(events),
        "excluded_event_count": len(exclusions),
        "source_duplicate_rows_collapsed": sum(max(0, int(row[6] or 1) - 1) for row in rows),
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
        "exclusions": exclusions,
    }
    if audit["candidate_event_count"] != audit["eligible_event_count"] + audit["excluded_event_count"]:
        raise AssertionError("research universe coverage does not reconcile")

    identity = {
        "schema": SCHEMA,
        "source": source,
        "evidence_rule": EVIDENCE_RULE,
        "decision_scope": "end_of_day_research",
        "live_trading_eligible": False,
        "event_count": len(events),
        "audit": audit,
        "events": events,
    }
    return {
        **identity,
        "universe_id": _sha256(_canonical_json(identity)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_historical_event_universe(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)
