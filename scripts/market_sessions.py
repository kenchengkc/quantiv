"""Deterministic US market-session helpers backed by the checked-in holiday cache."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
HOLIDAY_PATH = REPO_ROOT / "apps" / "frontend" / "lib" / "marketHolidays.generated.ts"
EASTERN = ZoneInfo("America/New_York")


def generated_us_market_holidays(path: Path = HOLIDAY_PATH) -> set[date]:
    if not path.exists():
        return set()
    match = re.search(
        r"MARKET_HOLIDAYS_US\s*=\s*\[(.*?)\]\s+as const",
        path.read_text(),
        re.S,
    )
    if not match:
        return set()
    return {
        date.fromisoformat(value)
        for value in re.findall(r"""["'](\d{4}-\d{2}-\d{2})["']""", match.group(1))
    }


def is_us_market_session(
    value: date,
    *,
    holidays: set[date] | None = None,
) -> bool:
    closed = generated_us_market_holidays() if holidays is None else holidays
    return value.weekday() < 5 and value not in closed


def latest_completed_us_market_session(
    now: datetime | None = None,
    *,
    holidays: set[date] | None = None,
    close: time = time(16, 0),
) -> date:
    """Return the latest session whose regular close has already occurred."""
    current = now or datetime.now(EASTERN)
    eastern = current.astimezone(EASTERN) if current.tzinfo else current.replace(tzinfo=EASTERN)
    candidate = eastern.date()
    if not is_us_market_session(candidate, holidays=holidays) or eastern.time() < close:
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
