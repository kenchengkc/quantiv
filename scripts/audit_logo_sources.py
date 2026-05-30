#!/usr/bin/env python3
"""Classify logo resolution paths for the full ticker-names.json universe.

Mirrors apps/frontend/components/TickerLogo.tsx without HTTP calls. Reports
which provider is *tried first* and what fallbacks exist for each symbol.

Run:
  source .venv/bin/activate
  python scripts/audit_logo_sources.py
  python scripts/audit_logo_sources.py --csv reports/logo_sources.csv
  python scripts/audit_logo_sources.py --no-assume-logodev   # prod w/o key
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NAMES_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "ticker-names.json"
LOGOS_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "ticker-logos.json"
TICKER_LOGO_TS = REPO_ROOT / "apps" / "frontend" / "components" / "TickerLogo.tsx"

DEFAULT_SOURCE_ORDER = ("logodev", "finnhub", "parqet")

# Finnhub cache filenames that often mean the wrong listing for a US symbol.
FOREIGN_LOGO_HINT_RE = re.compile(
    r"(?:\.[A-Z]{1,3}\.)|(?:\.(?:TO|L|HK|KS|T|SS|SW|PA|MI|MC|AS|AX|NS|SA|MX|OL|ST|HE|CO|IR|TA|V)\b)",
    re.I,
)


def load_env() -> None:
    for path in (
        REPO_ROOT / "config" / ".env.local",
        REPO_ROOT / "config" / ".env.production",
        REPO_ROOT / ".env.local",
    ):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def separator_variants(ticker: str) -> list[str]:
    dot = ticker.replace("-", ".")
    dash = ticker.replace(".", "-")
    if dot == dash:
        return [ticker]
    return [dash, dot]


def parse_ts_record(name: str, source: str) -> dict[str, str]:
    """Extract a string→string Record from TickerLogo.tsx."""
    pattern = rf"const {name}: Record<string, LogoSource> = \{{([^}}]*)\}};"
    if name == "LOGO_OVERRIDES":
        pattern = rf"const {name}: Record<string, string> = \{{([^}}]*)\}};"
    elif name == "LOGO_DOMAIN_OVERRIDES":
        pattern = rf"const {name}: Record<string, string> = \{{([^}}]*)\}};"
    match = re.search(pattern, source, re.S)
    if not match:
        return {}
    body = match.group(1)
    out: dict[str, str] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        m = re.match(r"['\"]?([A-Z0-9.\-]+)['\"]?\s*:\s*['\"]([^'\"]+)['\"]", line)
        if m:
            out[m.group(1).upper()] = m.group(2)
    return out


def parse_source_overrides(source: str) -> dict[str, str]:
    match = re.search(
        r"const LOGO_SOURCE_OVERRIDES: Record<string, LogoSource> = \{([^}]*)\};",
        source,
        re.S,
    )
    if not match:
        return {}
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        m = re.match(
            r"['\"]?(?P<sym>[A-Z0-9.\-]+)['\"]?\s*:\s*['\"](?P<src>logodev|finnhub|parqet)['\"]",
            line,
        )
        if m:
            out[m.group("sym").upper()] = m.group("src")
    return out


def finnhub_cached(ticker: str, logos: dict[str, str]) -> str | None:
    for variant in separator_variants(ticker):
        url = logos.get(variant)
        if isinstance(url, str) and url.startswith("http"):
            return url
    return None


def resolution_order(
    ticker: str,
    *,
    source_overrides: dict[str, str],
    url_overrides: dict[str, str],
    domain_overrides: dict[str, str],
    assume_logodev: bool,
) -> list[str]:
    """Provider sequence actually attempted (deduped)."""
    preferred = source_overrides.get(ticker)
    order = (
        [preferred, *[s for s in DEFAULT_SOURCE_ORDER if s != preferred]]
        if preferred
        else list(DEFAULT_SOURCE_ORDER)
    )
    steps: list[str] = []
    if ticker in url_overrides:
        steps.append("pinned_url")
    if ticker in domain_overrides:
        steps.append("logodev_domain" if assume_logodev else "logodev_domain_skipped")
    for src in order:
        if src == "logodev" and not assume_logodev:
            continue
        steps.append(src)
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for step in steps:
        if step not in seen:
            seen.add(step)
            out.append(step)
    return out


def classify_ticker(
    ticker: str,
    logos: dict[str, str],
    *,
    source_overrides: dict[str, str],
    url_overrides: dict[str, str],
    domain_overrides: dict[str, str],
    assume_logodev: bool,
) -> dict[str, object]:
    steps = resolution_order(
        ticker,
        source_overrides=source_overrides,
        url_overrides=url_overrides,
        domain_overrides=domain_overrides,
        assume_logodev=assume_logodev,
    )
    first = steps[0] if steps else "letter_tile"
    fh_url = finnhub_cached(ticker, logos)
    preferred = source_overrides.get(ticker)

    bucket = "unknown"
    if first == "pinned_url":
        bucket = "pinned_url"
    elif first == "logodev_domain":
        bucket = "logodev_domain"
    elif preferred == "finnhub":
        bucket = "override_finnhub_first" if fh_url else "override_finnhub_first_no_cache"
    elif preferred == "parqet":
        bucket = "override_parqet_first"
    elif not assume_logodev:
        bucket = "finnhub_then_parqet" if fh_url else "parqet_only"
    elif fh_url:
        bucket = "logodev_then_finnhub_then_parqet"
    else:
        bucket = "logodev_then_parqet"

    return {
        "ticker": ticker,
        "bucket": bucket,
        "first_step": first,
        "steps": " → ".join(steps) if steps else "letter_tile",
        "finnhub_cached": bool(fh_url),
        "finnhub_url": fh_url or "",
        "finnhub_foreign_hint": bool(fh_url and FOREIGN_LOGO_HINT_RE.search(fh_url)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        help="Write per-ticker rows to this CSV path.",
    )
    parser.add_argument(
        "--assume-logodev",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Assume NEXT_PUBLIC_LOGO_DEV_PUBLISHABLE_KEY is set (default: auto).",
    )
    parser.add_argument(
        "--list-foreign-hints",
        action="store_true",
        help="Print Finnhub cache URLs that look like foreign listings.",
    )
    parser.add_argument(
        "--short-only",
        action="store_true",
        help="Only print tickers with length <= 2 (collision-prone).",
    )
    args = parser.parse_args()

    if not NAMES_PATH.exists():
        print(f"✗ Missing {NAMES_PATH}", file=sys.stderr)
        return 1

    load_env()
    assume_logodev = args.assume_logodev
    if assume_logodev is None:
        assume_logodev = bool(
            os.environ.get("NEXT_PUBLIC_LOGO_DEV_PUBLISHABLE_KEY")
            or os.environ.get("LOGO_DEV_API_KEY")
        )

    names: dict[str, str] = json.loads(NAMES_PATH.read_text())
    logos: dict[str, str] = {}
    if LOGOS_PATH.exists():
        payload = json.loads(LOGOS_PATH.read_text())
        logos = {
            k: v
            for k, v in dict(payload.get("logos", {})).items()
            if isinstance(k, str) and isinstance(v, str)
        }

    ts_source = TICKER_LOGO_TS.read_text() if TICKER_LOGO_TS.exists() else ""
    source_overrides = parse_source_overrides(ts_source)
    url_overrides = parse_ts_record("LOGO_OVERRIDES", ts_source)
    domain_overrides = parse_ts_record("LOGO_DOMAIN_OVERRIDES", ts_source)

    rows: list[dict[str, object]] = []
    for ticker in sorted(names):
        if args.short_only and len(ticker) > 2:
            continue
        rows.append(
            classify_ticker(
                ticker,
                logos,
                source_overrides=source_overrides,
                url_overrides=url_overrides,
                domain_overrides=domain_overrides,
                assume_logodev=assume_logodev,
            )
        )

    counts = Counter(r["bucket"] for r in rows)
    cached = sum(1 for r in rows if r["finnhub_cached"])
    foreign_hints = [r for r in rows if r["finnhub_foreign_hint"]]

    print("LOGO SOURCE AUDIT (ticker-names.json universe)")
    print(f"  symbols:              {len(rows):,}")
    print(f"  assume Logo.dev key:  {assume_logodev}")
    print(f"  Finnhub cache hits:   {cached:,} ({100 * cached / len(rows):.1f}%)")
    print(f"  source overrides:     {len(source_overrides):,} (from TickerLogo.tsx)")
    print(f"  Finnhub foreign hint: {len(foreign_hints):,} cached URLs")
    print()
    print("  Resolution bucket (first meaningful path):")
    for bucket, n in counts.most_common():
        pct = 100 * n / len(rows)
        print(f"    {bucket:40} {n:6,}  ({pct:5.1f}%)")

    if args.list_foreign_hints and foreign_hints:
        print("\n  Finnhub cache — possible foreign listing in filename:")
        for r in foreign_hints[:40]:
            print(f"    {r['ticker']:8} {r['finnhub_url']}")
        if len(foreign_hints) > 40:
            print(f"    … and {len(foreign_hints) - 40} more")

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "ticker",
            "bucket",
            "first_step",
            "steps",
            "finnhub_cached",
            "finnhub_foreign_hint",
            "finnhub_url",
        ]
        with args.csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"\n  Wrote {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
