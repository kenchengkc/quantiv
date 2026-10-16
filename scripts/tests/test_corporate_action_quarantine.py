"""Invalid provider actions withhold their issuers without losing other coverage."""

import hashlib
import json
from datetime import date

import duckdb
import pandas as pd
import pytest

from scripts import sync_dolthub
from scripts.build_data_reconciliation import _corporate_actions
from scripts.setup_duckdb_from_parquet import setup_corporate_action_coverage
from scripts.tests.historical_admission_fixtures import publish_action_receipt
from scripts.tests.test_corporate_action_universe import _sources, _declared
from scripts.verify_historical_training_gate import verify_historical_corporate_actions
from ml.causal_features import extract_reaction_labels
from scripts.corporate_action_universe import invalid_action_values


@pytest.mark.parametrize("table,row,invalid", [
    ("split", {"to_factor": "2", "for_factor": "1"}, False),
    ("split", {"to_factor": "0", "for_factor": "0"}, True),
    ("split", {"to_factor": "1", "for_factor": "0"}, True),
    ("split", {"to_factor": None, "for_factor": "1"}, True),
    ("split", {"to_factor": "inf", "for_factor": "1"}, True),
    ("dividend", {"amount": "0"}, False),
    ("dividend", {"amount": "-1"}, True),
    ("dividend", {"amount": "NaN"}, True),
    ("dividend", {"amount": "unknown"}, True),
])
def test_unknown_or_nonphysical_action_values_cannot_establish_coverage(table, row, invalid):
    assert invalid_action_values(table, row) is invalid


def _quarantined_receipt(tmp_path, monkeypatch):
    _sources(tmp_path, monkeypatch)

    def query(sql, *_args):
        if " FROM split " in sql:
            return ([{"act_symbol": "AAPL", "ex_date": "2024-05-17",
                      "to_factor": "0.00000", "for_factor": "0.00000"}]
                    if "'AAPL'" in sql else [])
        return ([{"act_symbol": "APLD", "ex_date": "2024-05-17", "amount": "-1"}]
                if "'APLD'" in sql else [])

    monkeypatch.setattr(sync_dolthub, "query", query)
    return sync_dolthub.sync_corporate_actions("2019-01-01", "2026-10-11")


def test_invalid_actions_exclude_entire_issuers_with_raw_evidence_and_other_coverage(tmp_path, monkeypatch):
    receipt = _quarantined_receipt(tmp_path, monkeypatch)
    evidence = verify_historical_corporate_actions(tmp_path, _declared(tmp_path))
    assert evidence["covered_symbols"] == ["AMWD", "BNY", "LEDGER", "LEVI", "NA", "OLDONLY"]
    assert evidence["quarantined_symbols"] == ["AAPL", "APLD"]
    assert receipt["universe"]["symbols"] == 8
    assert receipt["coverage_universe"]["symbols"] == 6
    assert all(dataset["rows"] == 0 for dataset in receipt["datasets"].values())
    envelope = pd.read_parquet(tmp_path / receipt["quarantine"]["partition"])
    source = json.loads(envelope.iloc[0]["source_bytes"])
    assert source["datasets"]["splits"]["pages"][0]["rows"][0]["to_factor"] == "0.00000"
    conn = duckdb.connect()
    setup_corporate_action_coverage(conn, tmp_path)
    assert conn.execute("SELECT act_symbol FROM v_corporate_action_coverage WHERE act_symbol IN ('AAPL','APLD')").fetchall() == []
    assert conn.execute("SELECT act_symbol FROM v_corporate_action_coverage WHERE act_symbol='LEVI'").fetchall() == [("LEVI",)]
    conn.execute("CREATE VIEW v_earnings AS SELECT act_symbol, DATE '2026-10-08' date, 'bmo' timing FROM (VALUES ('AAPL'),('LEVI')) t(act_symbol)")
    conn.execute("CREATE VIEW v_ohlcv AS SELECT act_symbol, date, close FROM (VALUES ('AAPL'),('LEVI')) t(act_symbol) CROSS JOIN (VALUES (DATE '2026-10-07',10.),(DATE '2026-10-08',11.)) p(date,close)")
    labels = extract_reaction_labels(conn, as_of_date=date(2026, 10, 11))
    assert labels.act_symbol.tolist() == ["LEVI"]


@pytest.mark.parametrize("mutation", ["digest", "unmanifested", "excluded", "valid_raw", "query", "completion", "coverage"])
def test_quarantine_cannot_invent_exclusions_or_hide_incomplete_provider_evidence(tmp_path, monkeypatch, mutation):
    receipt = _quarantined_receipt(tmp_path, monkeypatch)
    declared = _declared(tmp_path)
    quarantine = receipt["quarantine"]
    if mutation == "digest":
        quarantine["partition_sha256"] = "0" * 64
    elif mutation == "unmanifested":
        declared.remove(quarantine["partition"])
    elif mutation == "excluded":
        quarantine["excluded_symbols"] = ["AAPL", "APLD", "LEVI"]
    elif mutation == "coverage":
        receipt["coverage_universe"]["symbols"] = 8
    else:
        path = tmp_path / quarantine["partition"]
        source = json.loads(pd.read_parquet(path).iloc[0]["source_bytes"])
        if mutation == "valid_raw":
            source["datasets"]["splits"]["pages"][0]["rows"][0]["to_factor"] = "1"
            source["datasets"]["splits"]["pages"][0]["rows"][0]["for_factor"] = "1"
        elif mutation == "query":
            source["datasets"]["splits"]["pages"][0]["query"] += " AND act_symbol != 'LEVI'"
        else:
            source["datasets"]["splits"]["batches"][0]["page_rows"] = [1000]
        raw = json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        digest = hashlib.sha256(raw).hexdigest()
        target = path.with_name(digest + ".parquet")
        pd.DataFrame({"source_bytes": [raw]}).to_parquet(target, index=False)
        declared.remove(quarantine["partition"])
        quarantine.update(partition=target.relative_to(tmp_path).as_posix(),
                          partition_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                          source_sha256=digest)
        declared.add(quarantine["partition"])
    publish_action_receipt(tmp_path, receipt)
    with pytest.raises(RuntimeError, match="corporate-action"):
        verify_historical_corporate_actions(tmp_path, declared)


def test_reconciliation_accepts_derived_partial_coverage_without_claiming_quarantined_options(tmp_path, monkeypatch):
    _quarantined_receipt(tmp_path, monkeypatch)
    conn = duckdb.connect()
    conn.execute("CREATE TABLE v_options AS SELECT 'AAPL' act_symbol, DATE '2026-10-09' date UNION ALL SELECT 'BK', DATE '2026-10-09' UNION ALL SELECT 'AMWD', DATE '2026-10-09'")
    conn.execute("CREATE TABLE v_earnings AS SELECT 'LEVI' act_symbol, DATE '2026-10-08' date")
    conn.execute("CREATE TABLE v_ohlcv AS SELECT 'LEVI' act_symbol, DATE '2026-10-07' date UNION ALL SELECT 'LEVI', DATE '2026-10-09'")
    result = _corporate_actions(conn, tmp_path)
    assert result["status"] == "passed", result["errors"]
    assert result["quarantined_symbols"] == ["AAPL", "APLD"]
    assert result["universe_symbols"] == 6
