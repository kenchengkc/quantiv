from __future__ import annotations

import os
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from market_sessions import is_us_market_session

QUOTE_REFRESH_OPEN_MIN = 9 * 60 + 25
QUOTE_REFRESH_CLOSE_MIN = 16 * 60 + 45


def require_premarket_refresh_window(*, now: datetime | None = None) -> datetime:
    """Admit starts before 09:00 ET and return the same day's 09:25 deadline."""
    instant = now if now is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("The refresh clock must be timezone-aware")
    eastern_now = instant.astimezone(ZoneInfo("America/New_York"))
    if eastern_now.hour >= 9:
        raise SystemExit(
            f"Refusing daily refresh at {eastern_now.isoformat()}: starts at or "
            "after 09:00 ET are rejected. "
            "Run overnight; market-hours capacity is reserved for live quotes."
        )
    return eastern_now.replace(hour=9, minute=25, second=0, microsecond=0)


def run_refresh_shell(script: Path, deadline: datetime) -> int:
    """Run an Actions step, killing its process group at the admitted deadline."""
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        raise ValueError("The refresh deadline must be timezone-aware")
    remaining = (deadline.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        print("Refusing refresh step: the 09:25 ET provider cutoff has passed.", flush=True)
        return 124
    process = subprocess.Popen(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", str(script)],
        start_new_session=True,
    )
    try:
        return process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        print("Stopping refresh: the 09:25 ET provider cutoff has arrived.", flush=True)
        return 124
    finally:
        # Kill descendants as well as the shell, including any background work.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--admit", action="store_true")
    mode.add_argument("--run-shell", type=Path)
    args = parser.parse_args()
    if args.admit:
        cutoff = require_premarket_refresh_window()
        with Path(os.environ["GITHUB_ENV"]).open("a") as handle:
            handle.write(f"REFRESH_DEADLINE_UTC={cutoff.astimezone(timezone.utc).isoformat()}\n")
        print(f"Daily refresh admitted; provider cutoff: {cutoff.isoformat()}")
    else:
        deadline_value = os.environ.get("REFRESH_DEADLINE_UTC")
        if not deadline_value:
            raise SystemExit("Missing refresh admission; refusing to run a provider step.")
        raise SystemExit(run_refresh_shell(args.run_shell, datetime.fromisoformat(deadline_value)))
