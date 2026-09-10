from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ml.evidence_receipt import build_evidence_receipt, publish_evidence_receipt
from ml.model_bundle import (
    ModelBundleError,
    create_signed_bundle,
    create_signed_control_pointer,
    required_artifact_names,
)
from tools.build_public_validation import HORIZONS, build_validation


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _metadata(horizon: int, *, model_mae: float, baseline_mae: float) -> dict:
    return {
        "horizon": horizon,
        "n_train": 90,
        "n_val": 30,
        "val_mae": model_mae,
        "val_rmse": model_mae * 1.2,
        "val_r2": 0.2,
        "baseline_straddle_mae": baseline_mae,
        "q10_coverage": 0.10,
        "q25_coverage": 0.25,
        "q50_coverage": 0.50,
        "q75_coverage": 0.75,
        "q90_coverage": 0.90,
        "coverage_50": 0.50,
        "coverage_80": 0.80,
        "interval_width_50_mean": 0.05,
        "interval_width_80_mean": 0.10,
        "feature_cols": ["a", "b"],
        "quantiles": [0.1, 0.25, 0.5, 0.75, 0.9],
        "version": "test",
        "trained_at": "2026-01-01T00:00:00+00:00",
        "validation_split": {
            "train_start": "2023-01-01",
            "train_end": "2025-09-30",
            "validation_start": "2025-10-06",
            "validation_end": "2026-01-01",
            "purge_days": 5,
            "rows_total": 125,
            "rows_train": 90,
            "rows_purged": 5,
            "rows_validation": 30,
        },
        "walk_forward_validation": {
            "status": "passed",
            "method": "expanding_purged_walk_forward",
            "purge_days": 7,
            "test_days": 45,
            "requested_folds": 5,
            "fold_count": 5,
            "validation_rows": 100,
            "model_mae": model_mae,
            "baseline_straddle_mae": baseline_mae,
            "improvement_vs_straddle": 1.0 - model_mae / baseline_mae,
            "folds_beating_baseline": 4,
            "worst_fold_ratio": 1.1,
        },
    }


def _write_common_public_evidence(repo: Path, *, model_sha: str = "sha256:model") -> None:
    _write_json(
        repo / "apps/frontend/public/evidence/forecast.json",
        {
            "receipt_id": "sha256:forecast",
            "validated_at": "2026-01-02T00:00:00+00:00",
            "quality": {"status": "passed"},
            "coverage": {"rows": 12, "events": 7},
            "controls": {"exceptions": 0},
            "artifact_bundles": [
                {"name": "model_bundle", "sha256": model_sha}
            ],
        },
    )
    _write_json(
        repo / "apps/frontend/public/control-plane.json",
        {
            "status": "degraded",
            "publication_eligible": True,
            "data": {"status": "degraded"},
            "model": {"status": "passed", "drift_status": "warning"},
        },
    )


def _keys(tmp_path: Path) -> tuple[bytes, Path]:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_path = tmp_path / "public.pem"
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_pem, public_path


def _receipt_artifact(receipt: dict, name: str) -> dict:
    return next(item for item in receipt["artifacts"] if item["name"] == name)


