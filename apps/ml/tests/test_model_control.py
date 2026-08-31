from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from lightgbm import LGBMRegressor

from ml.model_artifact import save_native_model
from ml.model_bundle import create_signed_bundle
from ml.model_control import (
    append_prediction_ledger,
    compare_on_common_holdout,
    evaluate_realized_outcomes,
    feature_drift_report,
    update_outcome_history,
)


def _keys(tmp_path: Path) -> tuple[bytes, Path]:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = tmp_path / "public.pem"
    public.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_pem, public


def _bundle(
    root: Path,
    private: bytes,
    *,
    target_shift: float,
) -> Path:
    models = root / "models"
    models.mkdir(parents=True)
    X = pd.DataFrame({"feature": np.linspace(0, 1, 300), "straddle_pct": 0.08})
    y = 0.04 + X["feature"] * 0.02 + target_shift
    point = LGBMRegressor(n_estimators=30, min_child_samples=5, random_state=42, verbose=-1)
    point.fit(X, y)
    for name in ["lgbm_T1.txt", *[f"lgbm_T1_q{q:02d}.txt" for q in (10, 25, 50, 75, 90)]]:
        save_native_model(point, models / name)
    metadata = {
        "feature_cols": list(X.columns),
        "validation_split": {
            "validation_start": "2025-07-20",
            "validation_end": "2026-05-15",
        },
        "feature_reference": {
            "feature": {
                "missing_rate": 0.0,
                "cuts": np.linspace(0.1, 0.9, 9).tolist(),
                "probabilities": [0.1] * 10,
            },
            "straddle_pct": {
                "missing_rate": 0.0,
                "cuts": [0.08],
                "probabilities": [0.5, 0.5],
            },
        },
        "residual_reference": {"mean": 0.0, "std": 0.01},
    }
    (models / "metadata_T1.json").write_text(json.dumps(metadata))
    report = root / "report.json"
    report.write_text(json.dumps({"status": "passed"}))
    receipt = root / "receipt.json"
    receipt.write_text(
        json.dumps({"receipt_id": "sha256:" + "a" * 64, "quality": {"status": "passed"}})
    )
    bundle, _ = create_signed_bundle(
        models,
        root / "bundles",
        receipt_path=receipt,
        validation_report_path=report,
        source_revision=str(target_shift),
        horizons=[1],
        private_key=private,
    )
    return bundle


def test_common_holdout_blocks_a_regressing_challenger(tmp_path: Path, monkeypatch) -> None:
    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    champion = _bundle(tmp_path / "champion", private, target_shift=0.0)
    challenger = _bundle(tmp_path / "challenger", private, target_shift=0.04)
    dates = pd.date_range("2025-07-20", periods=300, freq="D")
    training = pd.DataFrame(
        {
            "feature": np.linspace(0, 1, 300),
            "straddle_pct": 0.08,
            "target": 0.04 + np.linspace(0, 1, 300) * 0.02,
            "__earnings_date": dates,
            "__symbol": [f"S{i}" for i in range(300)],
        }
    )
    training_dir = tmp_path / "training"
    training_dir.mkdir()
    training.to_parquet(training_dir / "training_T1.parquet", index=False)

    result = compare_on_common_holdout(
        challenger,
        champion,
        training_dir,
        horizons=[1],
    )

    assert result["status"] == "failed"
    assert any("regresses champion" in issue for issue in result["issues"])


