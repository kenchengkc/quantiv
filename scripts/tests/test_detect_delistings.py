"""Successor discovery must distinguish issuer identity from related names."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import detect_delistings as detector  # noqa: E402


@pytest.mark.parametrize("entries", [
    [("MSGE", "Madison Square Garden Entertainment Corp. Class A Common Stock")],
    [("MSGK", "MSG Knickerbockers Corp. Class A Common Stock"),
     ("MSGR", "MSG Rangers Corp. Class A Common Stock"),
     ("MSGE", "Madison Square Garden Entertainment Corp. Class A Common Stock")],
    [("NORTH", "North Star Energy Corporation Common Stock")],
])
def test_related_names_do_not_identify_successors(entries):
    company = "North Star Holdings" if entries[0][0] == "NORTH" else "Madison Square Garden Sports Corp."
    assert detector.build_rename_finder(entries)(company) is None


@pytest.mark.parametrize("entries", [
    [("NSTA", "North Star Corporation Class A Common Stock"),
     ("NSTB", "North Star Corporation Class B Common Stock")],
    [("NSTB", "North Star Corporation Class B Common Stock"),
     ("NSTA", "North Star Corporation Class A Common Stock")],
])
def test_multiple_common_share_classes_require_identity_review(entries):
    assert detector.build_rename_finder(entries)("North Star Corporation") is None


@pytest.mark.parametrize("security", [
    "North Star Corporation - Warrants",
    "North Star Corporation - Units",
    "North Star Corporation 7% Series A Preferred Stock",
    "North Star Corporation - Depositary Shares",
    "North Star Corporation - Rights",
])
def test_non_common_securities_cannot_be_successor_candidates(security):
    assert detector.build_rename_finder([("NSTAR", security)])("North Star Corporation") is None


def test_unique_common_name_candidate_ignores_only_listing_boilerplate():
    finder = detector.build_rename_finder([
        ("BNY", "The Bank of New York Mellon Corporation - Common Stock"),
        ("BNY", "The Bank of New York Mellon Corporation Common Stock"),
        ("BNYPA", "The Bank of New York Mellon Corporation Preferred Stock"),
    ])
    assert finder("Bank of New York Mellon") == "BNY"


def test_related_name_hold_does_not_auto_remove_possible_spinoff_parent(tmp_path, monkeypatch):
    entries = [("MSGE", "Madison Square Garden Entertainment Corp. Class A Common Stock")]
    listed = {"MSGE"} | {f"TEST{i}" for i in range(1000)}
    monkeypatch.setattr(detector, "fetch_directory", lambda: (listed, entries))
    monkeypatch.setattr(detector, "load_active_universe", lambda: {"MSGS"})
    monkeypatch.setattr(detector, "load_names", lambda: {"MSGS": "Madison Square Garden Sports Corp."})
    monkeypatch.setattr(detector, "ticker_renames", lambda: {})
    monkeypatch.setattr(detector, "delisted_tickers", lambda: frozenset())
    monkeypatch.setattr(detector, "today_iso", lambda: "2026-10-28")
    monkeypatch.setattr(detector, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(detector, "DELISTED_PATH", tmp_path / "delisted.json")
    monkeypatch.setattr(detector, "WATCH_PATH", tmp_path / "watch.json")
    detector.DELISTED_PATH.write_text('{"tickers": {}}')
    detector.write_watch({"MSGS": {
        "first_missing": "2026-10-25", "last_checked": "2026-10-27", "days_missing": 3,
    }})
    notifications = []
    monkeypatch.setattr(detector, "notify_github", lambda *args, **kwargs: notifications.append((args, kwargs)))
    monkeypatch.setattr(sys, "argv", ["detect_delistings.py", "--notify-github"])

    assert detector.main() == 0
    assert json.loads(detector.DELISTED_PATH.read_text())["tickers"] == {}
    assert detector.load_watch() == {}
    assert notifications == [(({}, []), {"dry_run": False, "unresolved": ["MSGS"]})]


def test_candidate_notice_requires_primary_identity_evidence(monkeypatch):
    posted = []
    session = SimpleNamespace(
        headers={},
        get=lambda *args, **kwargs: SimpleNamespace(ok=True, json=lambda: []),
        post=lambda *args, **kwargs: (posted.append(kwargs["json"]) or
                                    SimpleNamespace(ok=True, json=lambda: {"number": 1})),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repository")
    monkeypatch.setattr(detector.requests, "Session", lambda: session)
    detector.notify_github({"BK": "BNY"}, [], dry_run=False)

    assert len(posted) == 1
    body = posted[0]["body"]
    assert "unconfirmed" in body.lower()
    assert "CIK" in body and "CUSIP" in body
    assert "spin-off" in body.lower()
    assert '"new": "BNY"' not in body
