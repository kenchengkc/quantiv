from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from market_sessions import (  # noqa: E402
    generated_us_market_early_closes,
    latest_completed_us_market_session,
    market_session_lag,
    us_market_close,
)

EASTERN = ZoneInfo("America/New_York")
HOLIDAYS = {date(2026, 9, 7)}


def test_latest_completed_session_handles_weekend_and_premarket() -> None:
    assert latest_completed_us_market_session(
        datetime(2026, 8, 29, 12, tzinfo=EASTERN), holidays=HOLIDAYS
    ) == date(2026, 8, 28)
    assert latest_completed_us_market_session(
        datetime(2026, 8, 31, 7, tzinfo=EASTERN), holidays=HOLIDAYS
    ) == date(2026, 8, 28)
    assert latest_completed_us_market_session(
        datetime(2026, 8, 31, 17, tzinfo=EASTERN), holidays=HOLIDAYS
    ) == date(2026, 8, 31)


def test_latest_completed_session_skips_holiday() -> None:
    assert latest_completed_us_market_session(
        datetime(2026, 9, 8, 7, tzinfo=EASTERN), holidays=HOLIDAYS
    ) == date(2026, 9, 4)


def test_market_session_lag_counts_sessions_not_calendar_days() -> None:
    assert market_session_lag(
        date(2026, 9, 4), date(2026, 9, 8), holidays=HOLIDAYS
    ) == 1
    assert market_session_lag(
        date(2026, 9, 8), date(2026, 9, 8), holidays=HOLIDAYS
    ) == 0
    assert market_session_lag(
        date(2026, 9, 9), date(2026, 9, 8), holidays=HOLIDAYS
    ) == -1


def test_canonical_contract_exposes_early_close_and_completes_at_1300() -> None:
    early_closes = generated_us_market_early_closes()
    early_close_day = date(2026, 11, 27)
    assert early_closes[early_close_day] == time(13, 0)
    assert us_market_close(early_close_day) == time(13, 0)

    assert latest_completed_us_market_session(
        datetime(2026, 11, 27, 12, 59, tzinfo=EASTERN)
    ) == date(2026, 11, 25)
    assert latest_completed_us_market_session(
        datetime(2026, 11, 27, 13, 0, tzinfo=EASTERN)
    ) == early_close_day
