from __future__ import annotations

import json
from pathlib import Path

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
    }


def _write_common_public_evidence(repo: Path) -> None:
    _write_json(
        repo / "apps/frontend/public/evidence/forecast.json",
        {
            "receipt_id": "sha256:forecast",
            "validated_at": "2026-01-02T00:00:00+00:00",
            "quality": {"status": "passed"},
            "coverage": {"rows": 12, "events": 7},
            "controls": {"exceptions": 0},
            "artifact_bundles": [
                {"name": "model_bundle", "sha256": "sha256:model"}
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


def test_build_validation_uses_baked_fallback(tmp_path: Path) -> None:
    for horizon in HORIZONS:
        _write_json(
            tmp_path / f"apps/ml/models/metadata_T{horizon}.json",
            _metadata(horizon, model_mae=0.04, baseline_mae=0.06),
        )
    _write_common_public_evidence(tmp_path)

    payload = build_validation(tmp_path, generated_at="2026-01-03T00:00:00+00:00")

    assert payload["schema"] == "quantiv.public-model-validation.v1"
    assert payload["model_source"] == {
        "kind": "baked_fallback",
        "bundle_id": None,
        "artifact_sha256": "sha256:model",
    }
    assert payload["summary"]["validation_row_observations"] == 180
    assert payload["summary"]["weighted_model_mae"] == 0.04
    assert payload["summary"]["weighted_straddle_mae"] == 0.06
    assert payload["summary"]["weighted_relative_mae_improvement"] == 1 / 3
    assert payload["summary"]["weighted_coverage"]["interval_80"] == 0.8
    assert payload["current_evidence"]["publication_eligible"] is True
    assert payload["validation_protocol"]["live_trading_eligible"] is False


def test_build_validation_prefers_active_champion_bundle(tmp_path: Path) -> None:
    champion = "a" * 64
    for horizon in HORIZONS:
        _write_json(
            tmp_path / f"apps/ml/models/metadata_T{horizon}.json",
            _metadata(horizon, model_mae=0.05, baseline_mae=0.06),
        )
        _write_json(
            tmp_path / f"data/models/bundles/{champion}/metadata_T{horizon}.json",
            _metadata(horizon, model_mae=0.03, baseline_mae=0.06),
        )
    _write_json(
        tmp_path / "data/models/control/champion.json",
        {"champion_bundle_id": champion},
    )
    _write_common_public_evidence(tmp_path)

    payload = build_validation(tmp_path, generated_at="2026-01-03T00:00:00+00:00")

    assert payload["model_source"]["kind"] == "signed_champion"
    assert payload["model_source"]["bundle_id"] == champion
    assert payload["summary"]["weighted_model_mae"] == 0.03
    assert payload["summary"]["weighted_relative_mae_improvement"] == 0.5


def test_build_validation_fails_when_horizon_metadata_is_missing(tmp_path: Path) -> None:
    for horizon in HORIZONS[:-1]:
        _write_json(
            tmp_path / f"apps/ml/models/metadata_T{horizon}.json",
            _metadata(horizon, model_mae=0.04, baseline_mae=0.06),
        )
    _write_common_public_evidence(tmp_path)

    try:
        build_validation(tmp_path)
    except FileNotFoundError as exc:
        assert "metadata_T21.json" in str(exc)
    else:
        raise AssertionError("missing horizon metadata must fail closed")
