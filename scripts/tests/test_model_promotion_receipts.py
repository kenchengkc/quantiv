from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import model_promotion_receipts as receipts
from model_promotion_receipts import (
    attach_decision_evidence,
    build_promotion_receipts,
    verify_promotion_receipts,
)


@pytest.fixture(autouse=True)
def _isolated_repo_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = receipts.FEATURE_ENGINEERING_PATH.read_text()
    feature_path = tmp_path / "apps" / "ml" / "feature_engineering.py"
    feature_path.parent.mkdir(parents=True, exist_ok=True)
    feature_path.write_text(source)
    monkeypatch.setattr(receipts, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(receipts, "FEATURE_ENGINEERING_PATH", feature_path)


def _write_training(root: Path, horizon: int, *, snapshot_offset: int | None = None, leaked: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    earnings = pd.to_datetime(["2026-01-20", "2026-02-20", "2026-03-20"])
    frame = pd.DataFrame(
        {
            "atm_iv": [0.2, 0.3, 0.25],
            "target": [0.04, 0.05, 0.03],
            "__earnings_date": earnings.date,
        }
    )
    if snapshot_offset is not None:
        frame["__snapshot_date"] = (earnings - pd.to_timedelta(snapshot_offset, unit="D")).date
    if leaked:
        frame["post_price"] = [101.0, 102.0, 103.0]
    frame.to_parquet(root / f"training_T{horizon}.parquet", index=False)
    (root / f"metadata_T{horizon}.json").write_text(
        json.dumps({"n_samples": len(frame), "feature_cols": ["atm_iv"]})
    )


def _candidate(path: Path) -> str:
    bundle_id = "a" * 64
    path.write_text(json.dumps({"bundle_id": bundle_id, "bundle_dir": "unused"}))
    return bundle_id


def test_build_and_verify_metric_based_promotion_receipts(tmp_path: Path) -> None:
    training = tmp_path / "training"
    _write_training(training, 1, snapshot_offset=1)
    _write_training(training, 7)
    candidate = tmp_path / "candidate.json"
    bundle_id = _candidate(candidate)
    temporal_path = tmp_path / "temporal.json"
    statistical_path = tmp_path / "statistical.json"

    temporal, statistical = build_promotion_receipts(
        candidate,
        training_dir=training,
        temporal_path=temporal_path,
        statistical_path=statistical_path,
        source_revision="deadbeef",
    )

    assert temporal["status"] == "passed"
    assert temporal["violations"] == 0
    assert {row["horizon"] for row in temporal["horizons"]} == {1, 7}
    assert statistical["uses_p_values"] is False
    assert statistical["multiple_testing"]["disposition"] == "not_applicable"

    evidence = verify_promotion_receipts(
        bundle_id,
        training_dir=training,
        temporal_path=temporal_path,
        statistical_path=statistical_path,
    )
    assert len(evidence["temporal_integrity"]["receipt_id"]) == 64
    assert len(evidence["statistical_selection"]["sha256"]) == 64


def test_build_accepts_repo_relative_candidate_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    training = tmp_path / "training"
    _write_training(training, 1, snapshot_offset=1)
    candidate = tmp_path / "candidate.json"
    _candidate(candidate)
    monkeypatch.chdir(tmp_path)

    temporal, _ = build_promotion_receipts(
        Path("candidate.json"),
        training_dir=training,
        temporal_path=tmp_path / "temporal.json",
        statistical_path=tmp_path / "statistical.json",
    )

    assert temporal["candidate_record"]["path"] == "candidate.json"


def test_rejects_future_or_horizon_inconsistent_snapshot(tmp_path: Path) -> None:
    training = tmp_path / "training"
    _write_training(training, 7, snapshot_offset=1)
    candidate = tmp_path / "candidate.json"
    _candidate(candidate)
    with pytest.raises(ValueError, match="future or horizon-inconsistent"):
        build_promotion_receipts(
            candidate,
            training_dir=training,
            temporal_path=tmp_path / "temporal.json",
            statistical_path=tmp_path / "statistical.json",
        )


def test_rejects_label_like_feature_leakage(tmp_path: Path) -> None:
    training = tmp_path / "training"
    _write_training(training, 1, leaked=True)
    candidate = tmp_path / "candidate.json"
    _candidate(candidate)
    with pytest.raises(ValueError, match="future/label-like"):
        build_promotion_receipts(
            candidate,
            training_dir=training,
            temporal_path=tmp_path / "temporal.json",
            statistical_path=tmp_path / "statistical.json",
        )


def test_receipt_fails_closed_after_training_bytes_change(tmp_path: Path) -> None:
    training = tmp_path / "training"
    _write_training(training, 1)
    candidate = tmp_path / "candidate.json"
    bundle_id = _candidate(candidate)
    temporal_path = tmp_path / "temporal.json"
    statistical_path = tmp_path / "statistical.json"
    build_promotion_receipts(
        candidate,
        training_dir=training,
        temporal_path=temporal_path,
        statistical_path=statistical_path,
    )

    frame = pd.read_parquet(training / "training_T1.parquet")
    frame.loc[0, "atm_iv"] = 0.99
    frame.to_parquet(training / "training_T1.parquet", index=False)
    with pytest.raises(ValueError, match="training bytes changed"):
        verify_promotion_receipts(
            bundle_id,
            training_dir=training,
            temporal_path=temporal_path,
            statistical_path=statistical_path,
        )


def test_decision_report_records_receipt_identities(tmp_path: Path) -> None:
    report = tmp_path / "decision.json"
    report.write_text(json.dumps({"schema": "quantiv.model-decision.v1", "status": "passed"}))
    evidence = {
        "temporal_integrity": {"receipt_id": "a" * 64, "sha256": "b" * 64},
        "statistical_selection": {"receipt_id": "c" * 64, "sha256": "d" * 64},
    }
    attach_decision_evidence(report, evidence)
    payload = json.loads(report.read_text())
    assert payload["promotion_evidence"] == evidence
