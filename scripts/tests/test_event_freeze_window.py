from datetime import date

import duckdb
import pytest

import check_event_freeze_window as gate


@pytest.mark.parametrize(
    ("ohlcv_date", "options_date", "ready", "reason"),
    [
        (date(2026, 10, 2), date(2026, 10, 2), "true", "current_session_inputs"),
        (date(2026, 10, 2), date(2026, 10, 1), "false", "source_lag_ohlcv_2026-10-02_options_2026-10-01"),
        (date(2026, 10, 1), date(2026, 10, 2), "false", "source_lag_ohlcv_2026-10-01_options_2026-10-02"),
        (date(2026, 10, 2), None, "false", "source_lag_ohlcv_2026-10-02_options_None"),
        (None, date(2026, 10, 2), "false", "source_lag_ohlcv_None_options_2026-10-02"),
    ],
)
def test_data_gate_uses_canonical_market_views(
    tmp_path, monkeypatch, ohlcv_date, options_date, ready, reason
):
    db = tmp_path / "market.duckdb"
    with duckdb.connect(str(db)) as connection:
        # setup_duckdb_from_parquet publishes these views with a DATE column.
        # The collector must consume that contract, including empty feeds.
        for name, source_date in (("v_ohlcv", ohlcv_date), ("v_options", options_date)):
            connection.execute(f"CREATE TABLE source_{name} (date DATE)")
            if source_date is not None:
                connection.execute(f"INSERT INTO source_{name} VALUES (?)", [source_date])
            connection.execute(f"CREATE VIEW {name} AS SELECT * FROM source_{name}")
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(
        "sys.argv",
        ["freeze", "--phase", "data", "--db", str(db), "--now", "2026-10-02T21:30:00-04:00"],
    )

    assert gate.main() == 0
    assert output.read_text().splitlines() == [
        f"ready={ready}", f"reason={reason}", "session_date=2026-10-02"
    ]


@pytest.mark.parametrize(
    ("now", "reason"),
    [
        ("2026-10-03T21:30:00-04:00", "not_market_session"),
        ("2026-11-27T12:59:00-05:00", "market_session_not_closed"),
        ("2026-10-02T21:30:00-04:00", "missing_duckdb"),
    ],
)
def test_data_gate_remains_closed_before_inputs_are_eligible(tmp_path, monkeypatch, now, reason):
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(
        "sys.argv", ["freeze", "--phase", "data", "--db", str(tmp_path / "missing.duckdb"), "--now", now]
    )

    assert gate.main() == 0
    assert output.read_text().splitlines() == ["ready=false", f"reason={reason}"]
