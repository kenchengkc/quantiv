"""Action evidence covers discontinuous price histories under their raw symbols."""
from datetime import date
import hashlib
import json

import duckdb
import pandas as pd

from scripts import corporate_action_universe as universe
from scripts.build_data_reconciliation import _corporate_actions
from scripts.data_release import build_release
from scripts.tests.historical_admission_fixtures import publish_action_receipt, write_action_receipt
from scripts.verify_historical_training_gate import verify_historical_corporate_actions


EXPECTED = ["BNY", "MSGE", "MSGK", "MSGR", "MSGS"]


def _frozen_sources(tmp_path):
    source_date = date(2026, 10, 27)
    options = tmp_path / "parquet/options_chain/year=2026/month=10/2026-10-27.parquet"
    options.parent.mkdir(parents=True)
    pd.DataFrame({"act_symbol": ["MSGK", "MSGE", "MSGR", "BK"]}).to_parquet(options, index=False)
    prices = tmp_path / "parquet/ohlcv/history.parquet"
    prices.parent.mkdir(parents=True)
    pd.DataFrame({"act_symbol": ["MSGS"], "date": ["2026-10-23"], "close": [200.]}).to_parquet(prices, index=False)
    calendar = tmp_path / "earnings_calendar.csv"
    pd.DataFrame({"act_symbol": ["MSGS"], "date": ["2026-10-23"]}).to_csv(calendar, index=False)
    renames = tmp_path / "mutable-renames.json"
    renames.write_text(json.dumps({"renames": {
        "MSGS": {"new": "MSGK", "history_policy": "preserve_pre_transition",
                 "price_history_continuous": False, "renamed_on": "2026-10-27"},
        "BK": {"new": "BNY"},
    }}))
    delistings = tmp_path / "mutable-delistings.json"
    delistings.write_text('{"tickers": {}}')
    sources = [
        universe._binding(tmp_path, options, "options"),
        universe._binding(tmp_path, prices, "prices"),
        universe._binding(tmp_path, calendar, "calendar", snapshot=True),
        universe._binding(tmp_path, renames, "renames", origin="config/ticker_renames.json", snapshot=True),
        universe._binding(tmp_path, delistings, "delistings", origin="config/delisted_tickers.json", snapshot=True),
    ]
    frozen = {"method": universe.UNIVERSE_METHOD, "prospective_days": universe.PROSPECTIVE_DAYS,
              "sources": sources}
    return frozen, source_date, renames


def test_frozen_action_universe_keeps_parent_history_and_new_securities_distinct(tmp_path):
    frozen, source_date, _ = _frozen_sources(tmp_path)
    symbols, ends = universe.reconstruct_universe(
        tmp_path, frozen, source_date, date(2019, 1, 1), source_date, lambda relative: tmp_path / relative)
    assert symbols == EXPECTED
    assert ends == {symbol: "2026-10-27" for symbol in EXPECTED}


def test_frozen_replay_does_not_apply_later_live_rename_policy(tmp_path, monkeypatch):
    import delisted

    frozen, source_date, renames = _frozen_sources(tmp_path)
    renames.write_text('{"renames": {"MSGS": {"new": "MSGE"}, "BK": {"new": "OTHER"}}}')
    monkeypatch.setattr(delisted, "canonical_ticker", lambda value: "WRONG")
    monkeypatch.setattr(delisted, "canonical_action_ticker", lambda value: "WRONG", raising=False)
    symbols, _ = universe.reconstruct_universe(
        tmp_path, frozen, source_date, date(2019, 1, 1), source_date, lambda relative: tmp_path / relative)
    assert symbols == EXPECTED


def _legacy_receipt(tmp_path):
    receipt = write_action_receipt(tmp_path, source_date="2026-10-27")
    options = tmp_path / "parquet/options_chain/year=2026/month=10/2026-10-27.parquet"
    pd.DataFrame({"act_symbol": ["MSGS", "MSGK", "MSGE", "MSGR", "BK"]}).to_parquet(options, index=False)
    receipt["universe"].update({"symbols": len(EXPECTED),
        "symbols_sha256": hashlib.sha256("\n".join(EXPECTED).encode()).hexdigest()})
    for dataset in receipt["datasets"].values():
        dataset["batches"][0]["symbols"] = len(EXPECTED)
    publish_action_receipt(tmp_path, receipt)


def test_legacy_gate_covers_each_raw_parent_and_successor_symbol(tmp_path):
    _legacy_receipt(tmp_path)
    declared = {item["path"] for item in build_release(tmp_path)[2]["files"]}
    result = verify_historical_corporate_actions(tmp_path, declared)
    assert result["covered_symbols"] == EXPECTED


def test_current_reconciliation_requires_raw_spinoff_parent_action_coverage(tmp_path):
    _legacy_receipt(tmp_path)
    conn = duckdb.connect()
    frame = pd.DataFrame({"act_symbol": ["MSGS", "MSGK", "MSGE", "MSGR", "BK"],
                          "date": [date(2026, 10, 27)] * 5})
    conn.register("options_source", frame)
    conn.execute("CREATE TABLE v_options AS SELECT * FROM options_source")
    conn.execute("CREATE TABLE v_earnings (act_symbol VARCHAR, date DATE)")
    conn.execute("CREATE TABLE v_ohlcv (act_symbol VARCHAR, date DATE)")
    result = _corporate_actions(conn, tmp_path)
    assert result["status"] == "passed", result["errors"]
    assert result["universe_symbols"] == len(EXPECTED)
