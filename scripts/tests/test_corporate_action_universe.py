"""Action coverage must derive from immutable, bounded source observations."""

from datetime import date
import hashlib

import duckdb
import pandas as pd
import pytest

from scripts import sync_dolthub
from scripts.build_data_reconciliation import _corporate_actions
from scripts.data_release import build_release
from scripts.setup_duckdb_from_parquet import setup_corporate_action_coverage
from scripts.tests.historical_admission_fixtures import publish_action_receipt
from scripts.verify_historical_training_gate import verify_historical_corporate_actions


def _sources(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sync_dolthub, "API_DELAY", 0)
    options = tmp_path / "parquet/options_chain/year=2026/month=10/2026-10-09.parquet"
    options.parent.mkdir(parents=True)
    pd.DataFrame({"act_symbol": ["AAPL", "BK", "AMWD"]}).to_parquet(options, index=False)
    prices = tmp_path / "parquet/ohlcv/history.parquet"
    prices.parent.mkdir(parents=True)
    pd.DataFrame({"act_symbol": ["LEVI", "LEDGER", "AMWD", "OLDONLY", "NA"],
                  "date": ["2026-10-08", "2026-10-08", "2025-04-01", "2026-06-01", "2026-10-08"],
                  "close": [10., 20., 30., 40., 50.]}).to_parquet(prices, index=False)
    pd.DataFrame({"act_symbol": ["LEVI", "AMWD", "AMWD", "OLDONLY", "APLD", "UNPRICED", "TOOFAR", "NA"],
                  "date": ["2026-10-08", "2025-04-02", "2026-10-16", "2026-10-08", "2026-10-20", "2025-04-02", "2026-12-01", "2026-10-08"]}).to_csv(
        tmp_path / "earnings_calendar.csv", index=False)
    ledger = tmp_path / "forecasts/event_prediction_ledger.parquet"
    ledger.parent.mkdir()
    pd.DataFrame({"act_symbol": ["LEDGER", "FARLEDGER"],
                  "earnings_date": ["2026-10-08", "2026-12-01"]}).to_parquet(ledger, index=False)
    monkeypatch.setattr(sync_dolthub, "query", lambda *_args, **_kwargs: [])


def _receipt(tmp_path, monkeypatch):
    _sources(tmp_path, monkeypatch)
    return sync_dolthub.sync_corporate_actions("2019-01-01", "2026-10-11")


def _declared(tmp_path):
    return {item["path"] for item in build_release(tmp_path)[2]["files"]}


def test_sync_includes_price_backed_events_ledger_and_nearby_optionless_calendar(tmp_path, monkeypatch):
    receipt = _receipt(tmp_path, monkeypatch)
    evidence = verify_historical_corporate_actions(tmp_path, _declared(tmp_path))
    assert evidence["covered_symbols"] == ["AAPL", "AMWD", "APLD", "BNY", "LEDGER", "LEVI", "NA", "OLDONLY"]
    assert receipt["schema"] == "quantiv.corporate-action-ingestion.v2"
    assert evidence["symbol_window_ends"]["AMWD"] == "2026-05-28"
    assert all(dataset["rows"] == 0 for dataset in evidence["datasets"].values())


def test_retired_symbol_without_pre_retirement_event_and_price_is_excluded(tmp_path, monkeypatch):
    _sources(tmp_path, monkeypatch)
    pd.DataFrame({"act_symbol": ["AMWD"], "date": ["2026-10-08"]}).to_csv(tmp_path / "earnings_calendar.csv", index=False)
    receipt = sync_dolthub.sync_corporate_actions("2019-01-01", "2026-10-11")
    evidence = verify_historical_corporate_actions(tmp_path, _declared(tmp_path))
    assert "AMWD" not in evidence["covered_symbols"]
    assert receipt["universe"]["symbols"] == 3  # AAPL, BNY, ledger-backed LEDGER.


def test_mutable_calendar_and_ledger_cannot_rewrite_receipted_coverage(tmp_path, monkeypatch):
    _receipt(tmp_path, monkeypatch)
    declared = _declared(tmp_path)
    (tmp_path / "earnings_calendar.csv").write_text("act_symbol,date\nINJECTED,2026-10-15\n")
    (tmp_path / "forecasts/event_prediction_ledger.parquet").unlink()
    evidence = verify_historical_corporate_actions(tmp_path, declared)
    assert "LEVI" in evidence["covered_symbols"] and "LEDGER" in evidence["covered_symbols"]
    assert "INJECTED" not in evidence["covered_symbols"]


