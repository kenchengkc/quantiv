from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from ml.pipeline_validation import (
    PipelineValidationError,
    validate_forecast_artifact,
    validate_model_artifacts,
    validate_training_artifacts,
)


FEATURES = ["atm_iv", "dte", "em_iv_pct", "straddle_pct"] + [
    f"feature_{index}" for index in range(6)
]


class ConstantEstimator:
    def __init__(self, feature_names: list[str], prediction: float = 0.07):
        self.feature_name_ = feature_names
        self.prediction = prediction

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), self.prediction, dtype=float)


def _issue_codes(exc: PipelineValidationError) -> set[str]:
    return {issue.code for issue in exc.issues}


def _write_training_artifacts(directory: Path, *, horizon: int = 1) -> pd.DataFrame:
    rows = 18
    dates = pd.date_range("2024-01-01", periods=rows, freq="7D")
    frame = pd.DataFrame(
        {
            **{
                feature: np.linspace(index + 0.1, index + 0.9, rows)
                for index, feature in enumerate(FEATURES)
            },
            "target": np.linspace(0.03, 0.12, rows),
            "__earnings_date": dates,
            "__symbol": [f"SYM{index:02d}" for index in range(rows)],
        }
    )
    directory.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(directory / f"training_T{horizon}.parquet", index=False)
    (directory / f"metadata_T{horizon}.json").write_text(
        json.dumps({"n_samples": rows, "feature_cols": FEATURES})
    )
    return frame


def _model_metadata() -> dict[str, object]:
    metadata: dict[str, object] = {
        "n_train": 12,
        "n_val": 4,
        "val_mae": 0.02,
        "baseline_straddle_mae": 0.03,
        "coverage_80": 0.80,
        "coverage_50": 0.50,
        "quantile_crossing_rate_raw": 0.05,
        "quantile_negative_rate_raw": 0.01,
        "feature_cols": FEATURES,
        "validation_split": {
            "purge_days": 1,
            "rows_total": 18,
            "rows_train": 12,
            "rows_purged": 2,
            "rows_validation": 4,
            "train_end": "2024-03-18",
            "validation_start": "2024-04-01",
        },
    }
    metadata.update(
        {
            "q10_coverage": 0.10,
            "q25_coverage": 0.25,
            "q50_coverage": 0.50,
            "q75_coverage": 0.75,
            "q90_coverage": 0.90,
        }
    )
    return metadata


def _write_model_artifacts(directory: Path, *, horizon: int = 1) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"metadata_T{horizon}.json").write_text(json.dumps(_model_metadata()))
    suffixes = [""] + [f"_q{quantile:02d}" for quantile in (10, 25, 50, 75, 90)]
    for suffix in suffixes:
        joblib.dump(
            ConstantEstimator(FEATURES),
            directory / f"lgbm_T{horizon}{suffix}.joblib",
        )


