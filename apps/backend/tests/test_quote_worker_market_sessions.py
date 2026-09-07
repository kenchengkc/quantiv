from datetime import datetime
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))

from workers.quote_worker import is_quote_window, load_market_sessions  # noqa: E402

ET = ZoneInfo("America/New_York")


def test_quote_worker_reads_canonical_session_contract() -> None:
    holidays, early_closes = load_market_sessions()
    assert "2026-09-07" in holidays
    assert early_closes["2026-11-27"] == 13 * 60


def test_quote_worker_stops_after_early_close_settle_window() -> None:
    # Friday after Thanksgiving 2026 closes at 13:00 ET. The quote writer keeps
    # the same 45-minute settle window used on normal sessions, then yields its
    # single-writer lease instead of polling until the normal 16:45 cutoff.
    assert is_quote_window(datetime(2026, 11, 27, 13, 44, tzinfo=ET))
    assert is_quote_window(datetime(2026, 11, 27, 13, 45, tzinfo=ET))
    assert not is_quote_window(datetime(2026, 11, 27, 13, 46, tzinfo=ET))
