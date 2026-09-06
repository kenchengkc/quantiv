from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ml.model_bundle import (
    create_signed_control_pointer,
    create_signed_registry,
    verify_control_pointer,
    verify_registry,
)
from scripts import provenance_model_rollback as rollback_module


def _keys() -> tuple[bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def _state(tmp_path: Path) -> tuple[Path, str, str, bytes, bytes]:
    models_root = tmp_path / "models"
    control = models_root / "control"
    control.mkdir(parents=True)
    current = "b" * 64
    previous = "a" * 64
    private, public = _keys()
    (models_root / "bundles" / current).mkdir(parents=True)
    (models_root / "bundles" / previous).mkdir(parents=True)
    pointer = create_signed_control_pointer(
        bundle_id=current,
        previous_bundle_id=previous,
        decision={"action": "promote"},
        private_key=private,
    )
    registry = create_signed_registry(
        champion_bundle_id=current,
        challenger_bundle_id=None,
        previous_bundle_id=previous,
        decision={"action": "promote"},
        history=[{"action": "promote", "champion_bundle_id": current}],
        private_key=private,
    )
    (control / "champion.json").write_text(json.dumps(pointer))
    (control / "registry.json").write_text(json.dumps(registry))
    return models_root, current, previous, private, public


def _forecast(path: Path, bundle_id: str) -> None:
    pd.DataFrame(
        {
            "act_symbol": ["AAA", "BBB"],
            "snapshot_date": ["2026-09-01", "2026-08-29"],
            "model_bundle_id": [bundle_id, bundle_id],
        }
    ).to_parquet(path, index=False)


def test_rolls_back_only_to_signed_previous_and_restores_forecast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_root, current, previous, private, public = _state(tmp_path)
    candidate = tmp_path / "rollback.parquet"
    _forecast(candidate, previous)
    monkeypatch.setattr(rollback_module, "verify_bundle_dir", lambda *args, **kwargs: {})

    report = rollback_module.provenance_rollback(
        models_root=models_root,
        expected_current_bundle_id=current,
        target_bundle_id=previous,
        candidate_forecast=candidate,
        production_forecast_dir=tmp_path / "forecasts",
        report_path=tmp_path / "decision.json",
        reason="promotion used a data release held by reconciliation",
        private_key=private,
        public_key=public,
    )

    pointer = verify_control_pointer(
        json.loads((models_root / "control" / "champion.json").read_text()),
        public_key=public,
    )
    registry = verify_registry(
        json.loads((models_root / "control" / "registry.json").read_text()),
        public_key=public,
    )
    assert pointer["champion_bundle_id"] == previous
    assert pointer["previous_bundle_id"] == current
    assert pointer["decision"]["action"] == "operator_provenance_rollback"
    assert registry["champion_bundle_id"] == previous
    assert registry["challenger_bundle_id"] == current
    assert registry["previous_bundle_id"] == current
    assert report["champion_bundle_id"] == previous
    assert report["previous_bundle_id"] == current
    assert report["promoted"] is False
    production = Path(report["production_forecast"])
    assert production.name == "forecasts_2026-09-01.parquet"
    assert set(pd.read_parquet(production)["model_bundle_id"]) == {previous}


def test_rejects_arbitrary_target_before_mutating_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_root, current, previous, private, public = _state(tmp_path)
    candidate = tmp_path / "rollback.parquet"
    _forecast(candidate, previous)
    original_pointer = (models_root / "control" / "champion.json").read_bytes()
    monkeypatch.setattr(rollback_module, "verify_bundle_dir", lambda *args, **kwargs: {})

    with pytest.raises(RuntimeError, match="not the signed previous champion"):
        rollback_module.provenance_rollback(
            models_root=models_root,
            expected_current_bundle_id=current,
            target_bundle_id="c" * 64,
            candidate_forecast=candidate,
            production_forecast_dir=tmp_path / "forecasts",
            report_path=tmp_path / "decision.json",
            reason="invalid provenance",
            private_key=private,
            public_key=public,
        )

    assert (models_root / "control" / "champion.json").read_bytes() == original_pointer
    assert not (tmp_path / "decision.json").exists()


def test_expected_current_bundle_is_a_race_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_root, current, previous, private, public = _state(tmp_path)
    candidate = tmp_path / "rollback.parquet"
    _forecast(candidate, previous)
    monkeypatch.setattr(rollback_module, "verify_bundle_dir", lambda *args, **kwargs: {})

    with pytest.raises(RuntimeError, match="current champion changed"):
        rollback_module.provenance_rollback(
            models_root=models_root,
            expected_current_bundle_id="d" * 64,
            target_bundle_id=previous,
            candidate_forecast=candidate,
            production_forecast_dir=tmp_path / "forecasts",
            report_path=tmp_path / "decision.json",
            reason="invalid provenance",
            private_key=private,
            public_key=public,
        )


def test_forecast_must_be_from_target_bundle_before_control_pointer_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_root, current, previous, private, public = _state(tmp_path)
    candidate = tmp_path / "rollback.parquet"
    _forecast(candidate, current)
    original_pointer = (models_root / "control" / "champion.json").read_bytes()
    monkeypatch.setattr(rollback_module, "verify_bundle_dir", lambda *args, **kwargs: {})

    with pytest.raises(ValueError, match="do not match target"):
        rollback_module.provenance_rollback(
            models_root=models_root,
            expected_current_bundle_id=current,
            target_bundle_id=previous,
            candidate_forecast=candidate,
            production_forecast_dir=tmp_path / "forecasts",
            report_path=tmp_path / "decision.json",
            reason="invalid provenance",
            private_key=private,
            public_key=public,
        )

    assert (models_root / "control" / "champion.json").read_bytes() == original_pointer
