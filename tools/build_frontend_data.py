#!/usr/bin/env python3
"""
Build frontend data from local parquet files.

Generates:
  public/weekly.json         — current week's earnings + expected moves
  public/symbols/{SYM}.json  — per-symbol detail with implied moves, Greeks, term structure

Uses the most recent parquet snapshot as the as-of date. Safe to re-run.
"""

import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

sys.path.append(str(Path(__file__).parent))
from build_earnings_events import build_earnings_events_table, create_duckdb_views
from math_baseline import compute_em_math


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
# Vercel serves from apps/frontend/public only (see vercel.json outputDirectory).
PUBLIC_DIR = REPO_ROOT / "apps" / "frontend" / "public"
EARNINGS_CSV = DATA_DIR / "earnings_calendar.csv"


def write_to_public(relpath: str, content: str) -> None:
    path = PUBLIC_DIR / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def target_week(today: date) -> tuple[date, date]:
    """Return (Mon, Fri) of the trading week for `today`. Weekend rolls to next week."""
    if today.weekday() >= 5:
        base = today + timedelta(days=(7 - today.weekday()))
    else:
        base = monday_of_week(today)
    return base, base + timedelta(days=4)


# Week offsets we expose in the UI: last week, this week, next week, week after next.
WEEK_OFFSETS = [-1, 0, 1, 2]


def jsonable(v):
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 6)
    return v


def build_symbol_detail(conn, ticker: str, as_of_date: date, earnings_dt: date | None) -> dict | None:
    """Build per-symbol detail: implied moves across expiries + term structure."""
    expiries = conn.execute(
        """
        SELECT DISTINCT expiry_date
        FROM v_options_chain
        WHERE ticker = ? AND as_of_date = ?
          AND expiry_date > ?
          AND expiry_date <= ?
        ORDER BY expiry_date
        LIMIT 6
        """,
        [ticker, as_of_date, as_of_date, as_of_date + timedelta(days=120)],
    ).fetchall()

    if not expiries:
        return None

    # Spot estimate from ATM call with delta ~ 0.5 on nearest expiry
    spot_row = conn.execute(
        """
        SELECT strike
        FROM v_options_chain
        WHERE ticker = ? AND as_of_date = ?
          AND expiry_date = ?
          AND call_put = 'C' AND delta IS NOT NULL
          AND delta BETWEEN 0.3 AND 0.7
        ORDER BY ABS(delta - 0.5)
        LIMIT 1
        """,
        [ticker, as_of_date, expiries[0][0]],
    ).fetchone()
    spot = float(spot_row[0]) if spot_row else None

    straddles = []
    for (exp,) in expiries:
        atm_row = conn.execute(
            """
            SELECT strike
            FROM v_options_chain
            WHERE ticker = ? AND as_of_date = ? AND expiry_date = ?
              AND call_put = 'C' AND delta IS NOT NULL
              AND delta BETWEEN 0.3 AND 0.7
            ORDER BY ABS(delta - 0.5)
            LIMIT 1
            """,
            [ticker, as_of_date, exp],
        ).fetchone()
        if not atm_row:
            continue
        atm_strike = float(atm_row[0])

        pair = conn.execute(
            """
            SELECT call_put, mid_price, iv, delta, gamma, vega, theta
            FROM v_options_chain
            WHERE ticker = ? AND as_of_date = ? AND expiry_date = ? AND strike = ?
              AND call_put IN ('C','P')
            """,
            [ticker, as_of_date, exp, atm_strike],
        ).fetchall()
        if len(pair) != 2:
            continue
        call = next((r for r in pair if r[0] == "C"), None)
        put = next((r for r in pair if r[0] == "P"), None)
        if not call or not put:
            continue

        call_mid, call_iv, call_delta, call_gamma, call_vega, call_theta = (
            call[1], call[2], call[3], call[4], call[5], call[6]
        )
        put_mid, put_iv = put[1], put[2]

        dte = (exp - as_of_date).days
        straddle_mid = (call_mid or 0) + (put_mid or 0)
        atm_iv = (call_iv + put_iv) / 2.0 if call_iv and put_iv else None
        em_iv_abs = (spot * atm_iv * math.sqrt(dte / 365.0)) if (spot and atm_iv and dte > 0) else None

        straddles.append({
            "expiration": exp.isoformat(),
            "dte": dte,
            "atm_strike": atm_strike,
            "atm_iv": jsonable(atm_iv),
            "atm_call_iv": jsonable(call_iv),
            "atm_put_iv": jsonable(put_iv),
            "straddle_mid": jsonable(straddle_mid),
            "em_straddle": jsonable(straddle_mid),
            "em_straddle_pct": jsonable(straddle_mid / spot) if spot else None,
            "em_iv": jsonable(em_iv_abs),
            "em_iv_pct": jsonable((em_iv_abs / spot) if (em_iv_abs and spot) else None),
            "call_delta": jsonable(call_delta),
            "call_gamma": jsonable(call_gamma),
            "call_vega": jsonable(call_vega),
            "call_theta": jsonable(call_theta),
        })

    if not straddles:
        return None

    # Earnings history (last 12 events for this ticker)
    history = conn.execute(
        """
        SELECT earnings_dt, timing
        FROM earnings_events
        WHERE ticker = ?
        ORDER BY earnings_dt DESC
        LIMIT 12
        """,
        [ticker],
    ).fetchall()

    # Use the closest post-earnings expiry as the headline expected move
    em = None
    if earnings_dt:
        em_math_data = compute_em_math(conn, ticker, as_of_date, earnings_dt)
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

    # Fall back to nearest expiry for summary if no earnings event
    if em is None and straddles:
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

    return {
        "symbol": ticker,
        "as_of_date": as_of_date.isoformat(),
        "spot_price": jsonable(spot),
        "expected_move": em,
        "straddle_features": straddles,
        "earnings_history": [
            {"date": d.isoformat(), "timing": t} for d, t in history
        ],
        "next_earnings": earnings_dt.isoformat() if earnings_dt else None,
    }