def _install_signed_champion(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_mae: float = 0.03,
    baseline_mae: float = 0.06,
) -> tuple[str, dict, bytes]:
    models = repo / "data/models"
    training = repo / "data/ml_training"
    models.mkdir(parents=True, exist_ok=True)
    training.mkdir(parents=True, exist_ok=True)

    metadata_by_name = {
        f"metadata_T{horizon}.json": _metadata(
            horizon, model_mae=model_mae, baseline_mae=baseline_mae
        )
        for horizon in HORIZONS
    }
    for name in required_artifact_names(HORIZONS):
        path = models / name
        if name in metadata_by_name:
            _write_json(path, metadata_by_name[name])
        else:
            path.write_bytes(f"artifact:{name}".encode())
    for horizon in HORIZONS:
        (training / f"training_T{horizon}.parquet").write_bytes(
            f"training:{horizon}".encode()
        )
        _write_json(training / f"metadata_T{horizon}.json", {"horizon": horizon})

    validation_report = {
        "status": "passed",
        "validated_at": "2026-01-02T00:00:00+00:00",
        "issues": [],
        "stages": {
            "models": {
                "status": "passed",
                "horizons": {str(horizon): {} for horizon in HORIZONS},
            }
        },
    }
    model_receipt = build_evidence_receipt(
        validation_report,
        scope="models",
        repo_root=repo,
        data_dir=repo / "data",
        training_dir=training,
        models_dir=models,
        forecast_path=None,
        horizons=HORIZONS,
    )
    validation_report["evidence_receipt"] = model_receipt
    _, latest_receipt_path = publish_evidence_receipt(
        validation_report,
        receipt_dir=models / "receipts",
        scope="models",
        forecast_path=None,
    )
    report_path = repo / "data/validation/retrain_models.json"
    _write_json(report_path, validation_report)

    private, public_path = _keys(repo)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public_path))
    bundle_dir, manifest = create_signed_bundle(
        models,
        models / "bundles",
        receipt_path=latest_receipt_path,
        validation_report_path=report_path,
        source_revision="source-revision-test",
        horizons=HORIZONS,
        private_key=private,
    )
    champion = manifest["bundle_id"]
    _write_json(
        models / "control/champion.json",
        create_signed_control_pointer(
            bundle_id=champion,
            previous_bundle_id=None,
            decision={"action": "bootstrap"},
            private_key=private,
        ),
    )
    assert bundle_dir.name == champion
    _write_common_public_evidence(
        repo, model_sha=_receipt_artifact(model_receipt, "model_bundle")["sha256"]
    )
    return champion, model_receipt, private


def test_build_validation_uses_baked_fallback(tmp_path: Path) -> None:
    for horizon in HORIZONS:
        _write_json(
            tmp_path / f"apps/ml/models/metadata_T{horizon}.json",
            _metadata(horizon, model_mae=0.04, baseline_mae=0.06),
        )
    _write_common_public_evidence(tmp_path)

    payload = build_validation(tmp_path, generated_at="2026-01-03T00:00:00+00:00")

    assert payload["schema"] == "quantiv.public-model-validation.v1"
    assert payload["model_source"]["kind"] == "baked_fallback"
    assert payload["model_source"]["bundle_id"] is None
    assert payload["model_source"]["artifact_sha256"] is None
    assert payload["model_source"]["verification_status"] == "preview_unverified"
    assert payload["evaluation_evidence"] == {
        "status": "preview_unverified",
        "receipt": None,
    }
    assert payload["summary"]["validation_row_observations"] == 180
    assert payload["summary"]["weighted_model_mae"] == pytest.approx(0.04)
    assert payload["summary"]["weighted_straddle_mae"] == pytest.approx(0.06)
    assert payload["summary"]["weighted_relative_mae_improvement"] == pytest.approx(1 / 3)
    assert payload["summary"]["weighted_coverage"]["interval_80"] == pytest.approx(0.8)
    assert payload["current_evidence"]["publication_eligible"] is True
    assert payload["validation_protocol"]["walk_forward"]["source"] == "preview_unverified"
    assert payload["validation_protocol"]["live_trading_eligible"] is False


def test_build_validation_verifies_active_champion_and_run_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    champion, model_receipt, _ = _install_signed_champion(tmp_path, monkeypatch)

    payload = build_validation(tmp_path, generated_at="2026-01-03T00:00:00+00:00")

    assert payload["model_source"]["kind"] == "signed_champion"
    assert payload["model_source"]["bundle_id"] == champion
    assert payload["model_source"]["verification_status"] == "verified"
    assert payload["model_source"]["source_revision"] == "source-revision-test"
    assert payload["model_source"]["model_validation_receipt_id"] == model_receipt["receipt_id"]
    assert payload["model_source"]["artifact_sha256"] == _receipt_artifact(
        model_receipt, "model_bundle"
    )["sha256"]
    assert payload["summary"]["weighted_model_mae"] == pytest.approx(0.03)
    assert payload["summary"]["weighted_relative_mae_improvement"] == pytest.approx(0.5)
    assert payload["validation_protocol"]["walk_forward"]["expanding_windows"] == 5
    assert payload["validation_protocol"]["walk_forward"]["validation_window_days"] == 45
    assert payload["validation_protocol"]["walk_forward"]["purge_days"] == 7
    assert payload["validation_protocol"]["walk_forward"]["source"] == "verified_model_metadata"
    assert len(payload["validation_protocol"]["holdout_splits"]) == len(HORIZONS)
    assert payload["current_evidence"]["forecast_model_matches_evaluation"] is True
    evaluation = payload["evaluation_evidence"]
    assert evaluation["status"] == "verified"
    assert evaluation["receipt"]["schema"] == "quantiv.model-evaluation-receipt.v1"
    assert evaluation["receipt"]["bundle_id"] == champion
    assert evaluation["receipt"]["model_validation_receipt_id"] == model_receipt["receipt_id"]
    assert evaluation["receipt"]["receipt_id"].startswith("sha256:")


