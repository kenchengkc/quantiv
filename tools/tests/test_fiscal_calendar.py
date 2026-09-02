from __future__ import annotations

import json
from pathlib import Path

from fiscal_calendar import display_fiscal_year, load_fiscal_year_naming


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
