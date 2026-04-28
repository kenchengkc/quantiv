#!/usr/bin/env python3
"""
Build frontend data from local parquet files.

Generates:
  public/weekly.json         — current week's earnings + expected moves
  public/screener.json       — merged, deduped earnings rows for /screener (single fetch)
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
FORECASTS_DIR = DATA_DIR / "forecasts"
MODEL_HORIZONS = [1, 2, 3, 7, 14, 21]


def load_ml_forecasts() -> dict[tuple[str, str], dict]:
    """Return {(ticker, earnings_date_iso): forecast_row} from the newest
    daily_score.py output. Picks the row whose model_horizon is closest to the
    actual lead time, and the most recent snapshot_date within that horizon.
    Returns {} when no forecasts exist."""
    if not FORECASTS_DIR.exists():
        return {}
    files = sorted(FORECASTS_DIR.glob("forecasts_*.parquet"))
    if not files:
        return {}
    latest = files[-1]
    try:
        import pandas as pd  # local import — only needed when forecasts exist
        df = pd.read_parquet(latest)
    except Exception as e:
        print(f"⚠️  Could not read {latest.name}: {e}")
        return {}
    if df.empty:
        return {}

    df = df.copy()
    df["earnings_date"] = pd.to_datetime(df["earnings_date"]).dt.date
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
    df["lead_days"] = (df["earnings_date"] - df["snapshot_date"]).apply(lambda d: d.days)
    df["horizon_gap"] = (df["model_horizon"] - df["lead_days"]).abs()

    # Per (ticker, earnings_date): smallest horizon_gap, then most recent snapshot.
    df = df.sort_values(["act_symbol", "earnings_date", "horizon_gap", "snapshot_date"],
                        ascending=[True, True, True, False])
    df = df.drop_duplicates(subset=["act_symbol", "earnings_date"], keep="first")

    out: dict[tuple[str, str], dict] = {}
    for row in df.to_dict(orient="records"):
        key = (row["act_symbol"], row["earnings_date"].isoformat())
        out[key] = row
    print(f"🤖 Loaded {len(out)} ML forecasts from {latest.name}")
    return out


def ml_fields(fc: dict | None) -> dict:
    """Flatten a forecast row to the public JSON field names. Prefers p10/p90
    when present (newer daily_score.py output); falls back to band68/band95."""
    if not fc:
        return {}
    out: dict = {}

    def pick(*keys):
        for k in keys:
            v = fc.get(k)
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v
        return None

    out["em_ml_pct"] = jsonable(pick("em_ml_pct"))
    out["em_ml_abs"] = jsonable(pick("em_ml_abs"))
    out["correction_factor"] = jsonable(pick("correction_factor"))
    out["model_horizon"] = pick("model_horizon")
    out["ml_snapshot_date"] = (
        fc["snapshot_date"].isoformat() if fc.get("snapshot_date") else None
    )
    # Prefer true quantiles if the trainer emitted them, else use band endpoints.
    out["p10"] = jsonable(pick("p10", "band95_low_pct"))
    out["p25"] = jsonable(pick("p25", "band68_low_pct"))
    out["p50"] = jsonable(pick("p50", "em_ml_pct"))
    out["p75"] = jsonable(pick("p75", "band68_high_pct"))
    out["p90"] = jsonable(pick("p90", "band95_high_pct"))
    return {k: v for k, v in out.items() if v is not None}


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


def build_symbol_detail(conn, ticker: str, as_of_date: date, earnings_dt: date | None,
                        ml_lookup: dict[tuple[str, str], dict] | None = None) -> dict | None:
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
            fc = (ml_lookup or {}).get((ticker, earnings_dt.isoformat()))
            ml = ml_fields(fc)
            if ml:
                em.update(ml)
                em["em_method"] = "ml_lightgbm"
            else:
                em["em_method"] = "options_math"

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
        "vol_regime": vol_regime,
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

    # Hist average realized move — last 4 earnings × OHLCV close-to-close
    try:
        rows = conn.execute(
            """
            SELECT ABS(post.close / NULLIF(pre.close, 0) - 1.0) AS realized
            FROM v_earnings e
            JOIN v_ohlcv pre  ON pre.act_symbol = e.act_symbol
                AND pre.date < e.date AND pre.date >= e.date - INTERVAL '5' DAY
            JOIN v_ohlcv post ON post.act_symbol = e.act_symbol
                AND post.date > e.date AND post.date <= e.date + INTERVAL '5' DAY
            WHERE e.act_symbol = ? AND e.date < ? AND pre.close > 0 AND post.close > 0
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY e.date ORDER BY pre.date DESC, post.date ASC
            ) = 1
            ORDER BY e.date DESC LIMIT 4
            """,
            [ticker, earnings_dt],
        ).fetchall()
        if rows:
            vals = [r[0] for r in rows if r[0] is not None]
            if vals:
                extras["hist_move_avg_4q"] = jsonable(sum(vals) / len(vals))
    except Exception:
        pass

    # IV crush proxy — front ATM IV vs the next-out expiry's ATM IV.
    # Already computed in v_straddle_features as iv_crush_pct upstream;
    # we recompute here from raw options to avoid coupling to that view.
    try:
        front = conn.execute(
            """
            SELECT iv FROM v_options_chain
            WHERE ticker = ? AND as_of_date = ? AND call_put = 'C'
              AND iv IS NOT NULL AND iv > 0
              AND expiry_date >= ?
            ORDER BY expiry_date ASC, ABS(delta - 0.5) ASC
            LIMIT 1
            """,
            [ticker, as_of_date, earnings_dt],
        ).fetchone()
        if front:
            front_iv = float(front[0])
            back = conn.execute(
                """
                SELECT iv FROM v_options_chain
                WHERE ticker = ? AND as_of_date = ? AND call_put = 'C'
                  AND iv IS NOT NULL AND iv > 0
                  AND expiry_date > ?
                ORDER BY expiry_date ASC, ABS(delta - 0.5) ASC
                LIMIT 1 OFFSET 1
                """,
                [ticker, as_of_date, earnings_dt],
            ).fetchone()
            if back:
                back_iv = float(back[0])
                if front_iv > 0:
                    extras["iv_crush_pct"] = jsonable((front_iv - back_iv) / front_iv)
    except Exception:
        pass

    return extras


def build_week_events(conn, as_of_date: date, week_start: date, week_end: date,
                      ml_lookup: dict[tuple[str, str], dict],
                      require_ml: bool = True) -> list[dict]:
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
    skipped_no_ml = 0
    skipped_no_options = 0
    today = date.today()
    for i, (ticker, earnings_dt, timing, fiscal_q) in enumerate(rows, 1):
        fc = ml_lookup.get((ticker, earnings_dt.isoformat()))
        # Require ML only for *future* earnings. Past earnings within the same
        # week (e.g. Mon/Tue when today is Wed) fall back to options_math —
        # daily_score.py only scores upcoming events, so forecasts are absent
        # for past dates by design.
        if require_ml and not fc and earnings_dt >= today:
            skipped_no_ml += 1
            continue
        em = compute_em_math(conn, ticker, as_of_date, earnings_dt)
        if not em:
            skipped_no_options += 1
            continue
        if i % 25 == 0 or i == len(rows):
            print(f"    event {i}/{len(rows)}: {ticker} {earnings_dt}", flush=True)
        ml = ml_fields(fc)
        extras = screener_extras(conn, ticker, earnings_dt, as_of_date)
        event = {
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
            "em_method": "ml_lightgbm" if ml else "options_math",
            "confidence": "high" if ml else "high",
            **extras,
            **ml,
        }
        events.append(event)
    events.sort(key=lambda e: (e["earnings_date"], e["ticker"]))
    if skipped_no_ml or skipped_no_options:
        print(
            f"    skipped: {skipped_no_ml} (no ML forecast), "
            f"{skipped_no_options} (no options data)",
            flush=True,
        )
    return events


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip symbol detail files that were written AFTER weekly.json "
            "in this run. Use when recovering from a crash mid-loop so you "
            "don't re-do hundreds of completed tickers."
        ),
    )
    ap.add_argument(
        "--skip-weeks",
        action="store_true",
        help="Skip the weeks rebuild entirely — jump straight into symbol details. "
             "Implied by --resume.",
    )
    args = ap.parse_args()

    (PUBLIC_DIR / "symbols").mkdir(parents=True, exist_ok=True)
    (PUBLIC_DIR / "weeks").mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect()
    # Cap DuckDB memory so long-running symbol-detail loops don't OOM-segfault.
    # Spills to disk if the working set exceeds this.
    conn.execute("PRAGMA memory_limit='4GB'")
    conn.execute("PRAGMA threads=4")
    build_earnings_events_table(conn, EARNINGS_CSV)
    create_duckdb_views(conn, DATA_DIR)
    ml_lookup = load_ml_forecasts()

    as_of_row = conn.execute("SELECT MAX(as_of_date) FROM v_options_chain").fetchone()
    as_of_date = as_of_row[0]
    if as_of_date is None:
        print("❌ No options data available")
        sys.exit(1)

    today = date.today()
    this_monday = monday_of_week(today) if today.weekday() < 5 else today + timedelta(days=(7 - today.weekday()))

    # Build each week (last, this, next, +2) and collect unique tickers for detail pages.
    # On --resume: reuse the existing per-week JSONs on disk — don't rebuild them.
    skip_weeks = args.skip_weeks or args.resume
    week_payloads = {}
    tickers_needing_detail: dict[str, date] = {}

    if skip_weeks:
        print("⏭️  --resume/--skip-weeks: reading existing weeks/*.json from disk", flush=True)
        for offset in WEEK_OFFSETS:
            wk_start = this_monday + timedelta(days=7 * offset)
            wk_path = PUBLIC_DIR / "weeks" / f"{wk_start.isoformat()}.json"
            if not wk_path.exists():
                print(f"  ❌ missing {wk_path.name} — run without --resume to rebuild it")
                sys.exit(1)
            payload = json.loads(wk_path.read_text())
            week_payloads[wk_start] = payload
            events = payload.get("events", [])
            print(f"📅 week {wk_start} (offset {offset:+d}) → {len(events)} events (cached)")
            for ev in events:
                tickers_needing_detail.setdefault(ev["ticker"], date.fromisoformat(ev["earnings_date"]))

    for offset in ([] if skip_weeks else WEEK_OFFSETS):
        wk_start = this_monday + timedelta(days=7 * offset)
        wk_end = wk_start + timedelta(days=4)
        # daily_score.py only scores upcoming earnings within a ~21-day window,
        # so require_ml would leave last/+2 weeks empty. Fall back to the
        # options_math baseline for those; enforce ML on current + next week.
        require_ml = offset in (0, 1)
        events = build_week_events(conn, as_of_date, wk_start, wk_end, ml_lookup,
                                   require_ml=require_ml)
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
    if not skip_weeks:
        write_to_public("weekly.json", json.dumps(week_payloads[this_monday], indent=2, default=str))

    # Manifest of available weeks (used by the UI to render nav state).
    if not skip_weeks:
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

        screener = build_screener_payload(as_of_date, this_monday, week_payloads)
        write_to_public("screener.json", json.dumps(screener, indent=2, default=str))
        print(f"📊 screener.json → {screener['metadata']['event_count']} events", flush=True)

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
    skipped_resume = 0
    total_details = len(tickers_needing_detail)
    if args.resume:
        print(
            "⏭️  --resume: skipping any ticker whose symbols/{TICKER}.json already exists. "
            "Files from previous runs stay — run without --resume for a full refresh.",
            flush=True,
        )

    print(f"📝 generating {total_details} symbol detail files", flush=True)
    # Print every ticker so a segfault leaves the exact culprit on the last
    # visible line. (Cheap — ~1000 lines over a 10-min run.)
    for i, (ticker, earn_dt) in enumerate(tickers_needing_detail.items(), 1):
        if args.resume and (PUBLIC_DIR / "symbols" / f"{ticker}.json").exists():
            skipped_resume += 1
            continue
        print(f"  [{i}/{total_details}] {ticker}", flush=True)
        # Recycle the DuckDB connection every 200 tickers to shed any
        # accumulated query-plan memory that could push us toward a segfault.
        if i > 1 and i % 200 == 0:
            conn.close()
            conn = duckdb.connect()
            conn.execute("PRAGMA memory_limit='4GB'")
            conn.execute("PRAGMA threads=4")
            build_earnings_events_table(conn, EARNINGS_CSV)
            create_duckdb_views(conn, DATA_DIR)
        try:
            detail = build_symbol_detail(conn, ticker, as_of_date, earn_dt, ml_lookup)
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
                    # em_method is already set inside build_symbol_detail based
                    # on whether an ML forecast was attached; don't overwrite.
            write_to_public(
                f"symbols/{ticker}.json",
                json.dumps(detail, indent=2, default=str),
            )
            generated += 1
        except Exception as e:
            print(f"  ⚠️  {ticker} detail: {e}")

    print(
        f"✅ {len(week_payloads)} weeks written, {generated} symbol files"
        + (f", {skipped_resume} skipped (resume)" if skipped_resume else "")
    )
    conn.close()


if __name__ == "__main__":
    main()
