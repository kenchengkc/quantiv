from datetime import date
import json

import duckdb
import pandas as pd

from scripts.setup_duckdb_from_parquet import setup_corporate_action_coverage
from scripts.tests.historical_admission_fixtures import write_action_receipt


def test_setup_coverage_binds_original_source_universe_and_query_window(tmp_path):
    receipt = write_action_receipt(tmp_path)
    # A later prospective partition does not replace historical action coverage.
    newer = tmp_path / "parquet/options_chain/year=2026/month=09/2026-09-30.parquet"
    pd.DataFrame({"act_symbol": ["OTHER"]}).to_parquet(newer, index=False)
    conn = duckdb.connect()
    setup_corporate_action_coverage(conn, tmp_path)
    assert conn.execute("SELECT act_symbol,window_start,window_end,receipt_id FROM v_corporate_action_coverage").fetchall() == [
        ("TEST", date(2019, 1, 1), date(2026, 9, 1), receipt["receipt_id"])]


def test_setup_without_receipt_still_installs_empty_fail_closed_coverage(tmp_path):
    conn = duckdb.connect()
    setup_corporate_action_coverage(conn, tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM v_corporate_action_coverage").fetchone()[0] == 0
    assert [row[0] for row in conn.execute("DESCRIBE v_corporate_action_coverage").fetchall()] == [
        "act_symbol", "window_start", "window_end", "receipt_id"]


def test_setup_invalid_receipt_cannot_grant_coverage(tmp_path):
    write_action_receipt(tmp_path)
    path = tmp_path / "control/ingestion/corporate_actions/latest.json"
    receipt = json.loads(path.read_text())
    receipt["query_end"] = "2026-10-01"
    path.write_text(json.dumps(receipt))
    conn = duckdb.connect()
    setup_corporate_action_coverage(conn, tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM v_corporate_action_coverage").fetchone()[0] == 0
