"""Tests for delisted.py and detect_delistings rename guard."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from delisted import delisted_tickers, ticker_renames  # noqa: E402
from delisted import canonical_ticker, is_delisted, is_retired  # noqa: E402


def test_psky_is_a_rename_but_acquired_companies_remain_delisted():
    assert ticker_renames().get("PSKY") == "SKYD"
    assert canonical_ticker(" psky ") == "SKYD"
    assert not is_delisted("PSKY")
    assert not is_retired("SKYD")
    assert is_retired("PSKY")
    for old, acquirer in (("QRVO", "SWKS"), ("BLFS", "RGEN"), ("WBD", "SKYD")):
        assert is_delisted(old)
        assert old not in ticker_renames()
        assert canonical_ticker(old) == old
        assert not is_retired(acquirer)


def test_name_overlap_cannot_prove_a_rename_after_a_company_name_change():
    import detect_delistings as dd

    finder = dd.build_rename_finder([
        ("SKYD", "Skydance Corporation Class B Common Stock"),
        ("SWKS", "Skyworks Solutions, Inc. Common Stock"),
        ("RGEN", "Repligen Corporation Common Stock"),
    ])
    # PSKY needs the verified mapping: only one distinctive token survives.
    assert finder("Paramount Skydance Corporation") is None
    for name in ("Qorvo, Inc.", "BioLife Solutions, Inc.", "Warner Bros. Discovery, Inc."):
        assert finder(name) is None


def test_detector_skips_verified_psky_rename_and_promotes_acquisitions(tmp_path, monkeypatch):
    import json
    import detect_delistings as dd

    listed = {"SKYD", "SWKS", "RGEN"} | {f"ZX{i:04d}" for i in range(1000)}
    monkeypatch.setattr(dd, "fetch_directory", lambda: (listed, [
        ("SKYD", "Skydance Corporation Class B Common Stock"),
        ("SWKS", "Skyworks Solutions, Inc. Common Stock"),
        ("RGEN", "Repligen Corporation Common Stock"),
    ]))
    monkeypatch.setattr(dd, "load_active_universe", lambda: {"PSKY", "QRVO", "BLFS", "WBD"})
    monkeypatch.setattr(dd, "load_names", lambda: {
        "QRVO": "Qorvo, Inc.",
        "BLFS": "BioLife Solutions, Inc.", "WBD": "Warner Bros. Discovery, Inc.",
    })
    # Replay detection before the acquisitions were added to the ledger.
    monkeypatch.setattr(dd, "delisted_tickers", lambda: frozenset())
    monkeypatch.setattr(dd, "today_iso", lambda: "2026-10-11")
    monkeypatch.setattr(dd, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(dd, "DELISTED_PATH", tmp_path / "delisted.json")
    monkeypatch.setattr(dd, "WATCH_PATH", tmp_path / "watch.json")
    dd.DELISTED_PATH.write_text('{"tickers": {}}')
    dd.write_watch({symbol: {
        "first_missing": "2026-10-09", "last_checked": "2026-10-10", "days_missing": 2,
    } for symbol in ("PSKY", "QRVO", "BLFS", "WBD")})
    monkeypatch.setattr(sys, "argv", ["detect_delistings.py"])

    assert dd.main() == 0
    assert set(json.loads(dd.DELISTED_PATH.read_text())["tickers"]) == {"QRVO", "BLFS", "WBD"}
    assert dd.load_watch() == {}


@pytest.mark.parametrize("allow_failure, expected", [(False, 1), (True, 0)])
def test_detector_partial_directory_never_writes(tmp_path, monkeypatch, allow_failure, expected):
    import detect_delistings as dd

    monkeypatch.setattr(dd, "fetch_directory", lambda: ({"SKYD"}, []))
    monkeypatch.setattr(dd, "DELISTED_PATH", tmp_path / "delisted.json")
    monkeypatch.setattr(dd, "WATCH_PATH", tmp_path / "watch.json")
    dd.DELISTED_PATH.write_text('{"tickers": {}}')
    dd.WATCH_PATH.write_text('{"watching": {"QRVO": {"days_missing": 3}}}')
    before = (dd.DELISTED_PATH.read_bytes(), dd.WATCH_PATH.read_bytes())
    args = ["detect_delistings.py"] + (["--allow-fetch-failure"] if allow_failure else [])
    monkeypatch.setattr(sys, "argv", args)
    assert dd.main() == expected
    assert (dd.DELISTED_PATH.read_bytes(), dd.WATCH_PATH.read_bytes()) == before


def test_ticker_renames_includes_iac_leg_vsco_and_hlx():
    renames = ticker_renames()
    assert renames.get("IAC") == "PPLI"
    assert renames.get("LEG") == "SGI"
    assert renames.get("VSCO") == "VSXY"
    assert renames.get("HLX") == "HOS"


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

    sys.argv = ["detect_delistings.py", "--dry-run", "--allow-fetch-failure"]
    assert dd.main() == 0

    universe = {"IAC", "VSCO", "MASI"}
    listed = {"PPLI", "VSXY", "AAPL"}
    already = set()
    rename_old = {t.upper() for t in ticker_renames()}
    missing_now = universe - listed - already - rename_old
    assert "IAC" not in missing_now
    assert "VSCO" not in missing_now
    assert "MASI" in missing_now
