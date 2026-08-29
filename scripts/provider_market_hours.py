from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from market_sessions import is_us_market_session

QUOTE_REFRESH_OPEN_MIN = 9 * 60 + 25
QUOTE_REFRESH_CLOSE_MIN = 16 * 60 + 45


def is_finnhub_reserved_window(now: datetime | None = None) -> bool:
    """True when Finnhub capacity should be reserved for live quote polling."""
    et_now = (now or datetime.now(ZoneInfo("America/New_York"))).astimezone(
        ZoneInfo("America/New_York")
    )
    if not is_us_market_session(et_now.date()):
        return False
    minutes = et_now.hour * 60 + et_now.minute
    return QUOTE_REFRESH_OPEN_MIN <= minutes <= QUOTE_REFRESH_CLOSE_MIN


def block_finnhub_reserved_window(allow_market_hours: bool = False) -> None:
    if allow_market_hours or not is_finnhub_reserved_window():
        return
    # Soft-skip for the scheduled daily refresh. GitHub's schedule-dispatch
    # jitter (often 1-3h at the top of the hour) and a slow DoltHub merge can
    # push these non-price Finnhub steps past 09:25 ET. Aborting there (exit 1)
    # kills the whole pipeline — scoring, the frontend rebuild, and the commit
    # never run, so the site data goes stale. When FINNHUB_RESERVED_WINDOW_SOFT_SKIP
    # is set we instead skip just this enrichment and exit 0 so the rest of the
    # pipeline completes; the overlay is picked up on the next clean run.
    if os.getenv("FINNHUB_RESERVED_WINDOW_SOFT_SKIP") == "1":
        print(
            "↷ Skipping non-price Finnhub call during the quote refresh window "
            "(09:25-16:45 ET) — FINNHUB_RESERVED_WINDOW_SOFT_SKIP=1.",
            flush=True,
        )
        raise SystemExit(0)
    raise SystemExit(
        "Refusing non-price Finnhub calls during the quote refresh window "
        "(09:25-16:45 ET). Re-run outside market hours, or pass "
        "--allow-market-hours for a deliberate override."
    )
