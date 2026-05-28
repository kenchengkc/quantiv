#!/usr/bin/env python3
"""Guard against foreign-ticker / display-metadata mismatches in the UI.

Primary concern: foreign listings that share a bare symbol with a US name
(DoltHub rows without .TO/.L suffix, or logo CDNs that resolve globally).
Those show up as US rows in earnings_calendar.csv but Parqet (and sometimes
upstream feeds) attach the foreign issuer's logo while ticker-names.json
carries the US SEC name.

Checks (using cached Finnhub /stock/profile2 data in ticker-logos.json):
  1. No foreign-exchange suffix rows in the committed earnings CSV.
  2. No US-filtered symbols whose Finnhub profile is clearly non-US.
  3. No large gaps between SEC display names and Finnhub profile names.

Run with the project venv (pyarrow, shared script imports):
  source .venv/bin/activate
  python scripts/check_ticker_identity.py
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

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from sync_finnhub_earnings import is_us_symbol  # noqa: E402

US_COUNTRY_CODES = frozenset({"US", "USA", "UNITED STATES"})

# Exchange substrings that indicate a non-US listing in Finnhub profile2 text.
FOREIGN_EXCHANGE_MARKERS = (
    "TORONTO",
    "TSX",
    "NEO EXCHANGE",
    "LONDON",
    "LSE",
    "XETRA",
    "FRANKFURT",
    "PARIS",
    "EURONEXT",
    "HONG KONG",
    "SEHK",
    "SHANGHAI",
    "SHENZHEN",
    "TOKYO",
    "TSE",
    "JPX",
    "ASX",
    "SWISS",
    "SIX",
    "BOMBAY",
    "NSE INDIA",
    "KOSPI",
    "KOREA",
    "TAIWAN",
    "TWSE",
    "SINGAPORE",
    "SGX",
    "MILAN",
    "BORSA",
    "MADRID",
    "MEXICO",
    "SAO PAULO",
    "BOVESPA",
    "JOHANNESBURG",
    "JSE",
    "OSLO",
    "STOCKHOLM",
    "COPENHAGEN",
    "HELSINKI",
    "ISTANBUL",
    "TEL AVIV",
    "TASE",
)

US_EXCHANGE_MARKERS = (
    "NASDAQ",
    "NYSE",
    "AMEX",
    "ARCA",
    "BATS",
    "OTC",
    "NYSE MKT",
    "CBOE BZX",
)

# Bare US tickers where global logo CDNs often pick a foreign listing (same symbol).
BARE_SYMBOL_LOGO_COLLISIONS = frozenset({"NA"})

LEGAL_SUFFIX_RE = re.compile(
    r",?\s+(?:Inc\.?|Incorporated|Corp(?:oration)?\.?|(?:&\s+)?Co(?:mpany|mpanies)?\.?|"
    r"Ltd\.?|Limited|Holdings?|Group|LLC|PLC)$",
    re.I,
)


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


def profile_looks_foreign(profile: dict) -> bool:
    if profile.get("has_profile") is False:
        return False
    country = str(profile.get("country") or "").strip().upper()
    if country and country not in US_COUNTRY_CODES:
        return True
    exchange = str(profile.get("exchange") or "").upper()
    if not exchange:
        return False
    if any(marker in exchange for marker in FOREIGN_EXCHANGE_MARKERS):
        return True
    if any(marker in exchange for marker in US_EXCHANGE_MARKERS):
        return False
    # Unrecognized exchange label with no US country — treat as foreign.
    return bool(country) and country not in US_COUNTRY_CODES


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


def count_foreign_suffix_rows() -> tuple[int, list[str]]:
    if not EARNINGS_CSV.exists():
        return 0, []
    seen: list[str] = []
    count = 0
    with EARNINGS_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("act_symbol") or "").strip().upper()
            if sym and not is_us_symbol(sym):
                count += 1
                if sym not in seen:
                    seen.append(sym)
    return count, seen[:20]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Print issues but exit 0 (for optional CI steps).",
    )
    parser.add_argument(
        "--max-foreign-rows",
        type=int,
        default=0,
        help="Allowed earnings rows with foreign-exchange suffixes (default: 0).",
    )
    parser.add_argument(
        "--max-foreign-profiles",
        type=int,
        default=0,
        help="Allowed US-universe symbols with non-US Finnhub profiles (default: 0).",
    )
    parser.add_argument(
        "--max-name-mismatches",
        type=int,
        default=0,
        help="Allowed profile-name mismatches vs ticker-names.json (default: 0).",
    )
    parser.add_argument(
        "--max-missing-logos",
        type=int,
        default=0,
        help="Allowed bare-symbol collision tickers without Finnhub logos (default: 0).",
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
    foreign_rows, foreign_sample = count_foreign_suffix_rows()
    logo_collisions = BARE_SYMBOL_LOGO_COLLISIONS & symbols

    foreign_profiles: list[tuple[str, str, str]] = []
    name_mismatches: list[tuple[str, str, str]] = []
    for sym in sorted(symbols):
        profile = profiles.get(sym)
        if not profile or profile.get("has_profile") is False:
            continue
        fh_name = str(profile.get("name") or "").strip()
        country = str(profile.get("country") or "").strip()
        exchange = str(profile.get("exchange") or "").strip()
        if profile_looks_foreign(profile):
            foreign_profiles.append((sym, country or "?", exchange or fh_name or "?"))
            continue
        if fh_name:
            display = display_names.get(sym, sym)
            if not names_align(display, fh_name):
                name_mismatches.append((sym, display, fh_name))

    missing_logos = sorted(sym for sym in logo_collisions if sym not in logos)

    print("TICKER IDENTITY CHECK")
    print(f"  earnings symbols (US):     {len(symbols):,}")
    print(f"  foreign-suffix CSV rows:   {foreign_rows:,}")
    print(f"  cached Finnhub profiles:   {len(profiles):,}")
    print(f"  cached Finnhub logos:      {len(logos):,}")
    print(f"  foreign profile leaks:     {len(foreign_profiles):,}")
    print(f"  name mismatches:           {len(name_mismatches):,}")
    print(f"  bare-symbol w/o logo:      {len(missing_logos):,}")

    tripped: list[str] = []
    if foreign_rows > args.max_foreign_rows:
        tripped.append(
            f"{foreign_rows:,} earnings_calendar rows use foreign-exchange suffixes "
            f"(threshold {args.max_foreign_rows}). Sample symbols: {foreign_sample}"
        )
    if len(foreign_profiles) > args.max_foreign_profiles:
        tripped.append(
            f"{len(foreign_profiles):,} US-universe symbols have non-US Finnhub profiles "
            f"(threshold {args.max_foreign_profiles}). Sample: {foreign_profiles[:15]}"
        )
    if len(name_mismatches) > args.max_name_mismatches:
        tripped.append(
            f"{len(name_mismatches):,} symbols have Finnhub names that don't match "
            f"ticker-names.json (threshold {args.max_name_mismatches}). "
            f"Sample: {name_mismatches[:15]}"
        )
    if len(missing_logos) > args.max_missing_logos:
        tripped.append(
            f"{len(missing_logos):,} bare-symbol logo-collision tickers lack Finnhub logos "
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
