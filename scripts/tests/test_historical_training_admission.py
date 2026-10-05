import json

import pandas as pd
import pytest

from scripts.data_release import build_release
from scripts.tests.historical_admission_fixtures import publish_action_receipt, write_action_receipt
from scripts.verify_historical_training_gate import verify_historical_training_gate


def _release(tmp_path):
    path = tmp_path / "parquet" / "ohlcv" / "history.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({"date": ["2026-09-01"], "close": [100.0]}).to_parquet(path)
    write_action_receipt(tmp_path)
    build_release(tmp_path)
    held = tmp_path / "validation" / "data_reconciliation.json"
    held.parent.mkdir(exist_ok=True)
    held.write_text(json.dumps({"quality": {"decision_safe": False}}))
    (tmp_path / "earnings_calendar.csv").write_text("act_symbol,date,timing\nTEST,2026-09-01,bmo\n")
    return path


def test_historical_admission_checks_immutable_sources_not_prospective_hold(tmp_path):
    _release(tmp_path)
    result = verify_historical_training_gate(data_dir=tmp_path)
    assert result["status"] == "passed"
    assert result["activation_allowed"] is False
    assert len(result["release_id"]) == 64


def test_historical_admission_rejects_modified_history(tmp_path):
    path = _release(tmp_path)
    path.write_bytes(b"modified")
    with pytest.raises(RuntimeError, match="verification failed"):
        verify_historical_training_gate(data_dir=tmp_path)


def test_historical_admission_requires_historical_price_source(tmp_path):
    path = tmp_path / "parquet" / "options_chain" / "2026-09-30.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({"x": [1]}).to_parquet(path)
    build_release(tmp_path)
    with pytest.raises(RuntimeError, match="historical price"):
        verify_historical_training_gate(data_dir=tmp_path)


def test_calendar_derivative_cannot_change_historical_event_timing(tmp_path):
    _release(tmp_path)
    pd.DataFrame({"act_symbol": ["TEST"], "date": ["2026-09-01"], "timing": ["amc"]}).to_parquet(tmp_path / "earnings_calendar.parquet")
    with pytest.raises(RuntimeError, match="calendar.*canonical"):
        verify_historical_training_gate(data_dir=tmp_path)


def test_exact_calendar_consumed_is_bound_to_admission(tmp_path):
    _release(tmp_path)
    pd.DataFrame({"act_symbol": ["TEST"], "date": ["2026-09-01"], "timing": ["bmo"]}).to_parquet(tmp_path / "earnings_calendar.parquet")
    result = verify_historical_training_gate(data_dir=tmp_path)
    assert result["calendar_source"]["path"] == "earnings_calendar.parquet"
    assert len(result["calendar_source"]["sha256"]) == 64


@pytest.mark.parametrize("with_derivative", [False, True])
def test_historical_calendar_preserves_literal_na_ticker(tmp_path, with_derivative):
    _release(tmp_path)
    frame = pd.DataFrame({"act_symbol": ["NA", "TEST"],
                          "date": ["2026-09-01", "2026-09-01"],
                          "timing": ["bmo", "amc"],
                          "eps_estimate": [None, 1.5]})
    frame.to_csv(tmp_path / "earnings_calendar.csv", index=False)
    if with_derivative:
        frame.to_parquet(tmp_path / "earnings_calendar.parquet", index=False)
    result = verify_historical_training_gate(data_dir=tmp_path)
    assert result["status"] == "passed"
    assert result["calendar_source"]["events"] == 2


def test_literal_na_ticker_still_requires_matching_derivative(tmp_path):
    _release(tmp_path)
    (tmp_path / "earnings_calendar.csv").write_text(
        "act_symbol,date,timing\nNA,2026-09-01,bmo\n")
    pd.DataFrame({"act_symbol": ["TEST"], "date": ["2026-09-01"],
                  "timing": ["bmo"]}).to_parquet(tmp_path / "earnings_calendar.parquet")
    with pytest.raises(RuntimeError, match="calendar.*canonical"):
        verify_historical_training_gate(data_dir=tmp_path)


@pytest.mark.parametrize("blank", ["", "   "])
def test_historical_calendar_still_rejects_actual_blank_tickers(tmp_path, blank):
    _release(tmp_path)
    pd.DataFrame({"act_symbol": ["TEST", blank],
                  "date": ["2026-09-01", "2026-09-01"],
                  "timing": ["bmo", "amc"]}).to_csv(
        tmp_path / "earnings_calendar.csv", index=False)
    with pytest.raises(RuntimeError, match="blank symbols"):
        verify_historical_training_gate(data_dir=tmp_path)


