from datetime import date, datetime
from zoneinfo import ZoneInfo

from frontend_data.realized_moves import (
    MARKET_EARLY_CLOSES,
    realization_window_complete,
)

ET = ZoneInfo("America/New_York")


def test_realized_move_window_uses_canonical_early_close() -> None:
    early_close_day = date(2026, 11, 27)
    assert MARKET_EARLY_CLOSES[early_close_day] == 13 * 60
    assert not realization_window_complete(
        early_close_day,
        "bmo",
        datetime(2026, 11, 27, 12, 59, tzinfo=ET),
    )
    assert realization_window_complete(
        early_close_day,
        "bmo",
        datetime(2026, 11, 27, 13, 0, tzinfo=ET),
    )
