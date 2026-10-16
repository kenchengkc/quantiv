"""Feature extraction honors explicit runner CPU and memory budgets."""

import duckdb
import pytest

import feature_engineering


def _database(tmp_path, monkeypatch):
    path = tmp_path / "features.duckdb"
    with duckdb.connect(str(path)) as conn:
        for view in ("v_options_raw", "v_earnings", "v_straddle_features", "v_ohlcv",
                     "v_realized_vol", "v_vix", "v_volhist", "v_splits", "v_dividends"):
            conn.execute(f"CREATE VIEW {view} AS SELECT 1 AS placeholder")
    monkeypatch.setenv("DUCKDB_PATH", str(path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def test_feature_connection_applies_explicit_memory_and_thread_caps(tmp_path, monkeypatch):
    _database(tmp_path, monkeypatch)
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "512MB")
    monkeypatch.setenv("DUCKDB_THREADS", "2")
    with duckdb.connect() as expected:
        expected.execute("SET memory_limit='512MB'")
        expected_memory = expected.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    with feature_engineering.connect_duckdb() as conn:
        assert conn.execute("SELECT current_setting('threads')").fetchone()[0] == 2
        assert conn.execute("SELECT current_setting('memory_limit')").fetchone()[0] == expected_memory


@pytest.mark.parametrize("threads", ["0", "-1", "many"])
def test_feature_connection_rejects_invalid_thread_caps(tmp_path, monkeypatch, threads):
    _database(tmp_path, monkeypatch)
    monkeypatch.setenv("DUCKDB_THREADS", threads)
    with pytest.raises(ValueError, match="DUCKDB_THREADS"):
        feature_engineering.connect_duckdb()