@pytest.mark.parametrize("mutation", ["symbol_injection", "source_digest", "source_unmanifested", "source_origin", "batch_digest", "batch_overlap", "page_count"])
def test_union_receipt_rejects_unsupported_coverage_or_provenance(tmp_path, monkeypatch, mutation):
    receipt = _receipt(tmp_path, monkeypatch)
    declared = _declared(tmp_path)
    if mutation == "symbol_injection":
        path = tmp_path / receipt["universe"]["snapshot"]["partition"]
        pd.DataFrame({"act_symbol": ["INJECTED"]}).to_parquet(path, index=False)
        receipt["universe"]["snapshot"]["partition_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt["universe"]["symbols"] = 1
        receipt["universe"]["symbols_sha256"] = hashlib.sha256(b"INJECTED").hexdigest()
    elif mutation == "source_digest":
        receipt["universe"]["sources"][0]["partition_sha256"] = "0" * 64
    elif mutation == "source_unmanifested":
        declared.remove(receipt["universe"]["sources"][0]["partition"])
    elif mutation == "source_origin":
        receipt["universe"]["sources"][0]["origin"] = "arbitrary-symbols.csv"
    elif mutation == "batch_digest":
        receipt["datasets"]["splits"]["batches"][0]["symbols_sha256"] = "0" * 64
    elif mutation == "batch_overlap":
        receipt["datasets"]["splits"]["batches"].append(receipt["datasets"]["splits"]["batches"][0])
    else:
        receipt["datasets"]["splits"]["batches"][0]["pages"] = 2
    publish_action_receipt(tmp_path, receipt)
    with pytest.raises(RuntimeError, match="corporate-action"):
        verify_historical_corporate_actions(tmp_path, declared)


def test_coverage_view_includes_verified_empty_action_optionless_and_retired_history(tmp_path, monkeypatch):
    receipt = _receipt(tmp_path, monkeypatch)
    conn = duckdb.connect()
    setup_corporate_action_coverage(conn, tmp_path)
    rows = conn.execute("SELECT act_symbol,window_end,receipt_id FROM v_corporate_action_coverage WHERE act_symbol IN ('LEVI','AMWD') ORDER BY act_symbol").fetchall()
    assert rows == [("AMWD", date(2026, 5, 28), receipt["receipt_id"]),
                    ("LEVI", date(2026, 10, 11), receipt["receipt_id"])]


def test_current_reconciliation_accepts_verified_event_union_and_keeps_latest_options_gate(tmp_path, monkeypatch):
    _receipt(tmp_path, monkeypatch)
    conn = duckdb.connect()
    conn.execute("CREATE TABLE v_options AS SELECT 'AAPL' act_symbol, DATE '2026-10-09' date UNION ALL SELECT 'BK', DATE '2026-10-09' UNION ALL SELECT 'AMWD', DATE '2026-10-09'")
    conn.execute("CREATE TABLE v_earnings AS SELECT 'LEVI' act_symbol, DATE '2026-10-08' date")
    conn.execute("CREATE TABLE v_ohlcv AS SELECT 'LEVI' act_symbol, DATE '2026-10-07' date UNION ALL SELECT 'LEVI', DATE '2026-10-09'")
    result = _corporate_actions(conn, tmp_path)
    assert result["status"] == "passed", result["errors"]
    assert result["universe_symbols"] == 8
    conn.execute("INSERT INTO v_options VALUES ('AAPL', DATE '2026-10-12')")
    result = _corporate_actions(conn, tmp_path)
    assert result["status"] == "failed"
    assert "corporate-action universe does not match the latest options partition" in result["errors"]


def test_action_query_exhausts_exact_full_page_and_binds_each_symbol_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_dolthub, "ROW_LIMIT", 2)
    monkeypatch.setattr(sync_dolthub, "CORPORATE_ACTION_BATCH_SIZE", 1)
    monkeypatch.setattr(sync_dolthub, "API_DELAY", 0)
    queries = []

    def query(sql, *_args):
        queries.append(sql)
        if "'LEVI'" in sql and "AND (act_symbol, ex_date) >" not in sql:
            return [{"act_symbol": "LEVI", "ex_date": "2025-01-01", "amount": 1},
                    {"act_symbol": "LEVI", "ex_date": "2025-02-01", "amount": 2}]
        return []

    monkeypatch.setattr(sync_dolthub, "query", query)
    frame, batches = sync_dolthub._fetch_action_table("dividend", ["amount"], ["LEVI", "RELL"], date(2019, 1, 1), date(2026, 10, 11))
    assert len(frame) == 2 and len(queries) == 3
    assert batches[0]["pages"] == 2 and batches[1]["pages"] == 1
    assert batches[0]["symbols_sha256"] == hashlib.sha256(b"LEVI").hexdigest()
    assert batches[0]["page_rows"] == [2, 0]
    assert batches[1]["page_rows"] == [0]


@pytest.mark.parametrize("bad_rows", [
    [{"act_symbol": "OTHER", "ex_date": "2025-01-01", "amount": 1}],
    [{"act_symbol": "LEVI", "ex_date": "2018-01-01", "amount": 1}],
    [{"act_symbol": "LEVI", "ex_date": "2025-01-01", "amount": float("nan")}],
    [{"act_symbol": "LEVI", "ex_date": "2025-02-01", "amount": 1}, {"act_symbol": "LEVI", "ex_date": "2025-01-01", "amount": 1}],
])
def test_action_query_rejects_noncanonical_or_unordered_provider_pages(monkeypatch, bad_rows):
    monkeypatch.setattr(sync_dolthub, "API_DELAY", 0)
    monkeypatch.setattr(sync_dolthub, "query", lambda *_args: bad_rows)
    with pytest.raises(RuntimeError):
        sync_dolthub._fetch_action_table("dividend", ["amount"], ["LEVI"], date(2019, 1, 1), date(2026, 10, 11))
