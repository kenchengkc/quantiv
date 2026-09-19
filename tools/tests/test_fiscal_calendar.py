from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fiscal_calendar import (
    display_fiscal_year,
    load_fiscal_year_ends,
    load_fiscal_year_naming,
    reporting_fiscal_period,
    reporting_quarter_label,
)


def test_begin_year_retailer_offset(tmp_path):
    cfg = tmp_path / "naming.json"
    cfg.write_text(json.dumps({"offsets": {"DG": -1, "_comment": "x"}}))
    naming = load_fiscal_year_naming(cfg)
    assert naming == {"DG": -1}
    # DG names the FY ending ~Jan 2027 "fiscal 2026" (begin-year).
    assert display_fiscal_year("DG", 2027, naming) == 2026
    assert display_fiscal_year("dg", 2027, naming) == 2026  # case-insensitive


def test_unmapped_company_unchanged():
    naming = {"DG": -1}
    # NVDA / WMT also end in January but name by END year — must be untouched.
    assert display_fiscal_year("NVDA", 2026, naming) == 2026
    assert display_fiscal_year("UVV", 2026, naming) == 2026
    assert display_fiscal_year("AAPL", 2025, naming) == 2025


def test_none_and_missing_config_safe():
    assert display_fiscal_year("DG", None, {"DG": -1}) is None
    assert display_fiscal_year(None, 2027, {"DG": -1}) == 2027
    assert load_fiscal_year_naming(Path("/no/such/file.json")) == {}


def test_shipped_config_only_contains_verified_begin_year_retailers():
    naming = load_fiscal_year_naming()  # config/fiscal_year_naming.json
    # Every shipped entry is a -1 (begin-year) retailer; no accidental +/-.
    assert set(naming) == {"DG", "LULU", "FIVE", "PVH"}
    assert all(v == -1 for v in naming.values())


def test_load_fiscal_year_ends(tmp_path):
    cfg = tmp_path / "fye.json"
    cfg.write_text(json.dumps({"fye_month": {"AAPL": 9, "BAD": 13, "_comment": "x"}}))
    assert load_fiscal_year_ends(cfg) == {"AAPL": 9}


def test_reporting_period_uses_completed_fiscal_quarter():
    ends = {"AAPL": 9, "MSFT": 6, "NVDA": 1, "WMT": 1}
    naming = {}

    assert reporting_fiscal_period("AAPL", date(2026, 1, 29), ends, naming) == (2026, "Q1")
    assert reporting_fiscal_period("AAPL", date(2026, 7, 30), ends, naming) == (2026, "Q3")
    assert reporting_fiscal_period("MSFT", date(2026, 4, 29), ends, naming) == (2026, "Q3")
    assert reporting_fiscal_period("MSFT", date(2026, 10, 27), ends, naming) == (2027, "Q1")
    assert reporting_fiscal_period("NVDA", date(2026, 2, 25), ends, naming) == (2026, "Q4")
    assert reporting_fiscal_period("WMT", date(2026, 5, 21), ends, naming) == (2027, "Q1")


def test_reporting_period_defaults_unknown_ticker_to_calendar_year():
    assert reporting_fiscal_period("AMZN", date(2026, 2, 5), {}, {}) == (2025, "Q4")
    assert reporting_quarter_label("AMZN", date(2026, 7, 30), {}, {}) == "Q2 26"


def test_reporting_period_applies_company_fiscal_year_naming():
    ends = {"DG": 1}
    naming = {"DG": -1}
    assert reporting_fiscal_period("DG", date(2026, 3, 12), ends, naming) == (2025, "Q4")
    assert reporting_fiscal_period("DG", date(2026, 6, 2), ends, naming) == (2026, "Q1")
