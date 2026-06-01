#!/usr/bin/env python3
"""Per-company fiscal-year naming correction.

Market-data vendors (Finnhub, Polygon, FMP) label a fiscal year by the calendar
year in which it **ends**. Many retailers on the 4-5-4 calendar instead name a
fiscal year by the year in which it **begins** (e.g. Dollar General calls the
52/53-week year ending ~Jan 2027 "fiscal 2026"). The two conventions differ by
one year, which is why DG showed "Q1 FY2027" when the company itself reports
"Q1 FY2026".

There is no arithmetic rule that derives the convention from the fiscal-year-end
month: NVDA and Walmart both end in January yet name by the END year, while
DG / LULU / FIVE / PVH / Target end in late Jan / early Feb yet name by the
BEGIN year. So the convention must be curated per ticker.

`config/fiscal_year_naming.json` maps a ticker to an integer **offset** applied
to the vendor's end-year label:

    -1  → company names the FY by the year it begins (subtract 1 from vendor year)
     0  → company matches the vendor end-year convention (default; not listed)

Only tickers present in the map are adjusted; everyone else is returned
unchanged, so the correction can never mislabel an unverified company.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NAMING_PATH = REPO_ROOT / "config" / "fiscal_year_naming.json"


def load_fiscal_year_naming(path: Path = DEFAULT_NAMING_PATH) -> dict[str, int]:
    """Load the ticker → fiscal-year-naming offset map. Missing/empty → {}."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    src = raw.get("offsets", raw) if isinstance(raw, dict) else {}
    out: dict[str, int] = {}
    for sym, off in src.items() if isinstance(src, dict) else []:
        if sym.startswith("_"):  # allow _comment keys
            continue
        try:
            out[str(sym).strip().upper()] = int(off)
        except (TypeError, ValueError):
            continue
    return out


def display_fiscal_year(
    symbol: str | None,
    vendor_fiscal_year: int | None,
    naming: dict[str, int],
) -> int | None:
    """Adjust a vendor end-year fiscal year to the company's own naming.

    Returns ``vendor_fiscal_year`` unchanged when the ticker isn't curated or
    the year is missing — so unmapped companies are never altered.
    """
    if vendor_fiscal_year is None or not symbol:
        return vendor_fiscal_year
    offset = naming.get(symbol.strip().upper(), 0)
    return vendor_fiscal_year + offset
