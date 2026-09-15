"""Build the calendar, screener, and per-symbol public payloads."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

from fiscal_calendar import display_fiscal_year, load_fiscal_year_naming
from math_baseline import compute_em_math

from .forecast_artifacts import ml_fields, provider_event_fields
from .shared import WEEK_OFFSETS, jsonable

def build_screener_payload(
    as_of_date: date,
    this_monday: date,
    week_payloads: dict[date, dict],
) -> dict:
    """Merge week JSON events in calendar order; first row wins on duplicate (ticker, date)."""
    seen: set[tuple[str, str]] = set()
    events: list[dict] = []
    for off in WEEK_OFFSETS:
        wk_start = this_monday + timedelta(days=7 * off)
        payload = week_payloads.get(wk_start)
        if not payload:
            continue
        for ev in payload.get("events", []):
            t = ev.get("ticker") or ""
            ed = ev.get("earnings_date") or ""
            if not t or not ed:
                continue
            key = (t, ed)
            if key in seen:
                continue
            seen.add(key)
            events.append(ev)
    return {
        "metadata": {
            "version": "v1",
            "as_of_date": as_of_date.isoformat(),
            "generated_at": datetime.now().isoformat(),
            "event_count": len(events),
            "week_starts": [
                (this_monday + timedelta(days=7 * off)).isoformat() for off in WEEK_OFFSETS
            ],
        },
        "events": events,
    }



def _surprise_pct(actual, estimate):
    """Surprise % = (actual - estimate) / |estimate|. Signed.
    None when either side is missing or estimate is zero. Uses |estimate|
    so a beat against a negative estimate still reads as positive."""
    if actual is None or estimate is None:
        return None
    try:
        a, e = float(actual), float(estimate)
    except (TypeError, ValueError):
        return None
    if math.isnan(a) or math.isnan(e) or e == 0:
        return None
    return (a - e) / abs(e)


# Curated ticker -> fiscal-year naming offset (see tools/fiscal_calendar.py).
# Loaded once; empty when the config is absent so the build is unaffected.
_FY_NAMING = load_fiscal_year_naming()


def _corrected_fiscal_year(symbol, fiscal_year, source):
    """Vendor labels (Finnhub) name a fiscal year by its END year; some
    retailers name it by the START year. Apply the curated per-ticker offset,
    but only to vendor-end-year rows (source contains 'finnhub'). DoltHub-only
    rows use a different (calendar-ordinal) labeling and are left untouched."""
    if fiscal_year is None:
        return None
    if not (source and "finnhub" in str(source).lower()):
        return fiscal_year
    try:
        return display_fiscal_year(symbol, int(fiscal_year), _FY_NAMING)
    except (TypeError, ValueError):
        return fiscal_year


def _quarter_label(fiscal_year, fiscal_q, fallback_date):
    """Render 'Q3 24' from a (already naming-corrected) fiscal_year/fiscal_q.
    Falls back to a calendar-month inference when either piece is missing —
    correct for ~70% of names, wrong for non-calendar-year filers, but
    no worse than the current frontend default."""
    fy = fiscal_year
    fq = (fiscal_q or "").upper() if isinstance(fiscal_q, str) else None
    if not fq or fq not in {"Q1", "Q2", "Q3", "Q4"}:
        month = fallback_date.month
        fq = f"Q{(month - 1) // 3 + 1}"
    if fy is None:
        fy = fallback_date.year
    try:
        yy = str(int(fy) % 100).zfill(2)
    except (TypeError, ValueError):
        yy = str(fallback_date.year % 100).zfill(2)
    return f"{fq} {yy}"


def _historical_option_evidence(conn, ticker: str, cutoff: date) -> dict[date, dict]:
    """Select the latest eligible pre-event ATM straddle for recent earnings."""
    rows = conn.execute(
        """
        WITH recent_events AS (
            SELECT ticker, earnings_dt, timing
            FROM earnings_events
            WHERE ticker = ? AND earnings_dt < ?
            ORDER BY earnings_dt DESC
            LIMIT 12
        )
        SELECT
            e.earnings_dt,
            selected.as_of_date,
            selected.expiry_date,
            selected.dte,
            DATE_DIFF('day', selected.as_of_date, e.earnings_dt) AS lead_time_days,
            selected.atm_strike,
            selected.straddle_mid,
            selected.straddle_pct,
            selected.atm_iv,
            selected.quote_quality_status
        FROM recent_events e
        LEFT JOIN LATERAL (
            SELECT s.*
            FROM v_eligible_straddles s
            WHERE s.ticker = e.ticker
              AND s.as_of_date >= e.earnings_dt - INTERVAL '14' DAY
              AND (
                  ((LOWER(COALESCE(e.timing, '')) IN (
                        'after_market_close', 'amc', 'after_close'
                    ) OR LOWER(COALESCE(e.timing, '')) LIKE '%after%')
                   AND s.as_of_date <= e.earnings_dt
                   AND s.expiry_date > e.earnings_dt)
                  OR
                  (NOT (
                        LOWER(COALESCE(e.timing, '')) IN (
                            'after_market_close', 'amc', 'after_close'
                        ) OR LOWER(COALESCE(e.timing, '')) LIKE '%after%'
                   )
                   AND s.as_of_date < e.earnings_dt
                   AND s.expiry_date >= e.earnings_dt)
              )
            ORDER BY s.as_of_date DESC, s.expiry_date
            LIMIT 1
        ) selected ON TRUE
        """,
        [ticker, cutoff],
    ).fetchall()
    return {
        earnings_date: {
            "implied": jsonable(straddle_pct),
            "implied_as_of": source_date.isoformat() if source_date else None,
            "implied_expiration": expiration.isoformat() if expiration else None,
            "implied_dte": int(dte) if dte is not None else None,
            "implied_lead_days": int(lead_days) if lead_days is not None else None,
            "implied_atm_strike": jsonable(atm_strike),
            "implied_straddle_abs": jsonable(straddle_mid),
            "implied_atm_iv": jsonable(atm_iv),
            "implied_quality_status": quality_status,
        }
        for (
            earnings_date,
            source_date,
            expiration,
            dte,
            lead_days,
            atm_strike,
            straddle_mid,
            straddle_pct,
            atm_iv,
            quality_status,
        ) in rows
        if straddle_pct is not None
    }


def _history_row(d, timing, fiscal_year, fiscal_q, eps_actual, eps_estimate,
                 rev_actual, rev_estimate, actual_move, symbol=None, source=None,
                 option_evidence=None):
    """Shape one realized earnings event with point-in-time options evidence."""
    fy = _corrected_fiscal_year(symbol, fiscal_year, source)
    evidence = option_evidence or {}
    return {
        "date": d.isoformat(),
        "timing": timing,
        "q": _quarter_label(fy, fiscal_q, d),
        "fiscal_year": int(fy) if fy is not None else None,
        "fiscal_q": (fiscal_q or "").upper() or None,
        # Signed close-to-close realized move (e.g. +0.034 = +3.4%).
        # NULL when OHLCV doesn't bracket the event.
        "actual": jsonable(actual_move),
        "implied": evidence.get("implied"),
        "implied_as_of": evidence.get("implied_as_of"),
        "implied_expiration": evidence.get("implied_expiration"),
        "implied_dte": evidence.get("implied_dte"),
        "implied_lead_days": evidence.get("implied_lead_days"),
        "implied_atm_strike": evidence.get("implied_atm_strike"),
        "implied_straddle_abs": evidence.get("implied_straddle_abs"),
        "implied_atm_iv": evidence.get("implied_atm_iv"),
        "implied_quality_status": evidence.get("implied_quality_status"),
        "eps_actual": jsonable(eps_actual),
        "eps_estimate": jsonable(eps_estimate),
        "eps_surprise_pct": jsonable(_surprise_pct(eps_actual, eps_estimate)),
        "revenue_actual": jsonable(rev_actual),
        "revenue_estimate": jsonable(rev_estimate),
        "rev_surprise_pct": jsonable(_surprise_pct(rev_actual, rev_estimate)),
    }


def build_symbol_detail(conn, ticker: str, as_of_date: date, earnings_dt: date | None,
                        ml_lookup: dict[tuple[str, str], dict] | None = None,
                        provider_lookup: dict[str, dict] | None = None) -> dict | None:
    """Build per-symbol detail: implied moves across expiries + term structure."""
    eligible_pairs = conn.execute(
        """
        SELECT
            expiry_date, dte, atm_strike, atm_iv,
            call_iv, put_iv, straddle_mid, straddle_pct,
            call_delta, call_gamma, call_vega, call_theta
        FROM v_eligible_straddles
        WHERE ticker = ? AND as_of_date = ?
          AND expiry_date > ?
          AND expiry_date <= ?
        ORDER BY expiry_date
        LIMIT 6
        """,
        [ticker, as_of_date, as_of_date, as_of_date + timedelta(days=120)],
    ).fetchall()

    if not eligible_pairs:
        return None

    # The source does not carry underlying spot, so the nearest eligible
    # delta-balanced strike is the explicit EOD spot proxy.
    spot = float(eligible_pairs[0][2])

    straddles = []
    for (
        exp,
        dte,
        atm_strike,
        atm_iv,
        call_iv,
        put_iv,
        straddle_mid,
        straddle_pct,
        call_delta,
        call_gamma,
        call_vega,
        call_theta,
    ) in eligible_pairs:
        em_iv_abs = (
            atm_strike * atm_iv * math.sqrt(dte / 365.0)
            if atm_strike and atm_iv and dte > 0
            else None
        )

        straddles.append({
            "expiration": exp.isoformat(),
            "dte": dte,
            "atm_strike": jsonable(atm_strike),
            "atm_iv": jsonable(atm_iv),
            "atm_call_iv": jsonable(call_iv),
            "atm_put_iv": jsonable(put_iv),
            "straddle_mid": jsonable(straddle_mid),
            "em_straddle": jsonable(straddle_mid),
            "em_straddle_pct": jsonable(straddle_pct),
            "em_iv": jsonable(em_iv_abs),
            "em_iv_pct": jsonable((em_iv_abs / atm_strike) if (em_iv_abs and atm_strike) else None),
            "call_delta": jsonable(call_delta),
            "call_gamma": jsonable(call_gamma),
            "call_vega": jsonable(call_vega),
            "call_theta": jsonable(call_theta),
        })

    if not straddles:
        return None

    # Earnings history (last 12 events) with signed close-to-close realized
    # moves where OHLCV coverage permits. The LEFT JOIN ensures we still
    # return history rows when v_ohlcv can't bracket the event — those
    # rows just have actual=NULL and the chart's dot won't render. The
    # window is ±5 calendar days so weekends/holidays don't drop events.
    #
    # Bracket is timing-aware (Finnhub-grade timing once overlay has run):
    #   BMO  → pre = last close BEFORE earnings_dt, post = close ON OR AFTER earnings_dt
    #   AMC  → pre = close ON OR BEFORE earnings_dt, post = first close AFTER earnings_dt
    #   DMH / unknown → symmetric (pre <, post >) — original behavior.
    # ROW_NUMBER picks the latest pre and earliest post per earnings_dt
    # (NULLS LAST keeps the bare row if no OHLCV exists).
    #
    # Also pulls EPS/revenue actual+estimate (populated by the Finnhub
    # overlay) so the historical chart can color realized-move dots by
    # surprise and surface fundamentals in the tooltip.
    try:
        history = conn.execute(
            """
            SELECT
                e.earnings_dt,
                e.timing,
                e.fiscal_year,
                e.fiscal_q,
                e.eps_actual,
                e.eps_estimate,
                e.revenue_actual,
                e.revenue_estimate,
                (post.close / NULLIF(pre.close, 0) - 1.0) AS actual,
                e.source
            FROM earnings_events e
            LEFT JOIN v_ohlcv pre  ON pre.act_symbol = e.ticker
                AND pre.date >= e.earnings_dt - INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date <= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date < e.earnings_dt)
                )
            LEFT JOIN v_ohlcv post ON post.act_symbol = e.ticker
                AND post.date <= e.earnings_dt + INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date >= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date > e.earnings_dt)
                )
            WHERE e.ticker = ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY e.earnings_dt
                ORDER BY pre.date DESC NULLS LAST, post.date ASC NULLS LAST
            ) = 1
            ORDER BY e.earnings_dt DESC
            LIMIT 12
            """,
            [ticker],
        ).fetchall()
    except Exception:
        # v_ohlcv may not exist for this build; fall back to bare history.
        history = [
            (d, t, fy, fq, epa, epe, rva, rve, None, src)
            for d, t, fy, fq, epa, epe, rva, rve, src in conn.execute(
                """
                SELECT earnings_dt, timing, fiscal_year, fiscal_q,
                       eps_actual, eps_estimate, revenue_actual, revenue_estimate, source
                FROM earnings_events
                WHERE ticker = ?
                ORDER BY earnings_dt DESC
                LIMIT 12
                """,
                [ticker],
            ).fetchall()
        ]

    historical_options = _historical_option_evidence(conn, ticker, as_of_date)

    # Use the closest post-earnings expiry as the headline expected move
    em = None
    if earnings_dt:
        timing_row = conn.execute(
            """
            SELECT timing FROM earnings_events
            WHERE ticker = ? AND earnings_dt = ?
            LIMIT 1
            """,
            [ticker, earnings_dt],
        ).fetchone()
        earnings_timing = timing_row[0] if timing_row else None
        em_math_data = compute_em_math(
            conn,
            ticker,
            as_of_date,
            earnings_dt,
            earnings_timing,
        )
        if em_math_data:
            em = {
                "earnings_date": earnings_dt.isoformat(),
                "expiration": em_math_data["expiry_date"],
                "dte": em_math_data["days_to_expiry"],
                "lead_time_days": em_math_data["lead_time_days"],
                "atm_strike": jsonable(em_math_data["atm_strike"]),
                "atm_iv": jsonable(em_math_data["avg_iv"]),
                "straddle_abs": jsonable(em_math_data["straddle_price"]),
                "straddle_pct": jsonable(em_math_data["em_baseline_straddle"]),
                "iv_pct": jsonable(em_math_data["em_baseline_iv"]),
                "skew_atm": jsonable(em_math_data.get("skew_atm")),
                "term_slope": jsonable(em_math_data.get("term_slope")),
                "total_vega": jsonable(em_math_data.get("total_vega")),
            }
            fc = (ml_lookup or {}).get((ticker, earnings_dt.isoformat()))
            ml = ml_fields(fc)
            if ml:
                em.update(ml)
                em["em_method"] = "ml_lightgbm"
            else:
                em["em_method"] = "options_math"

    # A generic near-term straddle is useful only when there is no known event.
    # It must never stand in for an earnings expected move if no eligible expiry
    # spans that event.
    if em is None and straddles and earnings_dt is None:
        near = straddles[0]
        em = {
            "expiration": near["expiration"],
            "dte": near["dte"],
            "atm_strike": near["atm_strike"],
            "atm_iv": near["atm_iv"],
            "straddle_abs": near["em_straddle"],
            "straddle_pct": near["em_straddle_pct"],
            "iv_pct": near["em_iv_pct"],
        }

    # Pull volatility-regime snapshot for the current as-of date. May not exist
    # for every ticker (coverage depends on volatility_history parquet).
    vol_regime = None
    try:
        vh = conn.execute(
            """
            SELECT iv_current, iv_year_high, iv_year_low,
                   hv_current, hv_year_high, hv_year_low,
                   iv_week_ago, iv_month_ago
            FROM v_volhist
            WHERE act_symbol = ? AND date = ?
            LIMIT 1
            """,
            [ticker, as_of_date],
        ).fetchone()
        if vh:
            iv_c, iv_hi, iv_lo, hv_c, hv_hi, hv_lo, iv_wk, iv_mo = vh
            def _rank(cur, hi, lo):
                if cur is None or hi is None or lo is None or hi <= lo:
                    return None
                return max(0.0, min(1.0, (cur - lo) / (hi - lo)))
            vol_regime = {
                "iv_current": jsonable(iv_c),
                "iv_rank":    jsonable(_rank(iv_c, iv_hi, iv_lo)),
                "iv_year_high": jsonable(iv_hi),
                "iv_year_low":  jsonable(iv_lo),
                "hv_current": jsonable(hv_c),
                "hv_rank":    jsonable(_rank(hv_c, hv_hi, hv_lo)),
                "iv_mom_week":  jsonable((iv_c - iv_wk) if (iv_c is not None and iv_wk is not None) else None),
                "iv_mom_month": jsonable((iv_c - iv_mo) if (iv_c is not None and iv_mo is not None) else None),
            }
    except Exception:
        # v_volhist may not exist if volatility_history parquet hasn't been synced yet
        pass

    provider_fields = provider_event_fields((provider_lookup or {}).get(ticker))
    return {
        "symbol": ticker,
        "as_of_date": as_of_date.isoformat(),
        "spot_price": jsonable(spot),
        "expected_move": em,
        "straddle_features": straddles,
        "earnings_history": [
            _history_row(
                d,
                t,
                fy,
                fq,
                epa,
                epe,
                rva,
                rve,
                a,
                symbol=ticker,
                source=src,
                option_evidence=historical_options.get(d),
            )
            for d, t, fy, fq, epa, epe, rva, rve, a, src in history
        ],
        "next_earnings": earnings_dt.isoformat() if earnings_dt else None,
        "vol_regime": vol_regime,
        **provider_fields,
    }


def screener_extras(conn, ticker: str, earnings_dt: date, as_of_date: date) -> dict:
    """Extra per-event fields the screener needs but the standard event payload
    doesn't carry: IV rank, last-4-quarter realized average, IV crush proxy.

    All three are best-effort — return None for any field that can't be
    computed. The screener degrades gracefully on missing fields.
    """
    extras: dict = {
        "iv_rank": None,
        "hist_move_avg_4q": None,
        "iv_crush_pct": None,
    }

    # IV rank from v_volhist
    try:
        vh = conn.execute(
            """
            SELECT iv_current, iv_year_high, iv_year_low
            FROM v_volhist
            WHERE act_symbol = ? AND date = ?
            LIMIT 1
            """,
            [ticker, as_of_date],
        ).fetchone()
        if vh:
            cur, hi, lo = vh
            if cur is not None and hi is not None and lo is not None and hi > lo:
                extras["iv_rank"] = jsonable(max(0.0, min(1.0, (cur - lo) / (hi - lo))))
    except Exception:
        pass

    # Hist average realized move — last 4 earnings × OHLCV close-to-close.
    # Uses the canonical `earnings_events` table (built by build_earnings_events_table)
    # joined to v_ohlcv. Both must exist for this to produce a value; the
    # try/except suppresses errors when either is missing.
    try:
        rows = conn.execute(
            """
            SELECT ABS(post.close / NULLIF(pre.close, 0) - 1.0) AS realized
            FROM earnings_events e
            JOIN v_ohlcv pre  ON pre.act_symbol = e.ticker
                AND pre.date >= e.earnings_dt - INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date <= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date < e.earnings_dt)
                )
            JOIN v_ohlcv post ON post.act_symbol = e.ticker
                AND post.date <= e.earnings_dt + INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date >= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date > e.earnings_dt)
                )
            WHERE e.ticker = ? AND e.earnings_dt < ? AND pre.close > 0 AND post.close > 0
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY e.earnings_dt ORDER BY pre.date DESC, post.date ASC
            ) = 1
            ORDER BY e.earnings_dt DESC LIMIT 4
            """,
            [ticker, earnings_dt],
        ).fetchall()
        if rows:
            vals = [r[0] for r in rows if r[0] is not None]
            if vals:
                extras["hist_move_avg_4q"] = jsonable(sum(vals) / len(vals))
    except Exception as e:
        print(f"  ⚠ hist_move_avg_4q failed for {ticker}: {e}", flush=True)

    # IV crush proxy — front ATM IV vs the next-out expiry's ATM IV.
    # Already computed in v_straddle_features as iv_crush_pct upstream;
    # we recompute here from raw options to avoid coupling to that view.
    try:
        front = conn.execute(
            """
            SELECT expiry_date, iv FROM v_options_chain
            WHERE ticker = ? AND as_of_date = ? AND call_put = 'C'
              AND iv IS NOT NULL AND iv > 0
              AND expiry_date >= ?
            ORDER BY expiry_date ASC, ABS(delta - 0.5) ASC
            LIMIT 1
            """,
            [ticker, as_of_date, earnings_dt],
        ).fetchone()
        if front:
            front_expiry = front[0]
            front_iv = float(front[1])
            back = conn.execute(
                """
                SELECT iv FROM v_options_chain
                WHERE ticker = ? AND as_of_date = ? AND call_put = 'C'
                  AND iv IS NOT NULL AND iv > 0
                  AND expiry_date > ?
                ORDER BY expiry_date ASC, ABS(delta - 0.5) ASC
                LIMIT 1
                """,
                [ticker, as_of_date, front_expiry],
            ).fetchone()
            if back:
                back_iv = float(back[0])
                if front_iv > 0:
                    extras["iv_crush_pct"] = jsonable((front_iv - back_iv) / front_iv)
    except Exception:
        pass

    return extras


def collapse_duplicate_earnings(conn, start: date, end: date):
    """Pick one canonical earnings date per ticker across the forward window, so
    a ticker whose estimated date was revised (e.g. PLUS 2026-05-20 → 05-27 →
    05-28) shows on a single day instead of two or three.

    Partitions by ticker only (NOT ticker+fiscal_q): a revised estimate can be
    tagged with a different/blank fiscal quarter (e.g. M's 05-26 vs 06-03), so
    keying on the quarter would fail to merge them. Within this ~25-day window a
    ticker has at most one real print, so per-ticker collapse is safe.

    Rule (matches the confirmed report date 5/5 on known cases):
      1. prefer the row that already has reported actuals (the confirmed date),
      2. otherwise the latest date (the most recent revision).
    Returns (keep, dropped): keep is the set of (ticker, iso_date) to retain;
    dropped is [(ticker, dropped_iso, kept_iso)] for the build log.
    """
    rows = conn.execute(
        """
        WITH base AS (
            SELECT ticker, earnings_dt, eps_actual, revenue_actual
            FROM earnings_events
            WHERE earnings_dt BETWEEN ? AND ?
        ),
        ranked AS (
            SELECT ticker, earnings_dt,
                ROW_NUMBER() OVER (
                    PARTITION BY ticker
                    ORDER BY
                        ((NULLIF(CAST(eps_actual AS VARCHAR), '') IS NOT NULL)
                          OR (NULLIF(CAST(revenue_actual AS VARCHAR), '') IS NOT NULL)) DESC,
                        earnings_dt DESC
                ) AS rn
            FROM base
        )
        SELECT ticker, earnings_dt, rn FROM ranked
        """,
        [start, end],
    ).fetchall()
    keep: set[tuple[str, str]] = set()
    kept_iso: dict[str, str] = {}
    for ticker, earnings_dt, rn in rows:
        if rn == 1:
            keep.add((ticker, earnings_dt.isoformat()))
            kept_iso[ticker] = earnings_dt.isoformat()
    dropped = [
        (ticker, earnings_dt.isoformat(), kept_iso.get(ticker, "?"))
        for ticker, earnings_dt, rn in rows
        if rn != 1
    ]
    return keep, dropped


def load_published_calendar_events(public_dir: Path) -> list[tuple[str, date, str]]:
    """Ticker, date, and session from calendar-reference.json.

    Missing or unreadable files fail closed to an empty list so a first-run
    build (no reference published yet) keeps research-only symbol dates.
    """
    path = public_dir / "calendar-reference.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    events: list[tuple[str, date, str]] = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        ticker = str(event.get("ticker") or "").strip().upper()
        earnings_date = str(event.get("earnings_date") or "")[:10]
        if not ticker or len(earnings_date) != 10:
            continue
        try:
            earn_dt = date.fromisoformat(earnings_date)
        except ValueError:
            continue
        timing = str(event.get("timing") or "unknown")
        events.append((ticker, earn_dt, timing))
    return events


def load_published_calendar_keys(public_dir: Path) -> set[tuple[str, str]]:
    """Ticker/date identities already shown by calendar-reference.json.

    Missing or unreadable files fail closed to an empty set so a first-run
    build (no reference published yet) keeps the historical ML-coverage gate
    instead of widening the research universe.
    """
    return {
        (ticker, earn_dt.isoformat())
        for ticker, earn_dt, _timing in load_published_calendar_events(public_dir)
    }


def published_symbol_dates(
    events: list[tuple[str, date, str]],
    today: date,
) -> dict[str, tuple[date, str]]:
    """Soonest upcoming published print per ticker; else the latest past date.

    Homepage membership is calendar-reference. Symbol pages and the watchlist
    Reports column used DuckDB's next_earnings, which can lag a revision and
    then render as a negative countdown while the calendar still lists the
    name next week.
    """
    latest: dict[str, tuple[date, str]] = {}
    upcoming: dict[str, tuple[date, str]] = {}
    for ticker, earn_dt, timing in events:
        prev_latest = latest.get(ticker)
        if prev_latest is None or earn_dt > prev_latest[0]:
            latest[ticker] = (earn_dt, timing)
        if earn_dt >= today:
            prev_upcoming = upcoming.get(ticker)
            if prev_upcoming is None or earn_dt < prev_upcoming[0]:
                upcoming[ticker] = (earn_dt, timing)
    return {**latest, **upcoming}


def apply_published_symbol_dates(
    tickers_needing_detail: dict[str, date | None],
    published: dict[str, tuple[date, str]],
) -> None:
    """Overwrite (or add) per-ticker detail dates from the published calendar."""
    for ticker, (earn_dt, _timing) in published.items():
        tickers_needing_detail[ticker] = earn_dt


def align_symbol_detail_to_published(
    detail: dict,
    published_date: date | None,
    published_timing: str | None = None,
) -> dict:
    """Force next_earnings onto the published date; drop a mismatched EM.

    An expected move is event-identity-specific. If research scored a
    superseded print, attaching it to the new calendar date would show a
    forecast for a different event.
    """
    if not detail or published_date is None:
        return detail
    iso = published_date.isoformat()
    detail["next_earnings"] = iso
    if published_timing:
        detail["next_earnings_timing"] = published_timing
    em = detail.get("expected_move")
    em_date = str((em or {}).get("earnings_date") or "")[:10]
    if em and em_date != iso:
        detail["expected_move"] = None
    elif em and published_timing:
        em["timing"] = published_timing
    return detail


def ml_gate_drops_event(
    ticker: str,
    earnings_iso: str,
    today: date,
    ml_lookup: dict[tuple[str, str], dict],
    require_ml: bool,
    published: set[tuple[str, str]] | None = None,
) -> bool:
    """Whether the ML-coverage gate should discard this event entirely.

    daily_score.py keys a forecast to the earnings date it scored. When a
    provider revises that date the forecast stays keyed to the superseded one,
    so it can never be reused: its horizon anchors the old date. Requiring ML
    then discarded the event outright — which used to mean "don't show it", but
    calendar-reference is now authoritative for membership, so the row is
    published anyway and simply renders with no expected move at all.

    An event the calendar already publishes is therefore never dropped here. It
    falls through to the options-math baseline priced at its current date, which
    is the same evidence the surrounding weeks publish when ML is unavailable.
    Events outside the published calendar keep the strict gate, so an unscored
    event does not quietly widen the research universe.
    """
    if not require_ml:
        return False
    if ml_lookup.get((ticker, earnings_iso)) is not None:
        return False
    # Past earnings are never scored (daily_score only covers upcoming events),
    # so their absence from ml_lookup is expected rather than a coverage gap.
    if earnings_iso < today.isoformat():
        return False
    return (ticker, earnings_iso) not in (published or set())


def build_week_events(conn, as_of_date: date, week_start: date, week_end: date,
                      ml_lookup: dict[tuple[str, str], dict],
                      provider_lookup: dict[str, dict] | None = None,
                      require_ml: bool = True,
                      canonical: set[tuple[str, str]] | None = None,
                      published: set[tuple[str, str]] | None = None) -> list[dict]:
    # realized_move: signed regular-session close-to-close move ACROSS the
    # print, for events already reported. Same timing-aware bracket as
    # build_symbol_detail (BMO → prev close→report-day close; AMC → report-day
    # close→next close). Regular-session OHLCV only, so it excludes the
    # pre/after-hours IEX prints the live quote can include. NULL for upcoming
    # events (no post close yet) — the calendar then shows the live tick.
    try:
        rows = conn.execute(
            """
            SELECT e.ticker, e.earnings_dt, e.timing, e.fiscal_q,
                   e.eps_actual, e.eps_estimate, e.revenue_actual, e.revenue_estimate,
                   (post.close / NULLIF(pre.close, 0) - 1.0) AS realized_move
            FROM earnings_events e
            LEFT JOIN v_ohlcv pre  ON pre.act_symbol = e.ticker
                AND pre.date >= e.earnings_dt - INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date <= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date < e.earnings_dt)
                )
            LEFT JOIN v_ohlcv post ON post.act_symbol = e.ticker
                AND post.date <= e.earnings_dt + INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date >= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date > e.earnings_dt)
                )
            WHERE e.earnings_dt BETWEEN ? AND ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY e.ticker, e.earnings_dt
                ORDER BY pre.date DESC NULLS LAST, post.date ASC NULLS LAST
            ) = 1
            ORDER BY e.earnings_dt, e.ticker
            """,
            [week_start, week_end],
        ).fetchall()
    except Exception:
        # v_ohlcv absent (e.g. options-only build) — fall back without moves.
        rows = [
            (*r, None) for r in conn.execute(
                """
                SELECT ticker, earnings_dt, timing, fiscal_q,
                       eps_actual, eps_estimate, revenue_actual, revenue_estimate
                FROM earnings_events
                WHERE earnings_dt BETWEEN ? AND ?
                ORDER BY earnings_dt, ticker
                """,
                [week_start, week_end],
            ).fetchall()
        ]

    events = []
    skipped_no_ml = 0
    skipped_no_options = 0
    skipped_dupe = 0
    # Named so the build log says which published calendar rows will render
    # without an expected move, instead of only an aggregate skip count.
    published_without_ml: list[str] = []
    published_without_options: list[str] = []
    today = date.today()
    for i, (
        ticker,
        earnings_dt,
        timing,
        fiscal_q,
        eps_actual,
        eps_estimate,
        revenue_actual,
        revenue_estimate,
        realized_move,
    ) in enumerate(rows, 1):
        # Drop revised/duplicate dates: keep only the canonical date chosen per
        # (ticker, fiscal quarter) by collapse_duplicate_earnings.
        if canonical is not None and (ticker, earnings_dt.isoformat()) not in canonical:
            skipped_dupe += 1
            continue
        earnings_iso = earnings_dt.isoformat()
        fc = ml_lookup.get((ticker, earnings_iso))
        if ml_gate_drops_event(ticker, earnings_iso, today, ml_lookup, require_ml, published):
            skipped_no_ml += 1
            continue
        em = compute_em_math(conn, ticker, as_of_date, earnings_dt, timing)
        is_published = (ticker, earnings_iso) in (published or set())
        if not em:
            if is_published:
                published_without_options.append(f"{ticker} {earnings_iso}")
                extras = screener_extras(conn, ticker, earnings_dt, as_of_date)
                provider_fields = provider_event_fields(
                    (provider_lookup or {}).get(str(ticker).upper())
                )
                # Keep the published identity so realized move / EPS can overlay
                # even when the expected move can no longer be recomputed.
                events.append({
                    "ticker": ticker,
                    "earnings_date": earnings_iso,
                    "timing": timing or "unknown",
                    "fiscal_q": fiscal_q,
                    "eps_actual": jsonable(eps_actual),
                    "eps_estimate": jsonable(eps_estimate),
                    "revenue_actual": jsonable(revenue_actual),
                    "revenue_estimate": jsonable(revenue_estimate),
                    "realized_move_pct": jsonable(realized_move),
                    "as_of_date": as_of_date.isoformat(),
                    "em_method": None,
                    "em_straddle_pct": None,
                    "em_iv_pct": None,
                    **extras,
                    **provider_fields,
                })
            else:
                skipped_no_options += 1
            continue
        if fc is None and is_published:
            published_without_ml.append(f"{ticker} {earnings_iso}")
        if i % 25 == 0 or i == len(rows):
            print(f"    event {i}/{len(rows)}: {ticker} {earnings_dt}", flush=True)
        ml = ml_fields(fc)
        extras = screener_extras(conn, ticker, earnings_dt, as_of_date)
        provider_fields = provider_event_fields((provider_lookup or {}).get(str(ticker).upper()))
        events.append({
            "ticker": ticker,
            "earnings_date": earnings_iso,
            "timing": timing or "unknown",
            "fiscal_q": fiscal_q,
            "eps_actual": jsonable(eps_actual),
            "eps_estimate": jsonable(eps_estimate),
            "revenue_actual": jsonable(revenue_actual),
            "revenue_estimate": jsonable(revenue_estimate),
            # Signed regular-session close-to-close move across the print, for
            # already-reported events. The calendar shows this (clearly marked
            # as the earnings-day reaction) instead of the live tick once a
            # reporter's date has passed. NULL for upcoming events.
            "realized_move_pct": jsonable(realized_move),
            "as_of_date": as_of_date.isoformat(),
            "spot_price": jsonable(em["estimated_spot"]),
            "atm_strike": jsonable(em["atm_strike"]),
            "atm_iv": jsonable(em["avg_iv"]),
            "em_straddle_pct": jsonable(em["em_baseline_straddle"]),
            "em_iv_pct": jsonable(em["em_baseline_iv"]),
            "em_straddle_abs": jsonable(em["straddle_price"]),
            "expiry_date": em["expiry_date"],
            "days_to_expiry": em["days_to_expiry"],
            "lead_time_days": em["lead_time_days"],
            "skew_atm": jsonable(em.get("skew_atm")),
            "term_slope": jsonable(em.get("term_slope")),
            "em_method": "ml_lightgbm" if ml else "options_math",
            "confidence": "high",
            **extras,
            **provider_fields,
            **ml,
        })
    events.sort(key=lambda e: (e["earnings_date"], e["ticker"]))
    if skipped_no_ml or skipped_no_options or skipped_dupe:
        print(
            f"    skipped: {skipped_no_ml} (no ML forecast), "
            f"{skipped_no_options} (no options data), "
            f"{skipped_dupe} (duplicate revised date)",
            flush=True,
        )
    if published_without_ml:
        print(
            f"    published calendar rows priced from options math only "
            f"({len(published_without_ml)}): {', '.join(published_without_ml)}",
            flush=True,
        )
    if published_without_options:
        print(
            f"    ⚠ published calendar rows with NO expected move "
            f"({len(published_without_options)}): {', '.join(published_without_options)}",
            flush=True,
        )
    return events


def week_em_averages(events: list[dict]) -> tuple[float, float]:
    """Mean straddle/IV expected moves for a week payload.

    Dates-only published rows omit a priced expected move (or set it to null)
    once the print has passed. The week summary still has to build.
    """
    straddles = [
        event["em_straddle_pct"]
        for event in events
        if event.get("em_straddle_pct") is not None
    ]
    ivs = [
        event["em_iv_pct"]
        for event in events
        if event.get("em_iv_pct") is not None
    ]
    return (
        sum(straddles) / len(straddles) if straddles else 0.0,
        sum(ivs) / len(ivs) if ivs else 0.0,
    )


def preserve_reported_events(
    events: list[dict],
    prior_events: list[dict],
    today: date,
    canonical: set[tuple[str, str]] | None = None,
) -> list[dict]:
    """Carry already-reported events forward from the previously published bundle.

    compute_em_math only answers for a pre-event observation, so once an earnings
    date has passed its expected-move row can never be rebuilt — a plain rebuild
    drops the event and the calendar renders it with no forecast. Rows for
    reported events therefore have to come from the last published bundle, while
    still-upcoming events stay fail-closed on whatever the fresh build produced.

    Fresh rows win on (ticker, earnings_date) collisions so a late EPS actual or
    a corrected realized move replaces the retained row. Non-canonical dates are
    never resurrected: a revision the dedup just collapsed (PLUS 5/20→5/28)
    would otherwise come back as a duplicate.
    """
    fresh_keys = {(e["ticker"], e["earnings_date"]) for e in events}
    cutoff = today.isoformat()
    retained = [
        event
        for event in prior_events
        if (event["ticker"], event["earnings_date"]) not in fresh_keys
        and event["earnings_date"] <= cutoff
        and (canonical is None or (event["ticker"], event["earnings_date"]) in canonical)
    ]
    if not retained:
        return events
    return sorted([*events, *retained], key=lambda e: (e["earnings_date"], e["ticker"]))
