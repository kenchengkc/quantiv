#!/usr/bin/env python3
"""Per-company fiscal calendar helpers.

The earnings feed stores the report date, not the fiscal period end date.
Using the report date's calendar quarter therefore shifts most companies by
one quarter (for example, a January report is usually Q4 of the prior fiscal
year, not Q1 of the new year).

config/fiscal_year_end.json provides each known company's fiscal-year-end
month. From that month and an earnings report date, we derive the most recently
completed fiscal quarter. Unknown tickers default to a December fiscal year,
which is the common case.

Market-data vendors generally name a fiscal year by the calendar year in which
it ends. Some retailers on 4-5-4 calendars instead name the year by the year in
which it begins. config/fiscal_year_naming.json contains the small curated
set of per-ticker offsets needed to match issuer naming.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NAMING_PATH = REPO_ROOT / "config" / "fiscal_year_naming.json"
DEFAULT_FYE_PATH = REPO_ROOT / "config" / "fiscal_year_end.json"


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
        if sym.startswith("_"):
            continue
        try:
            out[str(sym).strip().upper()] = int(off)
        except (TypeError, ValueError):
            continue
    return out


def load_fiscal_year_ends(path: Path = DEFAULT_FYE_PATH) -> dict[str, int]:
    """Load ticker → fiscal-year-end month, keeping only valid 1..12 values."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    src = raw.get("fye_month", raw) if isinstance(raw, dict) else {}
    out: dict[str, int] = {}
    for sym, month in src.items() if isinstance(src, dict) else []:
        if str(sym).startswith("_"):
            continue
        try:
            value = int(month)
        except (TypeError, ValueError):
            continue
        if 1 <= value <= 12:
            out[str(sym).strip().upper()] = value
    return out


def display_fiscal_year(
    symbol: str | None,
    vendor_fiscal_year: int | None,
    naming: dict[str, int],
) -> int | None:
    """Adjust an end-year fiscal label to the company's own naming."""
    if vendor_fiscal_year is None or not symbol:
        return vendor_fiscal_year
    offset = naming.get(symbol.strip().upper(), 0)
    return vendor_fiscal_year + offset


def _month_ordinal(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def _ordinal_year_month(value: int) -> tuple[int, int]:
    year, zero_based_month = divmod(value, 12)
    return year, zero_based_month + 1


def reporting_fiscal_period(
    symbol: str | None,
    report_date: date,
    fiscal_year_ends: dict[str, int] | None = None,
    naming: dict[str, int] | None = None,
) -> tuple[int, str]:
    """Derive the fiscal period being reported on an earnings release date.

    Earnings are normally released one to three months after a quarter closes,
    so the relevant period is the latest fiscal quarter-end month strictly
    before the report month. Unknown tickers default to a December fiscal year.
    """
    ends = fiscal_year_ends or {}
    offsets = naming or {}
    ticker = (symbol or "").strip().upper()
    fye_month = ends.get(ticker, 12)

    quarter_end_months = {
        1: ((fye_month + 3 - 1) % 12) + 1,
        2: ((fye_month + 6 - 1) % 12) + 1,
        3: ((fye_month + 9 - 1) % 12) + 1,
        4: fye_month,
    }

    latest_allowed = _month_ordinal(report_date.year, report_date.month) - 1
    best: tuple[int, int] | None = None
    for quarter, end_month in quarter_end_months.items():
        for year in (report_date.year - 1, report_date.year):
            ordinal = _month_ordinal(year, end_month)
            if ordinal <= latest_allowed and (best is None or ordinal > best[0]):
                best = (ordinal, quarter)

    if best is None:
        best = (_month_ordinal(report_date.year - 1, fye_month), 4)

    quarter_end_ordinal, quarter = best
    months_to_fye = (4 - quarter) * 3
    fiscal_year_end_ordinal = quarter_end_ordinal + months_to_fye
    fiscal_year_end_year, _ = _ordinal_year_month(fiscal_year_end_ordinal)
    fiscal_year = display_fiscal_year(ticker, fiscal_year_end_year, offsets)
    assert fiscal_year is not None
    return fiscal_year, f"Q{quarter}"


def reporting_quarter_label(
    symbol: str | None,
    report_date: date,
    fiscal_year_ends: dict[str, int] | None = None,
    naming: dict[str, int] | None = None,
) -> str:
    """Render a compact label such as Q3 26 for an earnings report."""
    fiscal_year, fiscal_q = reporting_fiscal_period(
        symbol,
        report_date,
        fiscal_year_ends=fiscal_year_ends,
        naming=naming,
    )
    return f"{fiscal_q} {fiscal_year % 100:02d}"
