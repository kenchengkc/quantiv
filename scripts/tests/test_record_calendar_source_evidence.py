from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from scripts.record_calendar_source_evidence import build_evidence


def test_records_snapshot_identity_and_bounds(tmp_path: Path) -> None:
    calendar = tmp_path / "earnings_calendar.csv"
    with calendar.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["act_symbol", "date", "timing"])
        writer.writeheader()
        writer.writerow({"act_symbol": "AAPL", "date": "2026-09-08", "timing": "amc"})
        writer.writerow({"act_symbol": "MSFT", "date": "2026-10-20", "timing": "bmo"})
        writer.writerow({"act_symbol": "AAPL", "date": "2026-07-30", "timing": "amc"})

    payload = build_evidence(
        calendar,
        datetime(2026, 9, 9, 12, 34, 56, tzinfo=timezone.utc),
    )

    assert payload["schema"] == "quantiv.calendar-source-evidence.v1"
    assert payload["provider"] == "dolthub"
    assert payload["synced_at"] == "2026-09-09T12:34:56Z"
    assert payload["rows"] == 3
    assert payload["symbols"] == 2
    assert payload["date_min"] == "2026-07-30"
    assert payload["date_max"] == "2026-10-20"
    assert len(payload["source_file_sha256"]) == 64


def test_rejects_empty_calendar(tmp_path: Path) -> None:
    calendar = tmp_path / "earnings_calendar.csv"
    calendar.write_text("act_symbol,date,timing\n")

    with pytest.raises(RuntimeError, match="no usable rows"):
        build_evidence(calendar)


def test_sync_earnings_records_evidence_when_imported_as_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import sync_dolthub

    def fake_query(sql: str, endpoint: str | None = None) -> list[dict[str, str]]:
        del sql, endpoint
        return [
            {
                "act_symbol": "AAPL",
                "date": "2026-09-08",
                "when": "After market close",
            }
        ]

    monkeypatch.setattr(sync_dolthub, "query", fake_query)
    monkeypatch.setattr(sync_dolthub, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(sync_dolthub, "SYMBOLS_DIR", tmp_path / "symbols")
    monkeypatch.setattr(sync_dolthub, "delisted_tickers", lambda: set())
    monkeypatch.setattr(sync_dolthub, "ticker_renames", lambda: {})

    sync_dolthub.sync_earnings()

    evidence_path = tmp_path / "dolthub_earnings_metadata.json"
    assert evidence_path.is_file()
    payload = json.loads(evidence_path.read_text())
    assert payload["schema"] == "quantiv.calendar-source-evidence.v1"
    assert payload["provider"] == "dolthub"
    assert payload["rows"] == 1
    assert payload["symbols"] == 1
