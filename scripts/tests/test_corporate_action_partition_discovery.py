from __future__ import annotations

from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from scripts import sync_dolthub


def _write_partition(root, snapshot: date, symbol: str) -> None:
    partition = sync_dolthub._partition_path(snapshot, root)
    partition.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"act_symbol": [symbol]}), partition)


def test_latest_option_universe_uses_newest_partition_without_metadata(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    root = sync_dolthub.parquet_root()
    older = date(2026, 9, 1)
    newer = date(2026, 9, 2)
    _write_partition(root, older, "MSFT")
    _write_partition(root, newer, "AAPL")

    # Legacy aggregate names must not be mistaken for single-date candidates.
    aggregate = root / "year=2026" / "month=09" / "data_0.parquet"
    pq.write_table(pa.table({"act_symbol": ["TSLA"]}), aggregate)

    # This is the state on a fresh R2-backed runner and after a partial
    # multi-date sync fails before cmd_incremental can save its cursor.
    assert not sync_dolthub.metadata_path().exists()

    source_date, symbols = sync_dolthub._latest_option_universe()

    assert source_date == newer
    assert symbols == ["AAPL"]


def test_latest_option_universe_ignores_stale_metadata_cursor(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    root = sync_dolthub.parquet_root()
    older = date(2026, 9, 1)
    newer = date(2026, 9, 2)
    _write_partition(root, older, "MSFT")
    _write_partition(root, newer, "AAPL")
    sync_dolthub.save_meta({"last_sync_date": older.isoformat()})

    source_date, symbols = sync_dolthub._latest_option_universe()

    assert source_date == newer
    assert symbols == ["AAPL"]
