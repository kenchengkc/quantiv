"""Timing-aware realized-move reconciliation and TwelveData fallback controls."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta

from twelvedata_basic import (
    TwelveDataUsageLedger,
    fetch_daily_closes,
    load_twelvedata_config,
    plan_credit_use,
)

from .shared import DATA_DIR, ET, MARKET_HOLIDAYS_TS, jsonable

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


def load_market_holidays() -> set[date]:
    if not MARKET_HOLIDAYS_TS.exists():
        return set()
    text = MARKET_HOLIDAYS_TS.read_text()
    match = re.search(
        r"MARKET_HOLIDAYS_US\s*=\s*\[(.*?)\]\s*as const",
        text,
        flags=re.S,
    )
    if not match:
        return set()
    return {
        date.fromisoformat(m.group(0))
        for m in re.finditer(r"\d{4}-\d{2}-\d{2}", match.group(1))
    }


MARKET_HOLIDAYS = load_market_holidays()


def timing_bucket(timing: str | None) -> str:
    k = (timing or "").lower()
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
    close_date = earnings_reaction_close_date(earnings_dt, timing)
    if now_et.date() > close_date:
        return True
    if now_et.date() < close_date:
        return False
    return now_et.hour * 60 + now_et.minute >= 16 * 60


def realized_move_from_ohlcv(
    conn,
    ticker: str,
    earnings_dt: date,
    timing: str | None,
) -> float | None:
    """Timing-aware regular-session close-to-close move from local OHLCV."""
    try:
        row = conn.execute(
            """
            WITH event AS (
                SELECT
                    CAST(? AS VARCHAR) AS ticker,
                    CAST(? AS DATE) AS earnings_dt,
                    LOWER(COALESCE(CAST(? AS VARCHAR), 'unknown')) AS timing
            )
            SELECT (post.close / NULLIF(pre.close, 0) - 1.0) AS realized_move
            FROM event e
            LEFT JOIN v_ohlcv pre ON pre.act_symbol = e.ticker
                AND pre.close > 0
                AND pre.date >= e.earnings_dt - INTERVAL '5' DAY
                AND (
                    ((e.timing IN ('after_market_close', 'amc', 'after_close') OR e.timing LIKE '%after%')
                        AND pre.date <= e.earnings_dt)
                    OR (NOT (e.timing IN ('after_market_close', 'amc', 'after_close') OR e.timing LIKE '%after%')
                        AND pre.date < e.earnings_dt)
                )
            LEFT JOIN v_ohlcv post ON post.act_symbol = e.ticker
                AND post.close > 0
                AND post.date <= e.earnings_dt + INTERVAL '5' DAY
                AND (
                    ((e.timing IN ('before_market_open', 'bmo', 'before_open') OR e.timing LIKE '%before%')
                        AND post.date >= e.earnings_dt)
                    OR (NOT (e.timing IN ('before_market_open', 'bmo', 'before_open') OR e.timing LIKE '%before%')
                        AND post.date > e.earnings_dt)
                )
            QUALIFY ROW_NUMBER() OVER (
                ORDER BY pre.date DESC NULLS LAST, post.date ASC NULLS LAST
            ) = 1
            """,
            [ticker, earnings_dt, timing or "unknown"],
        ).fetchone()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    return float(row[0])


def enrich_realized_moves_from_ohlcv(conn, events: list[dict]) -> int:
    """Backfill/refresh realized_move_pct on both new and preserved week rows."""
    candidates: list[tuple[dict, str, date, str]] = []
    for ev in events:
        try:
            ticker = str(ev.get("ticker") or "").upper()
            earnings_dt = date.fromisoformat(str(ev.get("earnings_date") or "")[:10])
        except ValueError:
            continue
        if not ticker or not realization_window_complete(earnings_dt, ev.get("timing")):
            continue
        candidates.append((ev, ticker, earnings_dt, str(ev.get("timing") or "unknown")))
    if not candidates:
        return 0

    try:
        conn.execute(
            """
            CREATE OR REPLACE TEMP TABLE tmp_earnings_reaction_events (
                ticker VARCHAR,
                earnings_dt DATE,
                timing VARCHAR
            )
            """
        )
        conn.executemany(
            "INSERT INTO tmp_earnings_reaction_events VALUES (?, ?, ?)",
            [(ticker, earnings_dt, timing) for _, ticker, earnings_dt, timing in candidates],
        )
        rows = conn.execute(
            """
            SELECT ticker, earnings_dt, realized_move
            FROM (
                SELECT
                    e.ticker,
                    e.earnings_dt,
                    (post.close / NULLIF(pre.close, 0) - 1.0) AS realized_move,
                    ROW_NUMBER() OVER (
                        PARTITION BY e.ticker, e.earnings_dt
                        ORDER BY pre.date DESC NULLS LAST, post.date ASC NULLS LAST
                    ) AS rn
                FROM tmp_earnings_reaction_events e
                LEFT JOIN v_ohlcv pre ON pre.act_symbol = e.ticker
                    AND pre.close > 0
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
                    AND post.close > 0
                    AND post.date <= e.earnings_dt + INTERVAL '5' DAY
                    AND (
                        ((LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                            OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                            AND post.date >= e.earnings_dt)
                        OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                            OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                            AND post.date > e.earnings_dt)
                    )
            )
            WHERE rn = 1 AND realized_move IS NOT NULL
            """
        ).fetchall()
    except Exception:
        rows = []

    moves = {
        (str(ticker).upper(), earnings_dt): float(move)
        for ticker, earnings_dt, move in rows
        if move is not None
    }
    updated = 0
    for ev, ticker, earnings_dt, _ in candidates:
        move = moves.get((ticker, earnings_dt))
        if move is None:
            continue
        new_value = jsonable(move)
        if ev.get("realized_move_pct") != new_value:
            ev["realized_move_pct"] = new_value
            updated += 1
    return updated


def _compute_realized_from_closes(
    closes: list[tuple[date, float]],
    earnings_dt: date,
    timing: str | None,
) -> float | None:
    if not closes:
        return None
    bucket = timing_bucket(timing)
    if bucket == "amc":
        pre = [row for row in closes if row[0] <= earnings_dt]
        post = [row for row in closes if row[0] > earnings_dt]
    elif bucket == "bmo":
        pre = [row for row in closes if row[0] < earnings_dt]
        post = [row for row in closes if row[0] >= earnings_dt]
    else:
        pre = [row for row in closes if row[0] < earnings_dt]
        post = [row for row in closes if row[0] > earnings_dt]
    if not pre or not post:
        return None
    pre_close = pre[-1][1]
    post_close = post[0][1]
    if pre_close <= 0 or post_close <= 0:
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
        if ev.get("realized_move_pct") is not None:
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
        ev["realized_move_pct"] = jsonable(move)
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
