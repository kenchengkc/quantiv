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


def _published_release(data_dir: Path, options_date: str) -> None:
    manifest_rel = "control/releases/test-release.json"
    _write(
        data_dir / manifest_rel,
        json.dumps(
            {
                "files": [
                    {
                        "path": (
                            "parquet/options_chain/"
                            f"year={options_date[:4]}/month={options_date[5:7]}/"
                            f"{options_date}.parquet"
                        )
                    },
                    {"path": "parquet/ohlcv/year=2026/month=09/2026-09-01.parquet"},
                ]
            }
        ),
    )
    _write(
        data_dir / "control/current_data_release.json",
        json.dumps({"manifest": manifest_rel, "release_id": "test-release"}),
    )


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


def test_quote_quality_failure_restores_published_snapshot(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    prior = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-01.parquet"
    candidate = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-04.parquet"
    _write(prior, "prior")
    _write(candidate, "candidate")
    _published_release(data_dir, "2026-09-01")
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
    assert result.quarantined_source_dates == ("2026-09-04",)
    assert prior.exists()
    assert not candidate.exists()
    assert not ingestion.exists()
    quarantined = list(
        (data_dir / "quarantine/options_candidates/2026-09-04").glob(
            "*/options-2026-09-04.parquet"
        )
    )
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == "candidate"
    metadata = json.loads((data_dir / "sync_metadata.json").read_text())
    assert metadata["last_sync_date"] == "2026-09-01"
    assert metadata["last_options_candidate_status"] == "rejected"


def test_partial_sync_failure_rolls_back_every_unpublished_partition(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    active = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-01.parquet"
    partial_one = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-02.parquet"
    partial_two = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-03.parquet"
    _write(active, "active")
    _write(partial_one, "partial-one")
    _write(partial_two, "partial-two")
    _published_release(data_dir, "2026-09-01")
    for source_date in ("2026-09-02", "2026-09-03"):
        _write(
            data_dir / f"control/ingestion/options/{source_date}.json",
            json.dumps({"source_date": source_date}),
        )
    manifest_path = data_dir / "validation/data_reconciliation.json"
    _write(
        manifest_path,
        json.dumps(
            _manifest(
                decision_safe=False,
                critical_codes=["options_stale", "option_quote_quality_below_limit"],
                source_date="2026-09-03",
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
    assert result.quarantined_source_dates == ("2026-09-02", "2026-09-03")
    assert active.exists()
    assert not partial_one.exists()
    assert not partial_two.exists()
    quarantine_root = data_dir / "quarantine/options_candidates/2026-09-03"
    assert len(list(quarantine_root.glob("*/options-2026-09-02.parquet"))) == 1
    assert len(list(quarantine_root.glob("*/options-2026-09-03.parquet"))) == 1


def test_sync_failure_without_new_partition_keeps_published_snapshot(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    active = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-01.parquet"
    _write(active, "active")
    _published_release(data_dir, "2026-09-01")
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
    assert result.quarantined_source_dates == ()
    assert active.exists()


def test_fallback_requires_published_release_anchor(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    candidate = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-04.parquet"
    _write(candidate, "candidate")
    manifest_path = data_dir / "validation/data_reconciliation.json"
    _write(
        manifest_path,
        json.dumps(
            _manifest(
                decision_safe=False,
                critical_codes=["option_quote_quality_below_limit"],
                source_date="2026-09-04",
            )
        ),
    )

    with pytest.raises(RuntimeError, match="no published data-release"):
        finalize_snapshot(manifest_path=manifest_path, data_dir=data_dir)

    assert candidate.exists()


def test_unrelated_critical_failure_remains_fatal(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    prior = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-01.parquet"
    candidate = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-04.parquet"
    _write(prior, "prior")
    _write(candidate, "candidate")
    _published_release(data_dir, "2026-09-01")
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
    assert status["active_source_date"] == "2026-09-01"
