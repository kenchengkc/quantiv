#!/usr/bin/env python3
"""Detect tickers that have dropped off the US exchange directories.

A symbol in our active universe (earnings_calendar.csv US rows ∪ generated
forecast pages) that disappears from the authoritative NASDAQ/NYSE listing
files is a delisting — an acquisition, merger, bankruptcy, or going-private.
Those directories are the exchanges' own daily-updated source of truth, far
cleaner than inferring delistings from gaps in DoltHub/Finnhub feeds.

To avoid acting on a one-day directory glitch or a symbol rename, a candidate
must be absent for several consecutive days (--threshold) before it is promoted
into config/delisted_tickers.json. Reappearing in the directories clears the
watch. State persists in data/delisting_watch.json across nightly runs.

Run in the daily refresh after scripts/build_ticker_names.mjs and before the
integrity gate, so a newly promoted delisting is honored in the same commit:
  python scripts/detect_delistings.py            # fetch, update watch, auto-promote
  python scripts/detect_delistings.py --dry-run  # preview only, no writes
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from delisted import delisted_tickers  # noqa: E402
from sync_finnhub_earnings import is_us_symbol  # noqa: E402
from check_ticker_identity import norm_tokens  # noqa: E402

SYMBOLS_DIR = REPO_ROOT / "apps" / "frontend" / "public" / "symbols"
NAMES_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "ticker-names.json"
DELISTED_PATH = REPO_ROOT / "config" / "delisted_tickers.json"
WATCH_PATH = REPO_ROOT / "data" / "delisting_watch.json"

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SYMBOL_DIR_HEADERS = {
    "User-Agent": "Quantiv delisting detector (ken@quantiv.app)",
    "Accept": "text/plain,*/*",
}
DEFAULT_THRESHOLD_DAYS = 3


def normalize(symbol: str) -> str:
    """Match build_ticker_names.mjs: uppercase, trim, '.' → '-' (BRK.B → BRK-B)."""
    return symbol.strip().upper().replace(".", "-")


def today_iso() -> str:
    return date.today().isoformat()


def fetch_pipe_table(url: str) -> list[dict[str, str]]:
    resp = requests.get(url, headers=SYMBOL_DIR_HEADERS, timeout=30)
    resp.raise_for_status()
    lines = [
        ln for ln in resp.text.splitlines()
        if ln and not ln.startswith("File Creation Time")
    ]
    if not lines:
        return []
    header = lines[0].split("|")
    rows = []
    for ln in lines[1:]:
        cells = ln.split("|")
        rows.append({h: (cells[i] if i < len(cells) else "") for i, h in enumerate(header)})
    return rows


def fetch_directory() -> tuple[set[str], list[str]]:
    """Return (currently-listed US symbols, security-name strings) from both
    NASDAQ Trader directory files. The name corpus lets us tell a real
    delisting apart from a ticker rename (same company, new symbol)."""
    listed: set[str] = set()
    names: list[str] = []
    for url, sym_col in ((NASDAQ_LISTED_URL, "Symbol"), (OTHER_LISTED_URL, "ACT Symbol")):
        for row in fetch_pipe_table(url):
            if row.get("Test Issue") == "Y":
                continue
            sym = normalize(row.get(sym_col, ""))
            if not sym:
                continue
            listed.add(sym)
            name = (row.get("Security Name") or "").strip()
            if name:
                names.append(name)
    return listed, names


def load_names() -> dict[str, str]:
    try:
        payload = json.loads(NAMES_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_rename_finder(directory_names: list[str]):
    """Return a fn mapping a company name → True if that company still appears
    in the exchange directories (i.e. the ticker was renamed, not delisted).

    Matches on distinctive token overlap so 'Bank of New York Mellon' (BK)
    still resolves to the 'The Bank of New York Mellon Corporation' row now
    listed as BNY. Requires ≥2 shared significant tokens and ≥60% overlap to
    avoid matching on a single common word like 'American' or 'Holdings'.
    """
    corpus = [toks for name in directory_names if (toks := norm_tokens(name))]

    def still_listed(company_name: str) -> bool:
        a = norm_tokens(company_name)
        if len(a) < 2:
            return False  # too generic to disambiguate safely
        for b in corpus:
            overlap = len(a & b)
            if overlap >= 2 and overlap / min(len(a), len(b)) >= 0.6:
                return True
        return False

    return still_listed


def load_active_universe() -> set[str]:
    """US tickers we *actively display*: the generated forecast pages.

    Deliberately NOT the full earnings_calendar.csv — that's a multi-year
    archive carrying thousands of long-dead tickers (ABMD, ABC, ADS, …) whose
    absence from the directories is old news, not actionable. A forecast page
    exists only for a name with recent/upcoming earnings that the frontend
    renders a detail page for (CTRA had one), so a forecast-page ticker missing
    from the exchange directories is a genuine, recent delisting.
    """
    if not SYMBOLS_DIR.exists():
        return set()
    return {
        normalize(path.stem)
        for path in SYMBOLS_DIR.glob("*.json")
        if is_us_symbol(path.stem)
    }


def load_watch() -> dict[str, dict]:
    try:
        payload = json.loads(WATCH_PATH.read_text())
    except (OSError, ValueError):
        return {}
    watched = payload.get("watching") if isinstance(payload, dict) else None
    return watched if isinstance(watched, dict) else {}


def write_watch(watching: dict[str, dict]) -> None:
    WATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "nasdaqtrader.com SymDir (nasdaqlisted + otherlisted)",
        "watching": dict(sorted(watching.items())),
    }
    WATCH_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def promote_to_delisted(tickers: list[str], threshold: int) -> None:
    """Append confirmed delistings to config/delisted_tickers.json, preserving
    the comment and any existing entries."""
    try:
        payload = json.loads(DELISTED_PATH.read_text())
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    table = payload.setdefault("tickers", {})
    if not isinstance(table, dict):
        table = payload["tickers"] = {}
    for ticker in tickers:
        if ticker in table:
            continue
        table[ticker] = {
            "reason": (
                "Auto-detected: absent from NASDAQ/NYSE exchange directories "
                f"for ≥{threshold} consecutive days (likely M&A, bankruptcy, "
                "or going-private)."
            ),
            "delisted_on": today_iso(),
            "source": "auto: scripts/detect_delistings.py",
        }
    payload["tickers"] = dict(sorted(table.items()))
    DELISTED_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD_DAYS,
        help=f"Consecutive missing days before auto-promotion (default {DEFAULT_THRESHOLD_DAYS}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only; do not update the watch file or delisted list.",
    )
    parser.add_argument(
        "--allow-fetch-failure",
        action="store_true",
        help="Exit 0 (no changes) if the directories can't be fetched. Default in CI.",
    )
    args = parser.parse_args()

    try:
        listed, directory_names = fetch_directory()
    except requests.RequestException as exc:
        print(f"⚠ Could not fetch exchange directories: {exc}")
        return 0 if args.allow_fetch_failure else 1
    if len(listed) < 1000:
        # Sanity floor: the real directories carry ~8k symbols. A tiny result
        # means a malformed/partial fetch — never act on it.
        print(f"⚠ Only {len(listed):,} listed symbols parsed; skipping (looks malformed).")
        return 0 if args.allow_fetch_failure else 1

    universe = load_active_universe()
    names = load_names()
    already = {normalize(t) for t in delisted_tickers()}
    missing_now = universe - listed - already

    # Split missing tickers into ticker RENAMES (the company still trades under
    # a new symbol — e.g. BK→BNY, EXPI→AGNT) and genuine disappearances. Only
    # the latter are auto-promotable; renames need a symbol remap, not removal,
    # and would otherwise be wrongly purged.
    still_listed = build_rename_finder(directory_names)
    renames: dict[str, str] = {}      # ticker → our known company name
    genuine: set[str] = set()
    for ticker in missing_now:
        company = names.get(ticker) or names.get(ticker.replace("-", "."))
        if company and still_listed(company):
            renames[ticker] = company
        else:
            genuine.add(ticker)

    watching = load_watch()
    today = today_iso()
    promoted: list[str] = []
    cleared: list[str] = []

    # Clear any watched ticker that reappeared in the directories, fell out of
    # our universe, or is now classified as a rename rather than a delisting.
    for ticker in list(watching):
        if ticker not in genuine:
            cleared.append(ticker)
            del watching[ticker]

    # Update / start watch entries for genuinely-missing tickers. Increment the
    # day count at most once per calendar day so a re-run can't fast-track.
    for ticker in sorted(genuine):
        entry = watching.get(ticker)
        if entry is None:
            watching[ticker] = {
                "first_missing": today,
                "last_checked": today,
                "days_missing": 1,
            }
        elif entry.get("last_checked") != today:
            entry["days_missing"] = int(entry.get("days_missing", 0)) + 1
            entry["last_checked"] = today
        if watching[ticker]["days_missing"] >= args.threshold:
            promoted.append(ticker)

    # ── Report ──────────────────────────────────────────────────────────
    print("DELISTING DETECTOR")
    print(f"  listed symbols (NASDAQ/NYSE): {len(listed):,}")
    print(f"  active universe (US):         {len(universe):,}")
    print(f"  already delisted:             {len(already):,}")
    print(f"  missing from directories:     {len(missing_now):,}")
    if renames:
        print("  ✋ likely ticker renames (review/remap — NOT auto-delisted):")
        for t in sorted(renames):
            print(f"       {t:<8} {renames[t]} (company still listed under a new symbol)")
    if cleared:
        print(f"  ↩ reappeared / cleared:       {sorted(cleared)}")
    pending = sorted(
        (t for t in watching if t not in promoted),
        key=lambda t: -watching[t]["days_missing"],
    )
    if pending:
        print("  ⏳ on watch (below threshold):")
        for t in pending:
            w = watching[t]
            print(f"       {t:<8} {w['days_missing']}d (since {w['first_missing']})")
    if promoted:
        print(f"  🚫 PROMOTING to delisted (≥{args.threshold}d): {sorted(promoted)}")

    if args.dry_run:
        print("  (dry run — no files written)")
        return 0

    if promoted:
        promote_to_delisted(sorted(promoted), args.threshold)
        # Promoted tickers leave the active watch — they're now in the
        # delisted list and filtered at the source.
        for ticker in promoted:
            watching.pop(ticker, None)
        print(f"  ✏ updated {DELISTED_PATH.relative_to(REPO_ROOT)}")
    write_watch(watching)
    print(f"  ✏ updated {WATCH_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
