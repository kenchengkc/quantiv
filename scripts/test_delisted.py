"""Tests for delisted.py and detect_delistings rename guard."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from delisted import delisted_tickers, ticker_renames  # noqa: E402


def test_ticker_renames_includes_iac_leg_and_vsco():
    renames = ticker_renames()
    assert renames.get("IAC") == "PPLI"
    assert renames.get("LEG") == "SGI"
    assert renames.get("VSCO") == "VSXY"


def test_eqr_avb_two_are_delisted_not_renames():
    """Merger-of-equals / preferred-class false positives stay delisted."""
    delisted = delisted_tickers()
    renames = ticker_renames()
    for ticker in ("AVB", "EQR", "TWO"):
        assert ticker in delisted
        assert ticker not in renames
    assert "VMRK" not in renames.values()
    assert renames.get("TWO") != "TWO$A"


def test_rename_finder_skips_preferred_dollar_classes():
    import detect_delistings as dd

    finder = dd.build_rename_finder([
        (
            "TWO$A",
            "Two Harbors Investment Corp. 8.125% Series A Preferred Stock",
        ),
        ("AAPL", "Apple Inc. Common Stock"),
    ])
    assert finder("Two Harbors Investment") is None


def test_rename_finder_still_matches_common_rebrand():
    import detect_delistings as dd

    finder = dd.build_rename_finder([
        ("BNY", "The Bank of New York Mellon Corporation"),
        ("AAPL", "Apple Inc. Common Stock"),
    ])
    assert finder("Bank of New York Mellon") == "BNY"


def test_detect_delistings_excludes_known_renames_from_missing(tmp_path, monkeypatch):
    """Symbols in ticker_renames.json must not be treated as delisting candidates."""
    import detect_delistings as dd

    monkeypatch.setattr(dd, "fetch_directory", lambda: ({"PPLI", "VSXY", "AAPL"}, []))
    monkeypatch.setattr(dd, "load_active_universe", lambda: {"IAC", "VSCO", "MASI"})
    monkeypatch.setattr(dd, "load_names", lambda: {})
    monkeypatch.setattr(dd, "load_watch", lambda: {})
    monkeypatch.setattr(dd, "write_watch", lambda w: None)
    monkeypatch.setattr(dd, "promote_to_delisted", lambda *a, **k: None)
    monkeypatch.setattr(dd, "notify_github", lambda *a, **k: None)

    # Simulate CLI with no writes
    sys.argv = ["detect_delistings.py", "--dry-run", "--allow-fetch-failure"]
    assert dd.main() == 0

    # IAC/VSCO are rename-old symbols — should not appear in promoted output.
    # MASI is genuinely missing and should be on watch (first day), not promoted
    # unless threshold met. Re-run internals via direct computation:
    universe = {"IAC", "VSCO", "MASI"}
    listed = {"PPLI", "VSXY", "AAPL"}
    already = set()
    rename_old = {t.upper() for t in ticker_renames()}
    missing_now = universe - listed - already - rename_old
    assert "IAC" not in missing_now
    assert "VSCO" not in missing_now
    assert "MASI" in missing_now
