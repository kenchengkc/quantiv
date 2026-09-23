from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from market_sessions import is_us_market_session

QUOTE_REFRESH_OPEN_MIN = 9 * 60 + 25
QUOTE_REFRESH_CLOSE_MIN = 16 * 60 + 45


def require_premarket_refresh_window(
    max_runtime_minutes: int, *, now: datetime | None = None
) -> datetime:
    """Admit a daily job only if its entire timeout fits before 08:30 ET.

    The workflow's job timeout enforces the runtime budget after admission.
    This applies to scheduled and manual jobs, including weekends, and protects
    every provider used by the pipeline rather than only individual Finnhub calls.
    """
    if max_runtime_minutes <= 0:
        raise ValueError("The refresh runtime budget must be positive")
    instant = now if now is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("The refresh clock must be timezone-aware")
    eastern_now = instant.astimezone(ZoneInfo("America/New_York"))
    deadline = eastern_now.replace(hour=8, minute=30, second=0, microsecond=0)
    # Measure elapsed time in UTC so DST jumps cannot extend the allowed window.
    latest_finish = instant.astimezone(timezone.utc) + timedelta(minutes=max_runtime_minutes)
    if latest_finish > deadline.astimezone(timezone.utc):
        raise SystemExit(
            f"Refusing daily refresh at {eastern_now.isoformat()}: the full "
            f"{max_runtime_minutes}-minute job timeout must fit before 08:30 ET. "
            "Run overnight; market-hours capacity is reserved for live quotes."
        )
    return deadline


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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Enforce the daily refresh premarket window.")
    parser.add_argument("--max-runtime-minutes", type=int, required=True)
    args = parser.parse_args()
    cutoff = require_premarket_refresh_window(args.max_runtime_minutes)
    print(f"Daily refresh admitted: full job timeout fits before {cutoff.isoformat()}")