def test_signed_champion_does_not_fall_back_on_invalid_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_signed_champion(tmp_path, monkeypatch)
    pointer_path = tmp_path / "data/models/control/champion.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["decision"] = {"action": "tampered"}
    _write_json(pointer_path, pointer)

    with pytest.raises(ModelBundleError, match="signature"):
        build_validation(tmp_path)


def test_signed_champion_rejects_modified_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    champion, _, _ = _install_signed_champion(tmp_path, monkeypatch)
    path = tmp_path / f"data/models/bundles/{champion}/metadata_T1.json"
    metadata = json.loads(path.read_text())
    metadata["val_mae"] = 0.0001
    _write_json(path, metadata)

    with pytest.raises(ModelBundleError, match="mismatch"):
        build_validation(tmp_path)


def test_signed_champion_rejects_mismatched_validation_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, receipt, _ = _install_signed_champion(tmp_path, monkeypatch)
    changed_report = {
        "status": "passed",
        "issues": [],
        "stages": {
            "models": {
                "status": "passed",
                "horizons": {str(horizon): {} for horizon in HORIZONS},
                "different_run_marker": True,
            }
        },
    }
    changed = build_evidence_receipt(
        changed_report,
        scope="models",
        repo_root=tmp_path,
        data_dir=tmp_path / "data",
        training_dir=tmp_path / "data/ml_training",
        models_dir=tmp_path / "data/models",
        forecast_path=None,
        horizons=HORIZONS,
    )
    assert changed["receipt_id"] != receipt["receipt_id"]
    receipt_digest = receipt["receipt_id"].removeprefix("sha256:")
    _write_json(
        tmp_path
        / f"data/models/receipts/models.{receipt_digest[:12]}.receipt.json",
        changed,
    )

    with pytest.raises(ModelBundleError, match="identities disagree"):
        build_validation(tmp_path)


def test_signed_champion_uses_immutable_receipt_not_latest_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    champion, receipt, _ = _install_signed_champion(tmp_path, monkeypatch)
    latest = json.loads(json.dumps(receipt))
    latest["receipt_id"] = "sha256:" + "f" * 64
    _write_json(tmp_path / "data/models/receipts/latest_models.json", latest)

    payload = build_validation(tmp_path)

    assert payload["model_source"]["bundle_id"] == champion
    assert payload["model_source"]["model_validation_receipt_id"] == receipt["receipt_id"]


def test_signed_champion_rejects_passed_forecast_from_other_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_signed_champion(tmp_path, monkeypatch)
    _write_common_public_evidence(tmp_path, model_sha="other-model-digest")

    with pytest.raises(ModelBundleError, match="forecast evidence"):
        build_validation(tmp_path)


def test_build_validation_fails_when_horizon_metadata_is_missing(tmp_path: Path) -> None:
    for horizon in HORIZONS[:-1]:
        _write_json(
            tmp_path / f"apps/ml/models/metadata_T{horizon}.json",
            _metadata(horizon, model_mae=0.04, baseline_mae=0.06),
        )
    _write_common_public_evidence(tmp_path)

    with pytest.raises(FileNotFoundError, match="metadata_T21.json"):
        build_validation(tmp_path)
