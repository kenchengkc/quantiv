"""New price/options-backed events must pass timing reconciliation before scoring."""

from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys

import pandas as pd
import pytest
import yaml

import reconcile_earnings_calendar as calendar
from sync_finnhub_earnings import load_data_backed_symbol_universe


TODAY = date(2026, 10, 25)


def source_file(data, kind, rows):
    path = data / "parquet" / kind / "year=2026" / "month=10" / "2026-10-23.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def setup_calendar(tmp_path, monkeypatch):
    symbols = ["OLD", "NEW", "OPT", "RAW", "BMW.DE", "AMWD", "BAD", "FUT", "STALE"]
    pd.DataFrame({"act_symbol": symbols, "date": ["2026-10-28"] * len(symbols),
                  "timing": ["amc"] * len(symbols)}).to_csv(
        tmp_path / "earnings_calendar.csv", index=False,
    )
    source_file(tmp_path, "ohlcv", [
        {"act_symbol": symbol, "date": day, "close": close}
        for symbol, day, close in [
            ("NEW", "2026-10-23", 10), ("BMW.DE", "2026-10-23", 10),
            ("AMWD", "2026-10-23", 10), ("BAD", "2026-10-23", 0),
            ("FUT", "2026-10-26", 10), ("STALE", "2026-09-01", 10),
        ]
    ])
    source_file(tmp_path, "options_chain", [
        {"act_symbol": "OPT", "date": "2026-10-23"},
    ])
    monkeypatch.setattr(calendar, "load_frontend_symbol_universe", lambda: ["OLD"])
    monkeypatch.setattr(calendar, "et_today", lambda: TODAY)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(calendar, "datetime", Clock)
    monkeypatch.setattr(calendar, "_env_key", lambda name: "")
    monkeypatch.setattr(calendar, "load_company_names", dict)
    for name in ("fetch_finnhub_calendar", "fetch_alphavantage_calendar", "fetch_fmp_calendar"):
        monkeypatch.setattr(calendar, name, lambda *args: ([], {"status": "missing_key"}))
    return ["reconcile", "--data-dir", str(tmp_path), "--baseline", str(tmp_path / "absent.csv"),
            "--report", str(tmp_path / "report.json")]


@pytest.mark.parametrize("confirmed", [False, True])
def test_new_price_backed_ticker_is_searched_and_reconciled_before_publication(
    tmp_path, monkeypatch, confirmed,
):
    argv = setup_calendar(tmp_path, monkeypatch)
    searched = []

    def collect(symbols, **kwargs):
        searched.extend(symbols)
        evidence = {"NEW": [{"provider": "financialcontent_press_release", "date": "2026-10-28",
                             "timing": "bmo", "published_on": "2026-10-20", "direct": True}]} if confirmed else {}
        return evidence, {"public_press_releases": {}, "twelvedata_press_releases": {}}

    monkeypatch.setattr(calendar, "_collect_announcements", collect)
    monkeypatch.setattr(sys, "argv", argv)
    assert calendar.main() == 0
    assert set(searched) == {"OLD", "NEW", "OPT"}
    decisions = json.loads((tmp_path / "report.json").read_text())["decisions"]
    assert set(decisions) == {"OLD", "NEW", "OPT"}
    assert decisions["NEW"]["timing"] == ("bmo" if confirmed else "unknown")
    rows = pd.read_csv(tmp_path / "earnings_calendar.csv").set_index("act_symbol")
    # The raw vendor AMC may never survive simply because no page existed yet.
    assert rows.loc["NEW", "timing"] == ("bmo" if confirmed else "unknown")


@pytest.mark.parametrize("extra, expected", [
    (["--announcement-max-symbols", "1"], ["OLD"]),
    (["--symbols", "OPT"], ["OPT"]),
    (["--symbols", "NEW", "OPT"], ["NEW", "OPT"]),
    (["--symbols", "OPT", "--announcement-max-symbols", "1"], ["OPT"]),
])
def test_newcomer_scope_keeps_budgets_and_explicit_lookup_targeting(
    tmp_path, monkeypatch, extra, expected,
):
    argv = setup_calendar(tmp_path, monkeypatch)
    monkeypatch.setattr(calendar, "MAX_NEW_ANNOUNCEMENT_SYMBOLS", 1)
    searched = []

    def collect(symbols, **kwargs):
        searched.extend(symbols)
        return {}, {"public_press_releases": {}, "twelvedata_press_releases": {}}

    monkeypatch.setattr(calendar, "_collect_announcements", collect)
    monkeypatch.setattr(sys, "argv", argv + extra)
    assert calendar.main() == 0
    assert searched == expected
    decisions = json.loads((tmp_path / "report.json").read_text())["decisions"]
    # Budget/targeting limits external calls, not the timing safety check.
    assert decisions["NEW"]["timing"] == decisions["OPT"]["timing"] == "unknown"


def test_normal_refresh_ingests_prices_once_before_selecting_reconciliation_scope():
    root = Path(__file__).resolve().parents[2]
    jobs = yaml.safe_load((root / ".github/workflows/data-refresh.yml").read_text())["jobs"]
    commands = [step.get("run", "") for step in jobs["refresh"]["steps"]]
    prices = [index for index, command in enumerate(commands) if "sync_dolthub.py --ohlcv" in command]
    reconciliation = next(index for index, command in enumerate(commands)
                          if "reconcile_earnings_calendar.py" in command)
    assert len(prices) == 1
    assert prices[0] < reconciliation


def test_lookup_sources_use_completed_sessions_including_one_session_grace(tmp_path):
    source_file(tmp_path, "ohlcv", [
        {"act_symbol": symbol, "date": day, "close": 10}
        for symbol, day in [("CURRENT", "2026-10-23"), ("GRACE", "2026-10-22"),
                            ("OLD", "2026-10-21"), ("UNFINISHED", "2026-10-26")]
    ])
    safety, fresh = load_data_backed_symbol_universe(
        tmp_path, today=date(2026, 10, 26), now=datetime(2026, 10, 26, 14, tzinfo=timezone.utc),
    )
    assert safety == {"CURRENT", "GRACE", "OLD", "UNFINISHED"}
    assert fresh == {"CURRENT", "GRACE"}


def test_newcomer_lookup_cap_rotates_without_limiting_safety_scope(tmp_path, monkeypatch):
    argv = setup_calendar(tmp_path, monkeypatch)
    # Exercise the production cap without manufacturing 101 ticker fixtures.
    monkeypatch.setattr(calendar, "MAX_NEW_ANNOUNCEMENT_SYMBOLS", 1)
    calendar.announcement_state.write_state(tmp_path / "calendar_announcement_state.json", {
        "public_press_releases|NEW|2026-10-28": {
            "observed_at": "2026-10-24T12:00:00+00:00", "status": "ok",
        },
    }, today=TODAY)
    searched = []

    def collect(symbols, **kwargs):
        searched.extend(symbols)
        return {}, {"public_press_releases": {}, "twelvedata_press_releases": {}}

    monkeypatch.setattr(calendar, "_collect_announcements", collect)
    monkeypatch.setattr(sys, "argv", argv)
    assert calendar.main() == 0
    assert searched == ["OLD", "OPT"]
    decisions = json.loads((tmp_path / "report.json").read_text())["decisions"]
    assert decisions["NEW"]["timing"] == "unknown"
