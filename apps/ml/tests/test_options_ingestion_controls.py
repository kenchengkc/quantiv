from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import sync_dolthub  # noqa: E402


def _frame(bid: float = 1.0) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "date": date(2026, 8, 21),
                "act_symbol": "TEST",
                "expiration": date(2026, 8, 28),
                "strike": 100.0,
                "call_put": "Call",
                "bid": bid,
                "ask": 1.2,
                "vol": 0.5,
                "delta": 0.5,
                "gamma": 0.1,
                "theta": -0.1,
                "vega": 0.1,
                "rho": 0.01,
            }
        ]
    )
    frame.attrs["ingestion_evidence"] = {
        "expected_rows": 1,
        "received_rows": 1,
        "expected_method": "exhaustive_keyset_pagination",
        "buckets": [{"symbol_range": [None, None], "rows": 1, "exhausted": True}],
    }
    return frame


def test_partition_promotion_and_replay_equivalence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    root = sync_dolthub.parquet_root()
    target = date(2026, 8, 21)
    frame = _frame()

    assert sync_dolthub.write_date(frame, root) == 1
    baseline = sync_dolthub._write_ingestion_manifest(target, frame, root)
    assert baseline["expected_rows"] == baseline["received_rows"] == 1
    assert baseline["replay_equivalence"] == "verified"
    assert baseline["source_revision_status"] == "baseline_recorded"
    assert not list(root.rglob("*.tmp"))

    replay = sync_dolthub._write_ingestion_manifest(
        target, frame, root, prior_manifest=baseline
    )
    assert replay["replay_equivalence"] == "verified"
    assert replay["source_revision_status"] == "unchanged"
    stored = json.loads(sync_dolthub._manifest_path(target).read_text())
    assert stored["partition_sha256"] == replay["partition_sha256"]

    with pytest.raises(RuntimeError, match="replay-equivalent|replay digest changed"):
        sync_dolthub._write_ingestion_manifest(
            target, _frame(bid=1.1), root, prior_manifest=baseline
        )


def test_incremental_current_partition_persists_source_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    root = sync_dolthub.parquet_root()
    target = date(2026, 8, 21)
    frame = _frame()
    assert sync_dolthub.write_date(frame, root) == 1

    class FixedDate(date):
        @classmethod
        def today(cls) -> FixedDate:
            return cls(2026, 8, 25)

    monkeypatch.setattr(sync_dolthub, "date", FixedDate)
    monkeypatch.setattr(sync_dolthub, "latest_dolthub_date", lambda: FixedDate(2026, 8, 21))

    sync_dolthub.cmd_incremental(SimpleNamespace(days=3))

    meta = json.loads(sync_dolthub.metadata_path().read_text())
    assert meta["last_sync_date"] == "2026-08-21"
    assert meta["mode"] == "incremental"
    assert sync_dolthub._manifest_path(FixedDate(2026, 8, 21)).exists()

    source_date, symbols = sync_dolthub._latest_option_universe()
    assert source_date == FixedDate(2026, 8, 21)
    assert symbols == ["TEST"]


def test_sync_aborts_on_source_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    def fail(_: date) -> pd.DataFrame:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(sync_dolthub, "fetch_date", fail)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        sync_dolthub.sync_dates(
            [date(2026, 8, 21)], sync_dolthub.parquet_root(), skip_existing=False
        )
    assert not sync_dolthub.metadata_path().exists()