def test_drift_and_realized_outcomes_drive_a_conservative_rollback(tmp_path: Path) -> None:
    forecast = pd.DataFrame(
        {
            "feature_vector": [
                json.dumps(
                    {
                        "feature": (0.2, 0.5, 0.8)[index % 3],
                        "straddle_pct": (0.06, 0.08, 0.10)[index % 3],
                    }
                )
                for index in range(60)
            ],
            "model_horizon": [1] * 60,
        }
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metadata_T1.json").write_text(
        json.dumps(
            {
                "feature_reference": {
                    "feature": {"missing_rate": 0.0, "cuts": [0.4, 0.6], "probabilities": [0.3, 0.4, 0.3]},
                    "straddle_pct": {"missing_rate": 0.0, "cuts": [0.07, 0.09], "probabilities": [0.3, 0.4, 0.3]},
                },
                "residual_reference": {"mean": 0.0, "std": 0.01},
            }
        )
    )
    drift = feature_drift_report(forecast, bundle, horizons=[1])
    assert drift["status"] in {"passed", "warning"}

    events = pd.date_range("2026-01-01", periods=30, freq="D")
    base = pd.DataFrame(
        {
            "act_symbol": [f"S{i}" for i in range(30)],
            "earnings_date": events,
            "snapshot_date": events - pd.Timedelta(days=7),
            "model_horizon": 1,
            "em_math_pct": 0.05,
            "p10": 0.02,
            "p25": 0.03,
            "p50": 0.05,
            "p75": 0.07,
            "p90": 0.09,
        }
    )
    champion_rows = base.assign(bundle_id="champion", role="champion", prediction=0.14)
    comparison_rows = base.assign(bundle_id="previous", role="previous", prediction=0.051)
    ledger_path = tmp_path / "ledger.parquet"
    ledger = append_prediction_ledger(ledger_path, [champion_rows, comparison_rows])
    training_dir = tmp_path / "training"
    training_dir.mkdir()
    pd.DataFrame(
        {
            "__symbol": [f"S{i}" for i in range(30)],
            "__earnings_date": events,
            "target": 0.05,
            "__sector": "Technology",
            "__dollar_volume": 200_000_000.0,
            "dte": 7,
            "vix_current": 20.0,
        }
    ).to_parquet(training_dir / "training_T1.parquet", index=False)

    result = evaluate_realized_outcomes(
        ledger,
        training_dir,
        bundle,
        bundle,
        champion_id="champion",
        comparison_id="previous",
        min_common_rows=30,
        horizons=[1],
    )

    assert result["rollback_recommended"] is True
    assert result["rollback_reasons"]["comparison_materially_better"] is True


def test_drift_blocks_large_missingness_shift_below_psi_sample_floor(tmp_path: Path) -> None:
    forecast = pd.DataFrame(
        {
            "feature_vector": [json.dumps({"feature": None}) for _ in range(25)],
            "model_horizon": [1] * 25,
        }
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metadata_T1.json").write_text(
        json.dumps(
            {
                "feature_reference": {
                    "feature": {
                        "missing_rate": 0.0,
                        "cuts": [0.4, 0.6],
                        "probabilities": [0.3, 0.4, 0.3],
                    }
                }
            }
        )
    )

    result = feature_drift_report(forecast, bundle, horizons=[1], min_rows=100)

    assert result["status"] == "critical"
    assert result["critical_features"] == 1
    assert result["horizons"]["1"]["features"]["feature"]["status"] == "critical"


def test_outcome_history_is_bounded_and_replaces_the_same_evaluation() -> None:
    first = update_outcome_history(
        {},
        {
            "evaluated_at": "2026-08-23T12:00:00Z",
            "status": "insufficient_data",
            "common_rows": 0,
            "minimum_common_rows": 30,
            "rolled_back": False,
        },
        limit=2,
    )
    second = update_outcome_history(
        first,
        {
            "evaluated_at": "2026-08-30T12:00:00Z",
            "status": "passed",
            "common_rows": 40,
            "minimum_common_rows": 30,
            "champion": {"mae": 0.04, "baseline_straddle_mae": 0.05},
            "rolled_back": False,
        },
        limit=2,
    )
    replaced = update_outcome_history(
        second,
        {
            "evaluated_at": "2026-08-30T12:00:00Z",
            "status": "passed",
            "common_rows": 41,
            "minimum_common_rows": 30,
            "rolled_back": False,
        },
        limit=2,
    )

    assert [item["common_rows"] for item in replaced["evaluations"]] == [41, 0]
    assert replaced["schema"] == "quantiv.model-outcome-history.v1"
