from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.options_snapshot_resilience import finalize_snapshot


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _manifest(*, decision_safe: bool, critical_codes: list[str], source_date: str) -> dict:
    return {
        "manifest_id": "sha256:test-manifest",
        "quality": {"decision_safe": decision_safe},
        "source_reconciliation": {"source_date": source_date},
        "quote_quality": {
            "source_date": source_date,
            "expected_source_date": source_date,
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


def test_accept_keeps_candidate_and_allows_scoring(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    candidate = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-04.parquet"
    _write(candidate, "candidate")
    manifest_path = data_dir / "validation/data_reconciliation.json"
    _write(
        manifest_path,
        json.dumps(_manifest(decision_safe=True, critical_codes=[], source_date="2026-09-04")),
    )

    result = finalize_snapshot(manifest_path=manifest_path, data_dir=data_dir)

    assert result.state == "accepted"
    assert result.can_score is True
    assert result.active_source_date == "2026-09-04"
    assert candidate.exists()
    status = json.loads((data_dir / "validation/options_snapshot_status.json").read_text())
    assert status["state"] == "accepted"
    assert status["policy"]["thresholds_changed"] is False


def test_quote_quality_failure_quarantines_candidate_and_restores_prior(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    prior = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-01.parquet"
    candidate = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-04.parquet"
    _write(prior, "prior")
    _write(candidate, "candidate")
    ingestion = data_dir / "control/ingestion/options/2026-09-04.json"
    _write(ingestion, json.dumps({"source_date": "2026-09-04"}))
    _write(
        data_dir / "sync_metadata.json",
        json.dumps({"last_sync_date": "2026-09-04", "total_rows_synced": 100}),
    )
    manifest_path = data_dir / "validation/data_reconciliation.json"
    _write(
        manifest_path,
        json.dumps(
            _manifest(
                decision_safe=False,
                critical_codes=[
                    "event_quote_coverage_below_limit",
                    "option_quote_quality_below_limit",
                ],
                source_date="2026-09-04",
            )
        ),
    )

    result = finalize_snapshot(manifest_path=manifest_path, data_dir=data_dir)

    assert result.state == "fallback"
    assert result.can_score is False
    assert result.active_source_date == "2026-09-01"
    assert prior.exists()
    assert not candidate.exists()
    assert not ingestion.exists()
    quarantined = list(
        (data_dir / "quarantine/options_candidates/2026-09-04").glob("*/candidate.parquet")
    )
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == "candidate"
    metadata = json.loads((data_dir / "sync_metadata.json").read_text())
    assert metadata["last_sync_date"] == "2026-09-01"
    assert metadata["last_options_candidate_status"] == "rejected"


def test_sync_failure_keeps_existing_snapshot_without_deleting_it(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    active = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-01.parquet"
    _write(active, "active")
    manifest_path = data_dir / "validation/data_reconciliation.json"
    _write(
        manifest_path,
        json.dumps(
            _manifest(
                decision_safe=False,
                critical_codes=["options_stale", "option_quote_quality_below_limit"],
                source_date="2026-09-01",
            )
        ),
    )

    result = finalize_snapshot(
        manifest_path=manifest_path,
        data_dir=data_dir,
        sync_outcome="failure",
    )

    assert result.state == "fallback"
    assert result.active_source_date == "2026-09-01"
    assert active.exists()


def test_unrelated_critical_failure_remains_fatal(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    candidate = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-04.parquet"
    _write(candidate, "candidate")
    manifest_path = data_dir / "validation/data_reconciliation.json"
    _write(
        manifest_path,
        json.dumps(
            _manifest(
                decision_safe=False,
                critical_codes=["ohlcv_stale", "option_quote_quality_below_limit"],
                source_date="2026-09-04",
            )
        ),
    )

    with pytest.raises(RuntimeError, match="non-options critical failures"):
        finalize_snapshot(manifest_path=manifest_path, data_dir=data_dir)

    assert candidate.exists()
    status = json.loads((data_dir / "validation/options_snapshot_status.json").read_text())
    assert status["state"] == "blocked"