def test_historical_admission_requires_verified_corporate_action_receipt(tmp_path):
    _release(tmp_path)
    (tmp_path / "control/ingestion/corporate_actions/latest.json").unlink()
    with pytest.raises(RuntimeError, match="corporate-action receipt"):
        verify_historical_training_gate(data_dir=tmp_path)


def test_corporate_action_receipt_identity_cannot_be_rewritten(tmp_path):
    _release(tmp_path)
    path = tmp_path / "control/ingestion/corporate_actions/latest.json"
    receipt = json.loads(path.read_text())
    receipt["query_end"] = "2026-10-01"
    path.write_text(json.dumps(receipt))
    with pytest.raises(RuntimeError, match="receipt identity"):
        verify_historical_training_gate(data_dir=tmp_path)


@pytest.mark.parametrize("mutation", ["unmanifested", "digest", "pagination", "universe", "window", "immutable_receipt"])
def test_historical_admission_rejects_incomplete_action_provenance(tmp_path, mutation):
    _release(tmp_path)
    receipt = json.loads((tmp_path / "control/ingestion/corporate_actions/latest.json").read_text())
    if mutation == "unmanifested":
        old = tmp_path / receipt["datasets"]["splits"]["partition"]
        new = tmp_path / "unmanifested" / old.name
        new.parent.mkdir()
        new.write_bytes(old.read_bytes())
        receipt["datasets"]["splits"]["partition"] = new.relative_to(tmp_path).as_posix()
    elif mutation == "digest":
        receipt["datasets"]["splits"]["partition_sha256"] = "0" * 64
    elif mutation == "pagination":
        receipt["datasets"]["splits"]["batches"][0]["completion"] = "row_limit"
    elif mutation == "universe":
        receipt["universe"]["symbols_sha256"] = "0" * 64
    elif mutation == "window":
        receipt["query_end"] = "2026-08-01"
    receipt = publish_action_receipt(tmp_path, receipt)
    if mutation == "immutable_receipt":
        (tmp_path / f"control/ingestion/corporate_actions/receipts/{receipt['source_options_date']}/{receipt['receipt_id']}.json").unlink()
    with pytest.raises(RuntimeError, match="corporate-action"):
        verify_historical_training_gate(data_dir=tmp_path)


def test_historical_actions_can_use_prior_source_snapshot_with_newer_options(tmp_path):
    _release(tmp_path)
    path = tmp_path / "parquet/options_chain/year=2026/month=09/2026-09-30.parquet"
    pd.DataFrame({"act_symbol": ["OTHER"]}).to_parquet(path, index=False)
    build_release(tmp_path)
    result = verify_historical_training_gate(data_dir=tmp_path)
    assert result["corporate_actions"]["source_options_date"] == "2026-09-01"
    assert set(result["corporate_actions"]["datasets"]) == {"splits", "dividends"}


def test_invalid_actions_cannot_be_admitted_even_with_authentic_receipt(tmp_path):
    _release(tmp_path)
    write_action_receipt(tmp_path, splits=[["TEST", "2026-09-01", -2., 1.]])
    build_release(tmp_path)
    with pytest.raises(RuntimeError, match="corporate-action.*invalid"):
        verify_historical_training_gate(data_dir=tmp_path)


def test_admission_declares_bounded_action_coverage_without_claiming_entire_calendar(tmp_path):
    _release(tmp_path)
    receipt = json.loads((tmp_path / "control/ingestion/corporate_actions/latest.json").read_text())
    receipt["query_start"] = "2026-08-01"
    publish_action_receipt(tmp_path, receipt)
    (tmp_path / "earnings_calendar.csv").write_text(
        "act_symbol,date,timing\nTEST,2026-09-01,bmo\nOTHER,2025-09-01,amc\n"
    )
    actions = verify_historical_training_gate(data_dir=tmp_path)["corporate_actions"]
    assert actions["covered_symbols"] == ["TEST"]
    assert actions["query_start"] == "2026-08-01"
    assert actions["query_end"] == "2026-09-01"
    assert len(actions["immutable_receipt_sha256"]) == 64