def _write_forecast_artifact(
    path: Path,
    models_dir: Path,
    *,
    now: datetime,
) -> pd.DataFrame:
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "metadata_T1.json").write_text(
        json.dumps({"feature_cols": FEATURES})
    )
    atm_iv = 0.50
    dte = 7.0
    iv_move = atm_iv * np.sqrt(dte / 365.0)
    em_math = 0.06
    em_ml = 0.07
    vector = {
        "atm_iv": atm_iv,
        "dte": dte,
        "em_iv_pct": iv_move,
        "straddle_pct": em_math,
        **{f"feature_{index}": float(index) for index in range(6)},
    }
    frame = pd.DataFrame(
        [
            {
                "act_symbol": "TEST",
                "earnings_date": "2026-08-29",
                "snapshot_date": "2026-08-22",
                "model_horizon": 1,
                "spot_price": 100.0,
                "atm_iv": atm_iv,
                "em_math_pct": em_math,
                "em_ml_pct": em_ml,
                "em_ml_abs": 7.0,
                "correction_factor": em_ml / em_math,
                "p10": 0.04,
                "p25": 0.05,
                "p50": em_ml,
                "p75": 0.09,
                "p90": 0.12,
                "scored_at": now.isoformat(),
                "feature_vector": json.dumps(vector, allow_nan=False),
            }
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return frame


def test_training_gate_accepts_consistent_artifacts(tmp_path: Path) -> None:
    training_dir = tmp_path / "training"
    _write_training_artifacts(training_dir)

    report = validate_training_artifacts(
        training_dir,
        horizons=[1],
        min_rows=10,
        min_symbols=10,
        min_history_days=30,
        purge_days=1,
    )

    assert report["status"] == "passed"
    assert report["horizons"]["1"]["train_rows"] > 0
    assert report["horizons"]["1"]["validation_rows"] > 0


def test_training_gate_reports_infinite_features_and_stale_metadata(tmp_path: Path) -> None:
    training_dir = tmp_path / "training"
    frame = _write_training_artifacts(training_dir)
    frame.loc[0, FEATURES[0]] = np.inf
    frame.to_parquet(training_dir / "training_T1.parquet", index=False)
    (training_dir / "metadata_T1.json").write_text(
        json.dumps({"n_samples": len(frame) - 1, "feature_cols": FEATURES})
    )

    with pytest.raises(PipelineValidationError) as error:
        validate_training_artifacts(
            training_dir,
            horizons=[1],
            min_rows=10,
            min_symbols=10,
            min_history_days=30,
            purge_days=1,
        )

    assert {"infinite_model_features", "feature_metadata_row_mismatch"} <= _issue_codes(
        error.value
    )


def test_model_gate_loads_every_model_and_smoke_predicts(tmp_path: Path) -> None:
    training_dir = tmp_path / "training"
    models_dir = tmp_path / "models"
    _write_training_artifacts(training_dir)
    _write_model_artifacts(models_dir)

    report = validate_model_artifacts(
        models_dir,
        horizons=[1],
        training_dir=training_dir,
        min_train_rows=10,
        min_validation_rows=4,
    )

    assert report["status"] == "passed"
    assert report["horizons"]["1"]["artifacts_loaded"] == 6


def test_model_gate_rejects_regression_and_missing_quantile(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    _write_model_artifacts(models_dir)
    metadata = _model_metadata()
    metadata["val_mae"] = 0.04
    (models_dir / "metadata_T1.json").write_text(json.dumps(metadata))
    (models_dir / "lgbm_T1_q90.joblib").unlink()

    with pytest.raises(PipelineValidationError) as error:
        validate_model_artifacts(
            models_dir,
            horizons=[1],
            min_train_rows=10,
            min_validation_rows=4,
        )

    assert {"model_fails_baseline", "missing_model_artifact"} <= _issue_codes(error.value)


def test_forecast_gate_accepts_valid_iv_and_model_handoff(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)
    forecast_path = tmp_path / "forecasts" / "forecasts_2026-08-22.parquet"
    models_dir = tmp_path / "models"
    _write_forecast_artifact(forecast_path, models_dir, now=now)

    report = validate_forecast_artifact(
        forecast_path,
        models_dir=models_dir,
        now=now,
    )

    assert report["status"] == "passed"
    assert report["rows"] == 1


def test_forecast_gate_surfaces_invisible_handoff_failures(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)
    forecast_path = tmp_path / "forecasts" / "forecasts_2026-08-22.parquet"
    models_dir = tmp_path / "models"
    frame = _write_forecast_artifact(forecast_path, models_dir, now=now)
    vector = json.loads(frame.loc[0, "feature_vector"])
    vector["em_iv_pct"] = 0.99
    vector["feature_0"] = "not-a-number"
    frame.loc[0, "feature_vector"] = json.dumps(vector)
    frame.loc[0, "p25"] = 0.15
    frame.to_parquet(forecast_path, index=False)

    with pytest.raises(PipelineValidationError) as error:
        validate_forecast_artifact(
            forecast_path,
            models_dir=models_dir,
            now=now,
        )

    assert {
        "crossed_served_quantiles",
        "iv_expected_move_mismatch",
        "invalid_feature_values",
    } <= _issue_codes(error.value)
