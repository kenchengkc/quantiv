"""Timing-aware realized-move reconciliation and TwelveData fallback controls."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from twelvedata_basic import (
    TwelveDataUsageLedger,
    fetch_daily_closes,
    load_twelvedata_config,
    plan_credit_use,
)

import pandas as pd

from ml.causal_features import extract_reaction_labels, reaction_sessions
from .shared import DATA_DIR, ET, MARKET_SESSIONS_JSON, jsonable


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
DEFAULT_REGULAR_CLOSE_MIN = 16 * 60


def load_market_sessions() -> tuple[set[date], dict[date, int]]:
    if not MARKET_SESSIONS_JSON.exists():
        raise RuntimeError(
            f"canonical market session contract is missing: {MARKET_SESSIONS_JSON}"
        )
    payload = json.loads(MARKET_SESSIONS_JSON.read_text())
    if payload.get("schema") != "quantiv.market-sessions.v1":
        raise RuntimeError("unsupported canonical market session schema")
    holidays = {
        date.fromisoformat(str(value))
        for value in payload.get("holidays") or []
    }
    early_closes: dict[date, int] = {}
    for day, close in (payload.get("early_closes") or {}).items():
        hour, minute = (int(part) for part in str(close).split(":", 1))
        early_closes[date.fromisoformat(str(day))] = hour * 60 + minute
    if not holidays:
        raise RuntimeError("canonical market session contract contains no holidays")
    return holidays, early_closes


MARKET_HOLIDAYS, MARKET_EARLY_CLOSES = load_market_sessions()


def timing_bucket(timing: str | None) -> str:
    k = (timing or "").strip().lower()
    if k == "bmo" or "before" in k:
        return "bmo"
    if k == "amc" or "after" in k:
        return "amc"
    return "unknown"


def next_trading_day(d: date) -> date:
    cur = d + timedelta(days=1)
    for _ in range(366):
        if cur.weekday() < 5 and cur not in MARKET_HOLIDAYS:
            return cur
        cur += timedelta(days=1)
    return cur


def earnings_reaction_close_date(earnings_dt: date, timing: str | None) -> date:
    return next_trading_day(earnings_dt) if timing_bucket(timing) == "amc" else earnings_dt


def realization_window_complete(
    earnings_dt: date,
    timing: str | None,
    now: datetime | None = None,
) -> bool:
    now_et = now or datetime.now(ET)
    boundaries = reaction_sessions(earnings_dt, timing_bucket(timing))
    if boundaries is None:
        return False
    _, close_date = boundaries
    if now_et.date() > close_date:
        return True
    if now_et.date() < close_date:
        return False
    close_minute = MARKET_EARLY_CLOSES.get(close_date, DEFAULT_REGULAR_CLOSE_MIN)
    return now_et.hour * 60 + now_et.minute >= close_minute


def realized_move_from_ohlcv(
    conn,
    ticker: str,
    earnings_dt: date,
    timing: str | None,
) -> float | None:
    """The signed value of the same adjusted session target used by training."""
    label = reaction_label_lookup(conn, [(ticker, earnings_dt, timing)], as_of_date=date.today()).get(
        (ticker, earnings_dt))
    return None if label is None else float(label.signed_realized_move_pct)


def reaction_label_lookup(conn, events: list[tuple[str, date, str | None]], *, as_of_date: date) -> dict:
    """Shared exact labels for product queries whose calendar uses ticker aliases."""
    if not events:
        return {}
    frame = pd.DataFrame([(symbol, day, timing_bucket(timing)) for symbol, day, timing in events],
                         columns=["act_symbol", "earnings_date", "timing"])
    try:
        calendar_columns = {row[0] for row in conn.execute("""
            SELECT column_name FROM information_schema.columns WHERE table_name='earnings_events'
        """).fetchall()}
        if "timing_source" in calendar_columns:
            # Calendar inference can use later reports. Only original reported
            # timings establish the exact historical reaction session.
            conn.register("_product_reaction_events", frame)
            try:
                frame = conn.execute("""
                    SELECT events.act_symbol,events.earnings_date,
                           calendar.timing
                    FROM _product_reaction_events events
                    JOIN earnings_events calendar
                      ON calendar.ticker=events.act_symbol
                     AND calendar.earnings_dt=CAST(events.earnings_date AS DATE)
                    WHERE calendar.timing_source='reported'
                """).fetchdf()
            finally:
                conn.unregister("_product_reaction_events")
            frame["timing"] = frame["timing"].map(timing_bucket)
        labels = extract_reaction_labels(conn, as_of_date=as_of_date, events=frame)
    except Exception:
        return {}
    return {(row.act_symbol, pd.Timestamp(row.earnings_date).date()): row
            for row in labels.itertuples(index=False)}


def enrich_realized_moves_from_ohlcv(conn, events: list[dict]) -> int:
    """Reconcile product outcomes with exact, normalized and mature ML targets."""
    candidates = []
    updated = 0
    for ev in events:
        try:
            ticker = str(ev.get("ticker") or "").upper()
            earnings_dt = date.fromisoformat(str(ev.get("earnings_date") or "")[:10])
        except ValueError:
            continue
        timing = timing_bucket(ev.get("timing"))
        if timing == "unknown" or not realization_window_complete(earnings_dt, timing):
            if ev.get("realized_move_pct") is not None:
                ev["realized_move_pct"] = None
                ev["realized_label_source"] = None
                ev["realized_target_protocol"] = None
                updated += 1
            continue
        if ticker:
            candidates.append((ev, ticker, earnings_dt, timing))
    if not candidates:
        return updated
    moves = reaction_label_lookup(conn, [(ticker, earnings_dt, timing)
                                        for _, ticker, earnings_dt, timing in candidates],
                                  as_of_date=date.today())
    for ev, ticker, earnings_dt, timing in candidates:
        label = moves.get((ticker, earnings_dt))
        move = None if label is None else jsonable(label.signed_realized_move_pct)
        if ev.get("realized_move_pct") != move:
            ev["realized_move_pct"] = move
            updated += 1
        ev["realized_label_source"] = None if label is None else label.label_source
        ev["realized_target_protocol"] = None if label is None else label.target_protocol
        if label is not None:
            ev["realized_pre_price_date"] = pd.Timestamp(label.pre_price_date).date().isoformat()
            ev["realized_post_price_date"] = pd.Timestamp(label.post_price_date).date().isoformat()
            ev["realized_label_available_at"] = pd.Timestamp(label.label_available_at).date().isoformat()
    return updated


def _compute_realized_from_closes(
    closes: list[tuple[date, float]],
    earnings_dt: date,
    timing: str | None,
) -> float | None:
    """Exact-session fallback on externally comparable closes, without label proof."""
    boundaries = reaction_sessions(earnings_dt, timing_bucket(timing))
    if boundaries is None:
        return None
    pre, post = boundaries
    observations = dict(closes)
    if len(observations) != len(closes):
        return None
    pre_close, post_close = observations.get(pre), observations.get(post)
    if pre_close is None or post_close is None or pre_close <= 0 or post_close <= 0:
        return None
    return post_close / pre_close - 1.0


def twelvedata_realized_candidates(events: list[dict]) -> tuple[list[tuple[dict, str, date, date]], dict[str, int]]:
    missing: list[tuple[dict, str, date, date]] = []
    stats = {
        "already_realized": 0,
        "not_complete": 0,
        "invalid": 0,
    }
    for ev in events:
        if ev.get("realized_move_pct") is not None or ev.get("realized_external_fallback_pct") is not None:
            stats["already_realized"] += 1
            continue
        try:
            ticker = str(ev.get("ticker") or "").upper()
            earnings_dt = date.fromisoformat(str(ev.get("earnings_date") or "")[:10])
        except ValueError:
            stats["invalid"] += 1
            continue
        if not ticker or not realization_window_complete(earnings_dt, ev.get("timing")):
            stats["not_complete"] += 1
            continue
        close_date = earnings_reaction_close_date(earnings_dt, ev.get("timing"))
        missing.append((ev, ticker, earnings_dt, close_date))
    return missing, stats


def print_twelvedata_dry_run(events: list[dict], *, label: str) -> None:
    missing, stats = twelvedata_realized_candidates(events)
    symbols = sorted({ticker for _, ticker, _, _ in missing})
    config = load_twelvedata_config(DATA_DIR)
    ledger = TwelveDataUsageLedger(config.ledger_path, config.daily_credit_limit)
    plan = plan_credit_use(symbols, config, ledger=ledger)
    print(
        f"    TwelveData dry-run {label}: "
        f"{len(missing)} candidate event(s), "
        f"{plan['needed_credits']} needed credit(s), "
        f"{plan['remaining_credits']} remaining, "
        f"{plan['planned_credits']} would be used; "
        f"skipped: {len(plan['skipped_symbols'])} quota, "
        f"{stats['already_realized']} already realized, "
        f"{stats['not_complete']} not complete, "
        f"{stats['invalid']} invalid",
        flush=True,
    )
    if plan["planned_symbols"]:
        print(f"      planned symbols: {', '.join(plan['planned_symbols'])}", flush=True)
    if plan["skipped_symbols"]:
        print(f"      quota-skipped symbols: {', '.join(plan['skipped_symbols'])}", flush=True)


def enrich_realized_moves_from_twelvedata(events: list[dict], *, dry_run: bool = False, label: str = "") -> int:
    config = load_twelvedata_config(DATA_DIR)
    if dry_run:
        print_twelvedata_dry_run(events, label=label)
        return 0
    if not config.realized_fallback_enabled or not config.api_key:
        return 0

    missing, _stats = twelvedata_realized_candidates(events)

    if not missing:
        return 0

    symbols = sorted({ticker for _, ticker, _, _ in missing})
    start = min(earnings_dt for _, _, earnings_dt, _ in missing) - timedelta(days=7)
    end = max(close_date for _, _, _, close_date in missing) + timedelta(days=7)
    fetch_result = fetch_daily_closes(
        symbols,
        start,
        end,
        config,
        purpose="realized_fallback",
    )
    closes_by_symbol = fetch_result.closes
    if fetch_result.skipped_symbols:
        print(
            f"  ⚠ TwelveData quota skipped {len(fetch_result.skipped_symbols)} symbol(s): "
            f"{', '.join(fetch_result.skipped_symbols)}",
            flush=True,
        )
    if fetch_result.errors:
        for err in fetch_result.errors[:8]:
            print(f"  ⚠ TwelveData fallback: {err}", flush=True)
        if len(fetch_result.errors) > 8:
            print(f"  ⚠ TwelveData fallback: {len(fetch_result.errors) - 8} more error(s)", flush=True)
    if not closes_by_symbol:
        return 0

    updated = 0
    for ev, ticker, earnings_dt, _ in missing:
        move = _compute_realized_from_closes(
            closes_by_symbol.get(ticker, []),
            earnings_dt,
            ev.get("timing"),
        )
        if move is None:
            continue
        ev["realized_external_fallback_pct"] = jsonable(move)
        ev["realized_external_fallback_source"] = "external_split_adjusted_close_unverified"
        ev["realized_move_pct"] = None
        ev["realized_move_abs"] = None
        ev["realized_label_source"] = None
        ev["realized_target_protocol"] = None
        updated += 1
    return updated


def twelvedata_hist_move_candidates(conn, events: list[dict]) -> dict[str, list[tuple[date, str]]]:
    """Return {ticker: last earnings events} for rows missing hist_move_avg_4q."""
    out: dict[str, list[tuple[date, str]]] = {}
    for ev in events:
        if ev.get("hist_move_avg_4q") is not None:
            continue
        ticker = str(ev.get("ticker") or "").upper()
        try:
            earnings_dt = date.fromisoformat(str(ev.get("earnings_date") or "")[:10])
        except ValueError:
            continue
        if not ticker or ticker in out:
            continue
        try:
            rows = conn.execute(
                """
                SELECT earnings_dt, timing
                FROM earnings_events
                WHERE ticker = ? AND earnings_dt < ?
                ORDER BY earnings_dt DESC
                LIMIT 4
                """,
                [ticker, earnings_dt],
            ).fetchall()
        except Exception:
            rows = []
        history = [(d, t or "unknown") for d, t in rows if realization_window_complete(d, t)]
        if history:
            out[ticker] = history
    return out


def print_twelvedata_hist_dry_run(conn, events: list[dict], *, label: str) -> None:
    candidates = twelvedata_hist_move_candidates(conn, events)
    config = load_twelvedata_config(DATA_DIR)
    ledger = TwelveDataUsageLedger(config.ledger_path, config.daily_credit_limit)
    plan = plan_credit_use(sorted(candidates), config, ledger=ledger)
    print(
        f"    TwelveData hist dry-run {label}: "
        f"{len(candidates)} ticker candidate(s), "
        f"{plan['needed_credits']} needed credit(s), "
        f"{plan['remaining_credits']} remaining, "
        f"{plan['planned_credits']} would be used; "
        f"skipped: {len(plan['skipped_symbols'])} quota",
        flush=True,
    )
    if plan["planned_symbols"]:
        print(f"      planned hist symbols: {', '.join(plan['planned_symbols'])}", flush=True)
    if plan["skipped_symbols"]:
        print(f"      quota-skipped hist symbols: {', '.join(plan['skipped_symbols'])}", flush=True)


def enrich_hist_move_avg_from_twelvedata(
    conn,
    events: list[dict],
    *,
    dry_run: bool = False,
    label: str = "",
) -> int:
    config = load_twelvedata_config(DATA_DIR)
    if dry_run:
        print_twelvedata_hist_dry_run(conn, events, label=label)
        return 0
    if not config.realized_fallback_enabled or not config.api_key:
        return 0

    candidates = twelvedata_hist_move_candidates(conn, events)
    if not candidates:
        return 0

    all_events = [item for history in candidates.values() for item in history]
    start = min(d for d, _ in all_events) - timedelta(days=7)
    end = max(earnings_reaction_close_date(d, timing) for d, timing in all_events) + timedelta(days=7)
    fetch_result = fetch_daily_closes(
        sorted(candidates),
        start,
        end,
        config,
        purpose="hist_move_avg_4q",
    )
    if fetch_result.skipped_symbols:
        print(
            f"  ⚠ TwelveData hist quota skipped {len(fetch_result.skipped_symbols)} symbol(s): "
            f"{', '.join(fetch_result.skipped_symbols)}",
            flush=True,
        )
    if fetch_result.errors:
        for err in fetch_result.errors[:8]:
            print(f"  ⚠ TwelveData hist fallback: {err}", flush=True)
        if len(fetch_result.errors) > 8:
            print(f"  ⚠ TwelveData hist fallback: {len(fetch_result.errors) - 8} more error(s)", flush=True)

    hist_avg_by_symbol: dict[str, float] = {}
    for ticker, history in candidates.items():
        closes = fetch_result.closes.get(ticker, [])
        vals = [
            abs(move)
            for d, timing in history
            if (move := _compute_realized_from_closes(closes, d, timing)) is not None
        ]
        if vals:
            hist_avg_by_symbol[ticker] = sum(vals) / len(vals)

    updated = 0
    for ev in events:
        ticker = str(ev.get("ticker") or "").upper()
        avg = hist_avg_by_symbol.get(ticker)
        if avg is None:
            continue
        new_value = jsonable(avg)
        if ev.get("hist_move_avg_4q") != new_value:
            ev["hist_move_avg_4q"] = new_value
            updated += 1
    return updated


def twelvedata_validation_sample_size() -> int:
    try:
        return max(0, int(os.getenv("TWELVEDATA_VALIDATION_SAMPLE_SIZE", "8")))
    except ValueError:
        return 8


def twelvedata_validation_delta_threshold() -> float:
    try:
        return max(0.0, float(os.getenv("TWELVEDATA_VALIDATION_DELTA_PCT", "0.005")))
    except ValueError:
        return 0.005


def twelvedata_validation_candidates(
    conn,
    events: list[dict],
    *,
    limit: int,
) -> list[tuple[str, date, str | None, float, date]]:
    out: list[tuple[str, date, str | None, float, date]] = []
    seen: set[tuple[str, date]] = set()
    for ev in events:
        if len(out) >= limit:
            break
        try:
            ticker = str(ev.get("ticker") or "").upper()
            earnings_dt = date.fromisoformat(str(ev.get("earnings_date") or "")[:10])
        except ValueError:
            continue
        key = (ticker, earnings_dt)
        if not ticker or key in seen:
            continue
        if not realization_window_complete(earnings_dt, ev.get("timing")):
            continue
        local_move = realized_move_from_ohlcv(conn, ticker, earnings_dt, ev.get("timing"))
        if local_move is None:
            continue
        seen.add(key)
        out.append((
            ticker,
            earnings_dt,
            ev.get("timing"),
            local_move,
            earnings_reaction_close_date(earnings_dt, ev.get("timing")),
        ))
    return out


def print_twelvedata_validation_dry_run(conn, events: list[dict], *, label: str) -> None:
    sample_size = twelvedata_validation_sample_size()
    if sample_size <= 0:
        return
    candidates = twelvedata_validation_candidates(conn, events, limit=sample_size)
    config = load_twelvedata_config(DATA_DIR)
    ledger = TwelveDataUsageLedger(config.ledger_path, config.daily_credit_limit)
    plan = plan_credit_use([ticker for ticker, *_ in candidates], config, ledger=ledger)
    print(
        f"    TwelveData validation dry-run {label}: "
        f"{len(candidates)} sample candidate(s), "
        f"{plan['needed_credits']} needed credit(s), "
        f"{plan['remaining_credits']} remaining, "
        f"{plan['planned_credits']} would be used; "
        f"skipped: {len(plan['skipped_symbols'])} quota",
        flush=True,
    )
    if plan["planned_symbols"]:
        print(f"      planned validation symbols: {', '.join(plan['planned_symbols'])}", flush=True)
    if plan["skipped_symbols"]:
        print(f"      quota-skipped validation symbols: {', '.join(plan['skipped_symbols'])}", flush=True)


def validate_twelvedata_against_ohlcv(
    conn,
    events: list[dict],
    *,
    dry_run: bool = False,
    label: str = "",
) -> int:
    sample_size = twelvedata_validation_sample_size()
    if sample_size <= 0:
        return 0
    if dry_run:
        print_twelvedata_validation_dry_run(conn, events, label=label)
        return 0

    config = load_twelvedata_config(DATA_DIR)
    if not config.realized_fallback_enabled or not config.api_key:
        return 0

    candidates = twelvedata_validation_candidates(conn, events, limit=sample_size)
    if not candidates:
        return 0

    symbols = sorted({ticker for ticker, *_ in candidates})
    start = min(earnings_dt for _, earnings_dt, *_ in candidates) - timedelta(days=7)
    end = max(close_date for _, _, _, _, close_date in candidates) + timedelta(days=7)
    fetch_result = fetch_daily_closes(
        symbols,
        start,
        end,
        config,
        purpose="validation_sample",
    )
    if fetch_result.skipped_symbols:
        print(
            f"  ⚠ TwelveData validation quota skipped {len(fetch_result.skipped_symbols)} symbol(s): "
            f"{', '.join(fetch_result.skipped_symbols)}",
            flush=True,
        )
    if fetch_result.errors:
        for err in fetch_result.errors[:8]:
            print(f"  ⚠ TwelveData validation: {err}", flush=True)
        if len(fetch_result.errors) > 8:
            print(f"  ⚠ TwelveData validation: {len(fetch_result.errors) - 8} more error(s)", flush=True)

    threshold = twelvedata_validation_delta_threshold()
    compared = 0
    deltas: list[tuple[str, date, float, float, float]] = []
    for ticker, earnings_dt, timing, local_move, _close_date in candidates:
        td_move = _compute_realized_from_closes(
            fetch_result.closes.get(ticker, []),
            earnings_dt,
            timing,
        )
        if td_move is None:
            continue
        compared += 1
        delta = td_move - local_move
        if abs(delta) >= threshold:
            deltas.append((ticker, earnings_dt, local_move, td_move, delta))

    if deltas:
        print(
            f"  ⚠ TwelveData validation {label}: "
            f"{len(deltas)}/{compared} material delta(s) >= {threshold:.2%}",
            flush=True,
        )
        for ticker, earnings_dt, local_move, td_move, delta in deltas[:8]:
            print(
                f"    {ticker} {earnings_dt.isoformat()}: "
                f"local={local_move:.2%}, twelvedata={td_move:.2%}, delta={delta:.2%}",
                flush=True,
            )
        if len(deltas) > 8:
            print(f"    ... {len(deltas) - 8} more material delta(s)", flush=True)
    elif compared:
        print(
            f"    TwelveData validation {label}: {compared} sample(s), "
            f"no material deltas >= {threshold:.2%}",
            flush=True,
        )
    return compared
