"""Shared paths, serialization, and publication helpers."""

from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
PUBLIC_DIR = REPO_ROOT / "apps" / "frontend" / "public"
EARNINGS_CSV = DATA_DIR / "earnings_calendar.csv"
FORECASTS_DIR = DATA_DIR / "forecasts"
FORECAST_RECEIPT_PATH = FORECASTS_DIR / "receipts" / "latest_forecasts.json"
PROVIDER_ENRICHMENTS_DIR = DATA_DIR / "provider_enrichments"
MARKET_HOLIDAYS_TS = REPO_ROOT / "apps" / "frontend" / "lib" / "marketHolidays.generated.ts"
WEEK_OFFSETS = [-1, 0, 1, 2]
ET = ZoneInfo("America/New_York")


def jsonable(value):
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    return value


def write_to_public(relpath: str, content: str) -> None:
    path = PUBLIC_DIR / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
