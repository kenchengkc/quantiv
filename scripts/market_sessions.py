"""Deterministic US market-session helpers backed by the canonical session contract."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_PATH = REPO_ROOT / "config" / "market_sessions.json"
EASTERN = ZoneInfo("America/New_York")
DEFAULT_CLOSE = time(16, 0)


def _session_contract(path: Path = SESSION_PATH) -> dict:
    if not path.exists():
        return {"holidays": [], "early_closes": {}}
    payload = json.loads(path.read_text())
    if payload.get("schema") != "quantiv.market-sessions.v1":
        raise RuntimeError(f"unsupported market-session schema in {path}")
    return payload


def generated_us_market_holidays(path: Path = SESSION_PATH) -> set[date]:
    payload = _session_contract(path)
    return {date.fromisoformat(value) for value in payload.get("holidays") or []}


def generated_us_market_early_closes(path: Path = SESSION_PATH) -> dict[date, time]:
    payload = _session_contract(path)
    out: dict[date, time] = {}
    for day, close in (payload.get("early_closes") or {}).items():
        hour, minute = (int(part) for part in str(close).split(":", 1))
        out[date.fromisoformat(day)] = time(hour, minute)
    return out


def is_us_market_session(
    value: date,
    *,
    holidays: set[date] | None = None,
) -> bool:
    closed = generated_us_market_holidays() if holidays is None else holidays
    return value.weekday() < 5 and value not in closed


def us_market_close(
    value: date,
    *,
    early_closes: dict[date, time] | None = None,
) -> time:
    closes = generated_us_market_early_closes() if early_closes is None else early_closes
    return closes.get(value, DEFAULT_CLOSE)


def latest_completed_us_market_session(
    now: datetime | None = None,
    *,
    holidays: set[date] | None = None,
    early_closes: dict[date, time] | None = None,
    close: time | None = None,
) -> date:
    """Return the latest session whose session-specific regular close has occurred.

    ``close`` remains as an explicit override for callers/tests that need a custom
    market close. Normal production callers should leave it unset so early-close
    dates from the canonical contract are honored.
    """
    current = now or datetime.now(EASTERN)
    eastern = current.astimezone(EASTERN) if current.tzinfo else current.replace(tzinfo=EASTERN)
    candidate = eastern.date()
    candidate_close = close or us_market_close(candidate, early_closes=early_closes)
    if not is_us_market_session(candidate, holidays=holidays) or eastern.time() < candidate_close:
        candidate -= timedelta(days=1)
    while not is_us_market_session(candidate, holidays=holidays):
        candidate -= timedelta(days=1)
    return candidate


def market_session_lag(
    source_date: date,
    expected_date: date,
    *,
    holidays: set[date] | None = None,
) -> int:
    """Count completed market sessions missing after ``source_date``."""
    if source_date > expected_date:
        return -1
    lag = 0
    cursor = source_date + timedelta(days=1)
    while cursor <= expected_date:
        if is_us_market_session(cursor, holidays=holidays):
            lag += 1
        cursor += timedelta(days=1)
    return lag
