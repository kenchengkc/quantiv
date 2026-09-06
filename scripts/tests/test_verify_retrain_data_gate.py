from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.verify_retrain_data_gate import verify_retrain_data_gate

EASTERN = ZoneInfo("America/New_York")


def _write_options_partition(data_dir: Path, source_date: str) -> None:
    year, month, _ = source_date.split("-")
    path = (
        data_dir
        / "parquet"
        / "options_chain"
        / f"year={year}"
        / f"month={month}"
        / f"{source_date}.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test")


def _write_manifest(
    data_dir: Path,
    *,
    source_date: str,
    generated_at: str,
    decision_safe: bool = True,
    critical_codes: tuple[str, ...] = (),
) -> None:
    path = data_dir / "validation" / "data_reconciliation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "manifest_id": "sha256:test-reconciliation",
                "schema": "quantiv.data-reconciliation.v2",
                "generated_at": generated_at,
                "quality": {
                    "status": "passed" if decision_safe else "failed",
                    "decision_safe": decision_safe,
                    "critical_exceptions": len(critical_codes),
                    "warnings": 0,
                },
                "source_reconciliation": {
                    "status": "passed",
                    "source_date": source_date,
                },
                "quote_quality": {
                    "status": "passed",
                    "source_date": source_date,
                },
                "exceptions": [
                    {
                        "code": code,
                        "severity": "critical",
                        "summary": code,
                    }
                    for code in critical_codes
                ],
            }
        )
    )


def test_accepts_current_decision_safe_friday_release_on_sunday(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_options_partition(data_dir, "2026-09-04")
    _write_manifest(
        data_dir,
        source_date="2026-09-04",
        generated_at="2026-09-06T15:00:00+00:00",
    )

    result = verify_retrain_data_gate(
        data_dir=data_dir,
        now=datetime(2026, 9, 6, 12, 0, tzinfo=EASTERN),
    )

    assert result["status"] == "passed"
    assert result["source_date"] == "2026-09-04"
    assert result["expected_source_date"] == "2026-09-04"


def test_labor_day_does_not_require_a_nonexistent_monday_snapshot(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_options_partition(data_dir, "2026-09-04")
    _write_manifest(
        data_dir,
        source_date="2026-09-04",
        generated_at="2026-09-07T14:00:00+00:00",
    )

    result = verify_retrain_data_gate(
        data_dir=data_dir,
        now=datetime(2026, 9, 7, 13, 0, tzinfo=EASTERN),
    )

    assert result["expected_source_date"] == "2026-09-04"


def test_held_reconciliation_blocks_retraining(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_options_partition(data_dir, "2026-09-04")
    _write_manifest(
        data_dir,
        source_date="2026-09-04",
        generated_at="2026-09-06T15:00:00+00:00",
        decision_safe=False,
        critical_codes=("event_quote_coverage_below_limit",),
    )

    with pytest.raises(RuntimeError, match="held by reconciliation"):
        verify_retrain_data_gate(
            data_dir=data_dir,
            now=datetime(2026, 9, 6, 12, 0, tzinfo=EASTERN),
        )


def test_calendar_recent_but_market_session_stale_release_is_rejected(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_options_partition(data_dir, "2026-09-01")
    _write_manifest(
        data_dir,
        source_date="2026-09-01",
        generated_at="2026-09-06T15:00:00+00:00",
    )

    with pytest.raises(RuntimeError, match="latest completed market session"):
        verify_retrain_data_gate(
            data_dir=data_dir,
            now=datetime(2026, 9, 6, 12, 0, tzinfo=EASTERN),
        )


def test_stale_or_mismatched_reconciliation_evidence_is_rejected(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_options_partition(data_dir, "2026-09-04")
    _write_manifest(
        data_dir,
        source_date="2026-09-03",
        generated_at="2026-09-06T15:00:00+00:00",
    )

    with pytest.raises(RuntimeError, match="does not match the published options release"):
        verify_retrain_data_gate(
            data_dir=data_dir,
            now=datetime(2026, 9, 6, 12, 0, tzinfo=EASTERN),
        )

    _write_manifest(
        data_dir,
        source_date="2026-09-04",
        generated_at="2026-09-04T00:00:00+00:00",
    )
    with pytest.raises(RuntimeError, match="current evidence is required"):
        verify_retrain_data_gate(
            data_dir=data_dir,
            now=datetime(2026, 9, 6, 12, 0, tzinfo=EASTERN),
        )


def test_retrain_r2_pull_restores_and_checks_reconciliation() -> None:
    pull_script = (Path(__file__).resolve().parents[1] / "r2_pull.sh").read_text()
    retrain_block = pull_script.split('if [ "${R2_PULL_EARNINGS:-0}" = "1" ]; then', 1)[1]

    assert 'REMOTE/validation/data_reconciliation.json' in retrain_block
    assert 'verify_retrain_data_gate.py --data-dir "$DATA_DIR"' in retrain_block
