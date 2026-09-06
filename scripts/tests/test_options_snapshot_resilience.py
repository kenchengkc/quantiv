from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.options_snapshot_resilience import finalize_snapshot, verify_fallback


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _manifest(*, decision_safe: bool, critical_codes: list[str], source_date: str) -> dict:
    return {
        "manifest_id": "sha256:test-manifest",
        "schema": "quantiv.data-reconciliation.v2",
        "generated_at": "2026-09-06T12:01:00+00:00",
        "quality": {"decision_safe": decision_safe, "critical_exceptions": len(critical_codes)},
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


@pytest.mark.parametrize("decision_safe", [True, False])
def test_stale_report_after_reconciliation_crash_cannot_authorize_publication(
    tmp_path: Path, decision_safe: bool,
) -> None:
    data_dir = tmp_path / "data"
    candidate = data_dir / "parquet/options_chain/year=2026/month=09/2026-09-04.parquet"
    _write(candidate, "candidate")
    manifest_path = data_dir / "validation/data_reconciliation.json"
    _write(manifest_path, json.dumps(_manifest(
        decision_safe=decision_safe,
        critical_codes=[] if decision_safe else ["option_quote_quality_below_limit"],
        source_date="2026-09-04",
    )))
    outputs = tmp_path / "github-output"
    # Reconciliation crashed before replacing yesterday's report. Exercise the
    # actual CLI boundary consumed by Actions, not only its Python return value.
    process = subprocess.run([
        sys.executable, "scripts/options_snapshot_resilience.py",
        "--manifest", str(manifest_path), "--data-dir", str(data_dir),
        "--not-before", "2026-09-07T12:00:00Z", "--github-output", str(outputs),
    ], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)

    assert process.returncode != 0
    assert "predates this refresh" in process.stderr
    assert not outputs.exists()
    assert candidate.read_text() == "candidate"


@pytest.mark.parametrize("payload", [
    {},
    {"quality": {"decision_safe": True}},
    {**_manifest(decision_safe=True, critical_codes=[], source_date="2026-09-04"),
     "quality": {"decision_safe": "false", "critical_exceptions": 0}},
    _manifest(decision_safe=True, critical_codes=["ohlcv_stale"], source_date="2026-09-04"),
    _manifest(decision_safe=False, critical_codes=[], source_date="2026-09-04"),
])
def test_invalid_or_contradictory_report_fails_closed(tmp_path: Path, payload: dict) -> None:
    report = tmp_path / "report.json"
    _write(report, json.dumps(payload))
    with pytest.raises(RuntimeError):
        finalize_snapshot(manifest_path=report, data_dir=tmp_path)
    assert not (tmp_path / "validation/options_snapshot_status.json").exists()


def test_report_for_older_partition_cannot_accept_newest_candidate(tmp_path: Path) -> None:
    _write(tmp_path / "parquet/options_chain/year=2026/month=09/2026-09-04.parquet", "new")
    report = tmp_path / "report.json"
    _write(report, json.dumps(_manifest(
        decision_safe=True, critical_codes=[], source_date="2026-09-01",
    )))
    with pytest.raises(RuntimeError, match="source date does not match"):
        finalize_snapshot(manifest_path=report, data_dir=tmp_path)


@pytest.mark.parametrize("critical_code", [
    "ohlcv_stale", "corporate_action_continuity_failed",
    "source_partition_reconciliation_failed", "options_duplicate_keys",
])
def test_restored_universe_failures_still_block_publication(
    tmp_path: Path, critical_code: str,
) -> None:
    _write(tmp_path / "parquet/options_chain/year=2026/month=09/2026-09-01.parquet", "old")
    _published_release(tmp_path, "2026-09-01")
    status_path = tmp_path / "validation/options_snapshot_status.json"
    _write(status_path, json.dumps({"state": "fallback", "active_source_date": "2026-09-01"}))
    report = tmp_path / "report.json"
    _write(report, json.dumps(_manifest(
        decision_safe=False, critical_codes=[critical_code], source_date="2026-09-01",
    )))
    with pytest.raises(RuntimeError, match="non-options critical failures"):
        verify_fallback(manifest_path=report, data_dir=tmp_path)
    assert "fallback_verified_at" not in json.loads(status_path.read_text())


def test_repeated_fallback_releases_advance_only_independent_data(tmp_path: Path) -> None:
    from scripts.data_release import build_release, verify_release

    active = tmp_path / "parquet/options_chain/year=2026/month=09/2026-09-01.parquet"
    _write(active, "immutable-published-options")
    _, _, initial = build_release(tmp_path)
    initial_options = initial["files"]
    candidate_report = tmp_path / "candidate.json"
    restored_report = tmp_path / "restored.json"
    for source_date in ("2026-09-04", "2026-09-05"):
        candidate = active.with_name(f"{source_date}.parquet")
        _write(candidate, f"rejected-{source_date}")
        healthy = tmp_path / f"parquet/ohlcv/year=2026/month=09/{source_date}.parquet"
        _write(healthy, f"healthy-{source_date}")
        _write(candidate_report, json.dumps(_manifest(
            decision_safe=False, critical_codes=["option_quote_quality_below_limit"],
            source_date=source_date,
        )))
        result = finalize_snapshot(manifest_path=candidate_report, data_dir=tmp_path)
        assert result.can_score is False
        assert not candidate.exists()
        _write(restored_report, json.dumps(_manifest(
            decision_safe=False, critical_codes=["options_stale"], source_date="2026-09-01",
        )))
        verify_fallback(manifest_path=restored_report, data_dir=tmp_path)
        _, _, release = build_release(tmp_path)
        assert verify_release(tmp_path)["status"] == "passed"
        assert [item for item in release["files"]
                if item["path"].startswith("parquet/options_chain/")] == initial_options
        assert any(item["path"] == str(healthy.relative_to(tmp_path)) for item in release["files"])
        status = json.loads((tmp_path / "validation/options_snapshot_status.json").read_text())
        assert status["fallback_manifest_id"]
        assert status["policy"]["scoring_allowed"] is False


def test_workflow_checks_restored_report_before_r2_promotion() -> None:
    import yaml

    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/daily-refresh.yml").read_text())
    steps = workflow["jobs"]["refresh"]["steps"]
    finalizer = next(step for step in steps if step.get("id") == "options_gate")
    restore = next(step for step in steps if step["name"].startswith("Restore validated fallback"))
    promotion = next(step for step in steps if step["name"].startswith("Promote reconciled"))
    assert '--not-before "$REFRESH_STARTED_AT"' in finalizer["run"]
    assert "--verify-fallback" in restore["run"]
    assert '--not-before "$REFRESH_STARTED_AT"' in restore["run"]
    assert restore["run"].index("build_data_reconciliation.py") < restore["run"].index("--verify-fallback")
    assert not restore.get("continue-on-error", False)
    assert steps.index(finalizer) < steps.index(restore) < steps.index(promotion)