def build_week_events(conn, as_of_date: date, week_start: date, week_end: date) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ticker, earnings_dt, timing, fiscal_q
        FROM earnings_events
        WHERE earnings_dt BETWEEN ? AND ?
        ORDER BY earnings_dt, ticker
        """,
        [week_start, week_end],
    ).fetchall()

    events = []
    for ticker, earnings_dt, timing, fiscal_q in rows:
        em = compute_em_math(conn, ticker, as_of_date, earnings_dt)
        if not em:
            continue
        events.append({
            "ticker": ticker,
            "earnings_date": earnings_dt.isoformat(),
            "timing": timing or "unknown",
            "fiscal_q": fiscal_q,
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
            "em_method": "options_math",  # source: ATM straddle + IV baseline
            "confidence": "high",
        })
    events.sort(key=lambda e: (e["earnings_date"], e["ticker"]))
    return events


def main():
    (PUBLIC_DIR / "symbols").mkdir(parents=True, exist_ok=True)
    (PUBLIC_DIR / "weeks").mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect()
    build_earnings_events_table(conn, EARNINGS_CSV)
    create_duckdb_views(conn, DATA_DIR)

    as_of_row = conn.execute("SELECT MAX(as_of_date) FROM v_options_chain").fetchone()
    as_of_date = as_of_row[0]
    if as_of_date is None:
        print("❌ No options data available")
        sys.exit(1)

    today = date.today()
    this_monday = monday_of_week(today) if today.weekday() < 5 else today + timedelta(days=(7 - today.weekday()))

    # Build each week (last, this, next, +2) and collect unique tickers for detail pages.
    week_payloads = {}
    tickers_needing_detail: dict[str, date] = {}
    for offset in WEEK_OFFSETS:
        wk_start = this_monday + timedelta(days=7 * offset)
        wk_end = wk_start + timedelta(days=4)
        events = build_week_events(conn, as_of_date, wk_start, wk_end)
        print(f"📅 week {wk_start} (offset {offset:+d}) → {len(events)} events")
        payload = {
            "metadata": {
                "version": "v4_multi_week",
                "generated_at": datetime.now().isoformat(),
                "as_of_date": as_of_date.isoformat(),
                "method": "ATM straddle + IV baseline",
                "offset": offset,
            },
            "window": {"start": wk_start.isoformat(), "end": wk_end.isoformat()},
            "events": events,
            "summary": {
                "total_events": len(events),
                "avg_em_straddle_pct": (
                    sum(e["em_straddle_pct"] for e in events if e["em_straddle_pct"]) / len(events)
                    if events else 0
                ),
                "avg_em_iv_pct": (
                    sum(e["em_iv_pct"] for e in events if e["em_iv_pct"]) / len(events)
                    if events else 0
                ),
            },
        }
        week_payloads[wk_start] = payload
        write_to_public(
            f"weeks/{wk_start.isoformat()}.json",
            json.dumps(payload, indent=2, default=str),
        )
        for ev in events:
            tickers_needing_detail.setdefault(ev["ticker"], date.fromisoformat(ev["earnings_date"]))

    # Primary weekly.json points at the current week (back-compat for any old consumers).
    write_to_public("weekly.json", json.dumps(week_payloads[this_monday], indent=2, default=str))

    # Manifest of available weeks (used by the UI to render nav state).
    manifest = {
        "as_of_date": as_of_date.isoformat(),
        "current_week": this_monday.isoformat(),
        "weeks": [
            {
                "start": d.isoformat(),
                "end": (d + timedelta(days=4)).isoformat(),
                "offset": offset,
                "count": len(week_payloads[d]["events"]),
            }
            for offset, d in ((off, this_monday + timedelta(days=7 * off)) for off in WEEK_OFFSETS)
        ],
    }
    write_to_public("weeks/manifest.json", json.dumps(manifest, indent=2))

    # Generate per-symbol detail: all tickers seen in any week + a curated popular list.
    popular = [
        "AAPL", "TSLA", "NVDA", "AMZN", "MSFT", "META", "GOOGL", "JPM",
        "BAC", "GS", "NFLX", "JNJ", "UNH", "PG", "V", "MA", "IBM", "INTC",
        "T", "BA", "GE", "RTX", "AXP", "HON", "LMT", "TMO", "MCO",
    ]
    for ticker in popular:
        if ticker not in tickers_needing_detail:
            row = conn.execute(
                """
                SELECT earnings_dt FROM earnings_events
                WHERE ticker = ? AND earnings_dt >= ?
                ORDER BY earnings_dt LIMIT 1
                """,
                [ticker, as_of_date],
            ).fetchone()
            tickers_needing_detail[ticker] = row[0] if row else None

    generated = 0
    for ticker, earn_dt in tickers_needing_detail.items():
        try:
            detail = build_symbol_detail(conn, ticker, as_of_date, earn_dt)
            if not detail:
                continue
            # Attach timing (BMO/AMC/unknown) from the earnings row, surfaced on the detail page.
            if earn_dt:
                row = conn.execute(
                    "SELECT timing FROM earnings_events WHERE ticker = ? AND earnings_dt = ? LIMIT 1",
                    [ticker, earn_dt],
                ).fetchone()
                timing = (row[0] if row else None) or "unknown"
                detail["next_earnings_timing"] = timing
                if detail.get("expected_move"):
                    detail["expected_move"]["timing"] = timing
                    detail["expected_move"]["em_method"] = "options_math"
            write_to_public(
                f"symbols/{ticker}.json",
                json.dumps(detail, indent=2, default=str),
            )
            generated += 1
        except Exception as e:
            print(f"  ⚠️  {ticker} detail: {e}")

    print(f"✅ {len(week_payloads)} weeks written, {generated} symbol files")
    conn.close()


if __name__ == "__main__":
    main()
