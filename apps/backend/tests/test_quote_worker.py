from datetime import datetime
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))

from workers.quote_worker import (  # noqa: E402
    is_quote_window,
    monday_iso_for,
    normalize_symbol,
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
