#!/usr/bin/env python3
"""Guard against display-name vs issuer-metadata mismatches in the UI.

Compares SEC/Nasdaq display names (ticker-names.json) to Finnhub /stock/profile2
names in apps/frontend/public/ticker-logos.json. When they disagree, the UI can
show the right company name with the wrong logo because Parqet resolves bare
symbols globally (NA → National Bank of Canada while US NA is Nano Labs).

Also flags high-risk symbols (very short tickers) that lack a cached Finnhub logo,
since the browser will fall back to Parqet for those rows.

Run locally:
  python scripts/check_ticker_identity.py
  python scripts/check_ticker_identity.py --warn-only
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NAMES_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "ticker-names.json"
LOGOS_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "ticker-logos.json"
EARNINGS_CSV = REPO_ROOT / "data" / "earnings_calendar.csv"

# Keep in sync with scripts/sync_finnhub_earnings.py (import avoided — pyarrow dep).
FOREIGN_EXCHANGE_SUFFIXES = frozenset({
    "AX", "BC", "BR", "CN", "CO", "DE", "HE", "HK", "IR", "IS", "JK", "JO", "KS",
    "L", "MC", "MI", "MX", "NE", "NS", "OL", "PA", "PM", "SA", "SN", "SS", "ST",
    "SW", "T", "TA", "TO", "TW", "V",
})


def is_us_symbol(value: object) -> bool:
    s = str(value or "").strip().upper()
    if not s or ":" in s:
        return False
    if "." in s:
        return s.rsplit(".", 1)[-1] not in FOREIGN_EXCHANGE_SUFFIXES
    return True

LEGAL_SUFFIX_RE = re.compile(
    r",?\s+(?:Inc\.?|Incorporated|Corp(?:oration)?\.?|(?:&\s+)?Co(?:mpany|mpanies)?\.?|"
    r"Ltd\.?|Limited|Holdings?|Group|LLC|PLC)$",
    re.I,
)

# Bare symbols Parqet often maps to a non-US listing; require Finnhub logo cache.
PARQET_RISKY_SYMBOLS = frozenset({"NA"})


def strip_legal_suffix(name: str) -> str:
    prev = name.strip()
    while True:
        nxt = LEGAL_SUFFIX_RE.sub("", prev).strip()
        if nxt == prev:
            return nxt
        prev = nxt


def norm_tokens(name: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9]+", " ", strip_legal_suffix(name).lower())
    return {t for t in cleaned.split() if len(t) > 2}


def names_align(display: str, finnhub: str) -> bool:
    a, b = norm_tokens(display), norm_tokens(finnhub)
    if not a or not b:
        return strip_legal_suffix(display).lower() == strip_legal_suffix(finnhub).lower()
    overlap = len(a & b)
    return overlap >= 1 and overlap / min(len(a), len(b)) >= 0.5


def load_earnings_symbols() -> set[str]:
    if not EARNINGS_CSV.exists():
        return set()
    out: set[str] = set()
    with EARNINGS_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("act_symbol") or "").strip().upper()
            if sym and is_us_symbol(sym):
                out.add(sym)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Print issues but exit 0 (for optional CI steps).",
    )
    parser.add_argument(
        "--max-name-mismatches",
        type=int,
        default=0,
        help="Allowed profile-name mismatches before failing (default: 0).",
    )
    parser.add_argument(
        "--max-missing-logos",
        type=int,
        default=0,
        help="Allowed risky symbols without Finnhub logos (default: 0).",
    )
    args = parser.parse_args()

    if not NAMES_PATH.exists():
        print(f"✗ Missing {NAMES_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1

    display_names: dict[str, str] = json.loads(NAMES_PATH.read_text())
    logos: dict[str, str] = {}
    profiles: dict[str, dict] = {}
    if LOGOS_PATH.exists():
        cache = json.loads(LOGOS_PATH.read_text())
        logos = {
            k: v
            for k, v in dict(cache.get("logos", {})).items()
            if isinstance(k, str) and isinstance(v, str) and v.startswith("http")
        }
        profiles = {
            k: v
            for k, v in dict(cache.get("profiles", {})).items()
            if isinstance(k, str) and isinstance(v, dict)
        }

    symbols = load_earnings_symbols()
    # Only symbols with known Parqet bare-ticker collisions — not every 1-letter ticker.
    risky = PARQET_RISKY_SYMBOLS & symbols

    name_mismatches: list[tuple[str, str, str]] = []
    for sym in sorted(symbols):
        profile = profiles.get(sym)
        if not profile or profile.get("has_profile") is False:
            continue
        fh_name = str(profile.get("name") or "").strip()
        if not fh_name:
            continue
        display = display_names.get(sym, sym)
        if not names_align(display, fh_name):
            name_mismatches.append((sym, display, fh_name))

    missing_logos = sorted(sym for sym in risky if sym not in logos)

    print("TICKER IDENTITY CHECK")
    print(f"  earnings symbols (US): {len(symbols):,}")
    print(f"  cached profiles:       {len(profiles):,}")
    print(f"  cached logos:          {len(logos):,}")
    print(f"  name mismatches:       {len(name_mismatches):,}")
    print(f"  risky w/o Finnhub logo:{len(missing_logos):,}")

    tripped: list[str] = []
    if len(name_mismatches) > args.max_name_mismatches:
        sample = name_mismatches[:15]
        tripped.append(
            f"{len(name_mismatches):,} symbols have Finnhub profile names that "
            f"don't match ticker-names.json (threshold {args.max_name_mismatches}). "
            f"Sample: {sample}"
        )
    if len(missing_logos) > args.max_missing_logos:
        tripped.append(
            f"{len(missing_logos):,} Parqet-risky symbols lack Finnhub logos "
            f"(threshold {args.max_missing_logos}): {missing_logos}"
        )

    if tripped:
        for msg in tripped:
            print(f"✗ {msg}", file=sys.stderr)
        if args.warn_only:
            print("⚠ warn-only: exiting 0")
            return 0
        return 1

    print("✅ Ticker identity check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
