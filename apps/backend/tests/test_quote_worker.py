from datetime import datetime
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))

from workers.quote_worker import (  # noqa: E402
    PREVIOUS_CLOSE_CACHE_MAX_AGE_S,
    QuoteWorkerState,
    cached_previous_close,
    is_quote_window,
    monday_iso_for,
    normalize_symbol,
    reset_previous_close_session,
    score_week_events,
)


ET = ZoneInfo("America/New_York")


def test_normalize_symbol_rejects_bad_values():
    assert normalize_symbol(" crm ") == "CRM"
    assert normalize_symbol("BRK.B") == "BRK.B"
    assert normalize_symbol("bad/value") is None
    assert normalize_symbol("") is None


def test_quote_window_respects_weekends_and_holidays():
    assert is_quote_window(
        datetime(2026, 5, 26, 10, 0, tzinfo=ET),
        holidays=set(),
    )
    assert not is_quote_window(
        datetime(2026, 5, 26, 8, 0, tzinfo=ET),
        holidays=set(),
    )
    assert not is_quote_window(
        datetime(2026, 5, 30, 10, 0, tzinfo=ET),
        holidays=set(),
    )
    assert not is_quote_window(
        datetime(2026, 5, 25, 10, 0, tzinfo=ET),
        holidays={"2026-05-25"},
    )


def test_monday_iso_for_week_dates():
    assert monday_iso_for("2026-05-26") == "2026-05-25"
    assert monday_iso_for("2026-05-31") == "2026-05-25"


def test_score_week_events_prioritizes_today_and_tomorrow():
    scores: dict[str, float] = {}
    score_week_events(
        scores,
        [
            {"ticker": "CRM", "earnings_date": "2026-05-26"},
            {"ticker": "NVDA", "earnings_date": "2026-05-27"},
            {"ticker": "AAPL", "earnings_date": "2026-05-29"},
        ],
        today_iso="2026-05-26",
        weight=55,
    )
    assert scores["CRM"] > scores["NVDA"] > scores["AAPL"]


def test_cached_previous_close_only_uses_recent_rest_quotes():
    now_ms = 1_800_000
    assert (
        cached_previous_close(
            '{"at": 1200000, "transport": "rest", "tick": {"previousClose": 295.19}}',
            now_ms=now_ms,
            session_date="2026-05-26",
        )
        == 295.19
    )
    assert (
        cached_previous_close(
            '{"at": 1200000, "transport": "rest", "sessionDate": "2026-05-22", "tick": {"previousClose": 252.8}}',
            now_ms=now_ms,
            session_date="2026-05-26",
        )
        is None
    )
    assert (
        cached_previous_close(
            '{"at": 1200000, "transport": "websocket", "tick": {"previousClose": 252.8}}',
            now_ms=now_ms,
            session_date="2026-05-26",
        )
        is None
    )
    stale_ms = now_ms - (PREVIOUS_CLOSE_CACHE_MAX_AGE_S + 1) * 1000
    assert (
        cached_previous_close(
            f'{{"at": {stale_ms}, "transport": "rest", "tick": {{"previousClose": 295.19}}}}',
            now_ms=now_ms,
            session_date="2026-05-26",
        )
        is None
    )


def test_previous_close_session_reset_clears_stale_values():
    state = QuoteWorkerState(
        previous_close={"DELL": 252.8},
        previous_close_session_date="2026-05-22",
        missing_previous_close_cursor=4,
    )

    assert reset_previous_close_session(state, "2026-05-26")
    assert state.previous_close == {}
    assert state.previous_close_session_date == "2026-05-26"
    assert state.missing_previous_close_cursor == 0

    state.previous_close["DELL"] = 295.19
    assert not reset_previous_close_session(state, "2026-05-26")
    assert state.previous_close == {"DELL": 295.19}
