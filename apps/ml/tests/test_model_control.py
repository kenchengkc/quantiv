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
    metadata_overrides: dict | None = None,
) -> Path:
    from ml.evidence_receipt import build_evidence_receipt
    from ml.model_protocol import target_protocol
    models = root / "models"
    models.mkdir(parents=True)
    X = pd.DataFrame({"feature": np.linspace(0, 1, 300), "straddle_pct": 0.08})
    y = 0.04 + X["feature"] * 0.02 + target_shift
    point = LGBMRegressor(
        n_estimators=30, min_child_samples=5, random_state=42, verbose=-1
    )
    point.fit(X, y)
    for name in [
        "lgbm_T1.txt",
        *[f"lgbm_T1_q{q:02d}.txt" for q in (10, 25, 50, 75, 90)],
    ]:
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
    metadata.update(metadata_overrides or {})
    (models / "metadata_T1.json").write_text(json.dumps(metadata))
    training = root / "training"
    training.mkdir()
    event_date = pd.Timestamp("2024-06-03")
    X.assign(
        target=y,
        __symbol=[f"S{index}" for index in range(len(X))],
        __earnings_date=event_date,
        __snapshot_date=event_date - pd.Timedelta(days=1),
        __pre_price_date=event_date - pd.Timedelta(days=3),
        __post_price_date=event_date,
        __label_available_at=event_date,
        __target_protocol=target_protocol(metadata),
        __cohort="strict_options",
    ).to_parquet(training / "training_T1.parquet", index=False)
    (training / "metadata_T1.json").write_text(json.dumps(metadata))
    report = root / "report.json"
    payload = {"status": "passed", "stages": {"models": {"horizons": [1]}}}
    receipt = root / "receipt.json"
    payload["evidence_receipt"] = build_evidence_receipt(payload, scope="models", repo_root=root,
        data_dir=root, training_dir=root / "training", models_dir=models, forecast_path=None, horizons=[1])
    report.write_text(json.dumps(payload))
    receipt.write_text(json.dumps(payload["evidence_receipt"]))
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


def test_common_holdout_blocks_a_regressing_challenger(
    tmp_path: Path, monkeypatch
) -> None:
    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    champion = _bundle(tmp_path / "champion", private, target_shift=0.0)
    challenger = _bundle(tmp_path / "challenger", private, target_shift=0.04)
    dates = pd.date_range("2026-05-16", periods=300, freq="D")
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


def test_drift_and_realized_outcomes_drive_a_conservative_rollback(
    tmp_path: Path,
) -> None:
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
                    "feature": {
                        "missing_rate": 0.0,
                        "cuts": [0.4, 0.6],
                        "probabilities": [0.3, 0.4, 0.3],
                    },
                    "straddle_pct": {
                        "missing_rate": 0.0,
                        "cuts": [0.07, 0.09],
                        "probabilities": [0.3, 0.4, 0.3],
                    },
                },
                "residual_reference": {"mean": 0.0, "std": 0.01},
            }
        )
    )
    drift = feature_drift_report(forecast, bundle, horizons=[1])
    assert drift["status"] == "insufficient_data"

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
    comparison_rows = base.assign(
        bundle_id="previous", role="previous", prediction=0.051
    )
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


def test_realized_outcomes_keep_optionless_ml_rows_out_of_straddle_comparison(
    tmp_path: Path,
) -> None:
    events = pd.date_range("2026-02-01", periods=30, freq="D")
    baselines = np.array([np.nan] * 10 + [0.05] * 20)
    common = pd.DataFrame(
        {
            "act_symbol": [f"O{i}" for i in range(30)],
            "earnings_date": events,
            "snapshot_date": events - pd.Timedelta(days=7),
            "model_horizon": 1,
            "em_math_pct": baselines,
            "p10": 0.02,
            "p25": 0.03,
            "p50": 0.05,
            "p75": 0.07,
            "p90": 0.09,
        }
    )
    champion_prediction = np.array([0.30] * 10 + [0.06] * 20)
    champion_rows = common.assign(
        bundle_id="champion",
        role="champion",
        prediction=champion_prediction,
    )
    comparison_rows = common.assign(
        bundle_id="previous",
        role="previous",
        prediction=0.055,
    )
    ledger_path = tmp_path / "ledger.parquet"
    ledger = append_prediction_ledger(ledger_path, [champion_rows, comparison_rows])

    training_dir = tmp_path / "training"
    training_dir.mkdir()
    pd.DataFrame(
        {
            "__symbol": [f"O{i}" for i in range(30)],
            "__earnings_date": events,
            "target": 0.05,
            "__sector": "Technology",
            "__dollar_volume": 200_000_000.0,
            "dte": 7,
            "vix_current": 20.0,
        }
    ).to_parquet(training_dir / "training_T1.parquet", index=False)

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metadata_T1.json").write_text(
        json.dumps({"residual_reference": {"mean": 0.0, "std": 1.0}})
    )

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

    champion = result["champion"]
    assert result["status"] == "passed"
    assert champion["rows"] == 30
    assert champion["baseline_rows"] == 20
    assert champion["optionless_rows"] == 10
    assert np.isclose(champion["mae"], 0.09)
    assert np.isclose(champion["baseline_comparable_model_mae"], 0.01)
    assert np.isclose(champion["baseline_straddle_mae"], 0.0)


def test_realized_outcomes_allow_fully_optionless_common_rows(tmp_path: Path) -> None:
    events = pd.date_range("2026-03-01", periods=30, freq="D")
    common = pd.DataFrame(
        {
            "act_symbol": [f"N{i}" for i in range(30)],
            "earnings_date": events,
            "snapshot_date": events - pd.Timedelta(days=7),
            "model_horizon": 1,
            "em_math_pct": np.nan,
            "p10": 0.02,
            "p25": 0.03,
            "p50": 0.05,
            "p75": 0.07,
            "p90": 0.09,
        }
    )
    champion_rows = common.assign(
        bundle_id="champion", role="champion", prediction=0.08
    )
    comparison_rows = common.assign(
        bundle_id="previous", role="previous", prediction=0.051
    )
    ledger_path = tmp_path / "ledger.parquet"
    ledger = append_prediction_ledger(ledger_path, [champion_rows, comparison_rows])

    training_dir = tmp_path / "training"
    training_dir.mkdir()
    pd.DataFrame(
        {
            "__symbol": [f"N{i}" for i in range(30)],
            "__earnings_date": events,
            "target": 0.05,
            "__sector": "Technology",
            "__dollar_volume": 200_000_000.0,
            "dte": 7,
            "vix_current": 20.0,
        }
    ).to_parquet(training_dir / "training_T1.parquet", index=False)

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metadata_T1.json").write_text(
        json.dumps({"residual_reference": {"mean": 0.0, "std": 1.0}})
    )

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

    champion = result["champion"]
    assert result["status"] == "passed"
    assert champion["baseline_rows"] == 0
    assert champion["optionless_rows"] == 30
    assert champion["baseline_comparable_model_mae"] is None
    assert champion["baseline_straddle_mae"] is None
    assert result["rollback_reasons"]["champion_worse_than_market"] is False
    assert result["rollback_recommended"] is False


def test_drift_blocks_large_missingness_shift_below_psi_sample_floor(
    tmp_path: Path,
) -> None:
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


def test_common_holdout_excludes_both_models_selection_exposure(tmp_path, monkeypatch):
    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    candidate = _bundle(
        tmp_path / "candidate",
        private,
        target_shift=0.0,
        metadata_overrides={
            "selection_exposure": {"through_date": "2026-07-01"},
            "final_fit": {"end": "2026-06-20", "label_available_end": "2026-06-21"},
        },
    )
    champion = _bundle(
        tmp_path / "incumbent",
        private,
        target_shift=0.0,
        metadata_overrides={"selection_exposure": {"through_date": "2026-07-05"}},
    )
    training_dir = tmp_path / "training"
    training_dir.mkdir()
    pd.DataFrame(
        {
            "feature": np.linspace(0, 1, 300),
            "straddle_pct": 0.08,
            "target": 0.05,
            "__symbol": [f"S{i}" for i in range(300)],
            "__earnings_date": pd.to_datetime(
                ["2026-07-03"] * 150 + ["2026-07-10"] * 150
            ),
            "__snapshot_date": pd.to_datetime(
                ["2026-07-02"] * 150 + ["2026-07-09"] * 150
            ),
        }
    ).to_parquet(training_dir / "training_T1.parquet", index=False)
    report = compare_on_common_holdout(candidate, champion, training_dir, horizons=[1])
    assert report["status"] == "insufficient_data"
    assert report["horizons"]["1"]["rows"] == 150
    assert report["horizons"]["1"]["excluded_exposed_rows"] == 150


def test_psi_seasonality_is_diagnostic_and_missing_feature_corruption_holds(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metadata_T1.json").write_text(
        json.dumps(
            {
                "feature_reference": {
                    "earnings_month": {
                        "missing_rate": 0.0,
                        "cuts": [3.0, 6.0, 9.0],
                        "probabilities": [0.25] * 4,
                    },
                    "feature": {
                        "missing_rate": 0.0,
                        "cuts": [0.5],
                        "probabilities": [0.5, 0.5],
                    },
                }
            }
        )
    )
    healthy = pd.DataFrame(
        {
            "model_horizon": [1] * 200,
            "feature_vector": [
                json.dumps({"earnings_month": 10, "feature": i % 2}) for i in range(200)
            ],
        }
    )
    result = feature_drift_report(healthy, bundle, horizons=[1])
    assert result["status"] == "warning"
    assert result["corruption_status"] == "passed"
    broken = healthy.assign(
        feature_vector=json.dumps({"earnings_month": 10, "feature": None})
    )
    assert feature_drift_report(broken, bundle, horizons=[1])["status"] == "critical"


def test_low_sample_drift_does_not_claim_health(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metadata_T1.json").write_text(
        json.dumps(
            {
                "feature_reference": {
                    "feature": {
                        "missing_rate": 0.0,
                        "cuts": [0.5],
                        "probabilities": [0.5, 0.5],
                    }
                }
            }
        )
    )
    frame = pd.DataFrame(
        {"model_horizon": [1] * 5, "feature_vector": [json.dumps({"feature": 0.5})] * 5}
    )
    assert (
        feature_drift_report(frame, bundle, horizons=[1])["status"]
        == "insufficient_data"
    )


def test_drift_reports_unsupported_optionless_cohort_instead_of_missingness(tmp_path):
    import ml.model_control as control

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metadata_T1.json").write_text(
        json.dumps(
            {
                "feature_reference": {
                    "straddle_pct": {
                        "missing_rate": 0.0,
                        "cuts": [0.1],
                        "probabilities": [0.5, 0.5],
                    }
                }
            }
        )
    )
    frame = pd.DataFrame(
        {
            "model_horizon": [1] * 30,
            "em_math_pct": [np.nan] * 30,
            "feature_vector": [json.dumps({"straddle_pct": None})] * 30,
        }
    )
    report = control.cohort_drift_report(frame, bundle, horizons=[1])
    assert report["status"] == "unsupported_cohort"
    assert report["critical_features"] == 0
    assert report["unsupported_rows"] == 30


def test_prospective_evidence_excludes_backfilled_scores_and_matches_snapshot_dates(
    tmp_path,
):
    import ml.model_control as control

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metadata_T1.json").write_text(
        json.dumps(
            {
                "selection_exposure": {"through_date": "2026-06-01"},
                "feature_protocol": "quantiv.earnings-causal.v2",
                "target_protocol": "quantiv.session-reaction.v2",
            }
        )
    )
    rows = pd.DataFrame(
        {
            "act_symbol": ["A", "B"],
            "earnings_date": pd.to_datetime(["2026-07-02"] * 2),
            "snapshot_date": pd.to_datetime(["2026-07-01"] * 2),
            "model_horizon": [1] * 2,
            "recorded_at": pd.to_datetime(
                ["2026-07-01T20:00:00Z", "2026-07-04T20:00:00Z"]
            ),
            "prediction": [0.05] * 2,
            "em_math_pct": [0.08] * 2,
            **{
                f"p{q:02d}": [0.01 if q < 50 else 0.09] * 2
                for q in (10, 25, 50, 75, 90)
            },
        }
    )
    ledger = pd.concat(
        [
            rows.assign(
                bundle_id="champion", feature_protocol="quantiv.earnings-causal.v2"
            ),
            rows.assign(
                bundle_id="candidate", feature_protocol="quantiv.earnings-causal.v2"
            ),
        ],
        ignore_index=True,
    )
    labels = pd.DataFrame(
        {
            "__symbol": ["A", "B"],
            "__earnings_date": pd.to_datetime(["2026-07-02"] * 2),
            "__label_available_date": pd.to_datetime(["2026-07-03"] * 2),
            "target": [0.05] * 2,
        }
    )
    report = control.compare_prospective_outcomes(
        ledger,
        labels,
        bundle,
        bundle,
        champion_id="champion",
        candidate_id="candidate",
        horizon=1,
    )
    assert report["rows"] == 1
    assert report["status"] == "insufficient_data"
    assert report["evaluation_target_protocol"] == "quantiv.session-reaction.v2"


def test_shadow_scoring_rejects_vectors_built_under_another_protocol(
    tmp_path, monkeypatch
):
    import pytest
    import ml.model_control as control

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    champion = _bundle(tmp_path / "legacy", private, target_shift=0.0)
    frame = pd.DataFrame(
        {
            "model_horizon": [1],
            "em_ml_pct": [0.05],
            "feature_protocol": ["quantiv.earnings-causal.v2"],
            "feature_vector": [json.dumps({"feature": 0.4, "straddle_pct": 0.08})],
        }
    )
    with pytest.raises(ValueError, match="protocol"):
        control.shadow_score_report(frame, champion)


def test_prediction_ledger_preserves_original_prospective_prediction(tmp_path):
    rows = pd.DataFrame(
        {
            "bundle_id": ["candidate"],
            "act_symbol": ["A"],
            "earnings_date": pd.to_datetime(["2026-10-10"]),
            "snapshot_date": pd.to_datetime(["2026-10-09"]),
            "model_horizon": [1],
            "prediction": [0.05],
        }
    )
    path = tmp_path / "ledger.parquet"
    append_prediction_ledger(path, [rows])
    result = append_prediction_ledger(path, [rows.assign(prediction=0.09)])
    assert result.iloc[0]["prediction"] == 0.05


def test_prospective_matched_unseen_predictions_preserve_regression_gate(
    tmp_path, monkeypatch
):
    import ml.model_control as control

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    bundle = _bundle(
        tmp_path / "bundle",
        private,
        target_shift=0.0,
        metadata_overrides={"selection_exposure": {"through_date": "2026-06-01"}},
    )
    actual = np.linspace(0.001, 0.10, 200)
    rows = pd.DataFrame(
        {
            "act_symbol": [f"S{i}" for i in range(200)],
            "earnings_date": pd.Timestamp("2026-07-02"),
            "snapshot_date": pd.Timestamp("2026-07-01"),
            "model_horizon": 1,
            "recorded_at": "2026-07-01T20:00:00Z",
            "feature_protocol": "quantiv.earnings-legacy.v1",
            "prediction": actual,
            "em_math_pct": 0.2,
            **{f"p{q:02d}": q / 1000 for q in (10, 25, 50, 75, 90)},
        }
    )
    labels = pd.DataFrame(
        {
            "__symbol": rows.act_symbol,
            "__earnings_date": rows.earnings_date,
            "__label_available_at": pd.Timestamp("2026-07-03"),
            "target": actual,
        }
    )
    champion = rows.assign(bundle_id="champion", prediction=actual + 0.001)
    candidate = rows.assign(bundle_id="candidate")
    ledger = pd.concat([champion, candidate], ignore_index=True)
    good = control.compare_prospective_outcomes(
        ledger,
        labels,
        bundle,
        bundle,
        champion_id="champion",
        candidate_id="candidate",
        horizon=1,
    )
    assert good["status"] == "passed"
    assert good["rows"] == 200
    ledger.loc[ledger.bundle_id == "candidate", "prediction"] += 0.05
    bad = control.compare_prospective_outcomes(
        ledger,
        labels,
        bundle,
        bundle,
        champion_id="champion",
        candidate_id="candidate",
        horizon=1,
    )
    assert bad["status"] == "failed"
    assert any("regresses champion" in issue for issue in bad["issues"])


def test_realized_comparison_requires_matching_forecast_snapshot(tmp_path):
    dates = pd.date_range("2026-07-01", periods=30)
    common = pd.DataFrame(
        {
            "act_symbol": [f"S{i}" for i in range(30)],
            "earnings_date": dates,
            "snapshot_date": dates - pd.Timedelta(days=1),
            "model_horizon": 1,
            "prediction": 0.05,
            "em_math_pct": 0.08,
            **{f"p{q:02d}": q / 1000 for q in (10, 25, 50, 75, 90)},
        }
    )
    ledger = pd.concat(
        [
            common.assign(bundle_id="champion"),
            common.assign(
                bundle_id="other", snapshot_date=dates - pd.Timedelta(days=2)
            ),
        ]
    )
    training = tmp_path / "training"
    training.mkdir()
    pd.DataFrame(
        {
            "__symbol": common.act_symbol,
            "__earnings_date": dates,
            "target": 0.05,
            "dte": 7,
            "vix_current": 20,
            "__dollar_volume": 200_000_000,
        }
    ).to_parquet(training / "training_T1.parquet")
    result = evaluate_realized_outcomes(
        ledger,
        training,
        tmp_path,
        tmp_path,
        champion_id="champion",
        comparison_id="other",
        horizons=[1],
    )
    assert result["status"] == "insufficient_data"
    assert result["common_rows"] == 0


def test_prospective_rejects_old_snapshot_scored_before_future_event(tmp_path):
    import ml.model_control as control

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metadata_T21.json").write_text(
        json.dumps({"selection_exposure": {"through_date": "2026-06-01"}})
    )
    rows = pd.DataFrame(
        {
            "act_symbol": ["A"],
            "earnings_date": [pd.Timestamp("2026-07-22")],
            "snapshot_date": [pd.Timestamp("2026-07-01")],
            "model_horizon": [21],
            "recorded_at": ["2026-07-10T21:00:00Z"],
            "scored_at": ["2026-07-10T21:00:00Z"],
            "feature_protocol": ["quantiv.earnings-legacy.v1"],
            "prediction": [0.05],
            "em_math_pct": [0.08],
            **{f"p{q:02d}": [q / 1000] for q in (10, 25, 50, 75, 90)},
        }
    )
    labels = pd.DataFrame(
        {
            "__symbol": ["A"],
            "__earnings_date": [pd.Timestamp("2026-07-22")],
            "__label_available_at": [pd.Timestamp("2026-07-23")],
            "target": [0.05],
        }
    )
    ledger = pd.concat(
        [rows.assign(bundle_id="candidate"), rows.assign(bundle_id="champion")]
    )
    result = control.compare_prospective_outcomes(
        ledger,
        labels,
        bundle,
        bundle,
        champion_id="champion",
        candidate_id="candidate",
        horizon=21,
    )
    assert result["rows"] == 0


def test_common_holdout_requires_evidence_for_every_supported_cohort(
    tmp_path, monkeypatch
):
    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    bundle = _bundle(
        tmp_path / "bundle",
        private,
        target_shift=0.0,
        metadata_overrides={"supported_cohorts": ["strict_options", "optionless"]},
    )
    training = tmp_path / "training"
    training.mkdir()
    pd.DataFrame(
        {
            "feature": np.linspace(0, 1, 420),
            "straddle_pct": [0.2] * 400 + [np.nan] * 20,
            "hist_move_med_4q": 0.2,
            "target": 0.05,
            "__cohort": ["strict_options"] * 400 + ["optionless"] * 20,
            "__earnings_date": pd.Timestamp("2026-07-02"),
            "__snapshot_date": pd.Timestamp("2026-07-01"),
        }
    ).to_parquet(training / "training_T1.parquet")
    result = compare_on_common_holdout(bundle, bundle, training, horizons=[1])
    assert result["status"] == "insufficient_data"
    assert result["horizons"]["1"]["cohorts"]["optionless"]["rows"] == 20


def test_optionless_paired_evidence_requires_matched_historical_baseline():
    import ml.model_control as control

    actual = np.repeat(0.05, 200)
    quantiles = np.tile([0.01, 0.03, 0.05, 0.07, 0.09], (200, 1))
    result = control._paired_metrics(
        actual,
        actual,
        actual + 0.001,
        quantiles,
        quantiles,
        np.repeat(np.nan, 200),
        baseline_name="historical_median",
    )
    assert result["status"] == "insufficient_data"
    assert result["baseline_rows"] == 0


def test_legacy_selection_exposure_includes_conservative_label_availability():
    from ml.model_control import selection_exposure_end

    end = selection_exposure_end({"validation_split": {"validation_end": "2026-06-01"}})
    assert end == pd.Timestamp("2026-06-06")


def test_monitoring_ledger_retains_cohort_and_asof_historical_baseline(
    tmp_path, monkeypatch
):
    from datetime import datetime, timezone
    from market_sessions import latest_completed_us_market_session
    from ml.model_control import monitoring_rows

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    bundle = _bundle(tmp_path / "bundle", private, target_shift=0.0)
    forecast = pd.DataFrame(
        {
            "act_symbol": ["A"],
            "earnings_date": [pd.Timestamp("2026-11-01")],
            "snapshot_date": [
                latest_completed_us_market_session(datetime.now(timezone.utc))
            ],
            "model_horizon": [1],
            "em_math_pct": [np.nan],
            "em_ml_pct": [0.05],
            "__cohort": ["optionless"],
            "scored_at": [datetime.now(timezone.utc).isoformat()],
            "feature_vector": [
                json.dumps(
                    {"feature": 0.5, "straddle_pct": None, "hist_move_med_4q": 0.08}
                )
            ],
            **{f"p{q:02d}": [q / 1000] for q in (10, 25, 50, 75, 90)},
        }
    )
    rows = monitoring_rows(
        forecast,
        bundle,
        bundle_id="candidate",
        role="challenger",
        use_served_predictions=True,
    )
    assert rows.iloc[0]["__cohort"] == "optionless"
    assert rows.iloc[0]["hist_move_med_4q"] == 0.08


def test_monitoring_does_not_invent_a_score_timestamp_for_unrecorded_vectors(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from market_sessions import latest_completed_us_market_session
    from ml.model_control import monitoring_rows

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    bundle = _bundle(tmp_path / "bundle", private, target_shift=0.)
    rows = pd.DataFrame({
        "act_symbol": ["A"], "earnings_date": [pd.Timestamp("2026-11-01")],
        "snapshot_date": [latest_completed_us_market_session(datetime.now(timezone.utc))],
        "model_horizon": [1], "em_math_pct": [.08], "em_ml_pct": [.05],
        "feature_vector": [json.dumps({"feature": .5, "straddle_pct": .08})],
        **{f"p{q:02d}": [q / 1000] for q in (10, 25, 50, 75, 90)},
    })
    assert monitoring_rows(rows, bundle, bundle_id="candidate", role="challenger", use_served_predictions=True).empty


def test_common_holdout_rejects_training_vectors_from_another_protocol(
    tmp_path, monkeypatch
):
    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    bundle = _bundle(
        tmp_path / "bundle",
        private,
        target_shift=0.0,
        metadata_overrides={
            "feature_protocol": "quantiv.earnings-causal.v2",
            "target_protocol": "quantiv.session-reaction.v2",
        },
    )
    training = tmp_path / "training"
    training.mkdir()
    pd.DataFrame(
        {
            "feature": np.linspace(0, 1, 200),
            "straddle_pct": 0.2,
            "target": 0.05,
            "__snapshot_date": pd.Timestamp("2026-07-01"),
            "__earnings_date": pd.Timestamp("2026-07-02"),
        }
    ).to_parquet(training / "training_T1.parquet")
    (training / "metadata_T1.json").write_text(
        json.dumps(
            {
                "feature_protocol": "quantiv.earnings-legacy.v1",
                "target_protocol": "quantiv.generic-reaction.v1",
            }
        )
    )
    result = compare_on_common_holdout(bundle, bundle, training, horizons=[1])
    assert result["status"] == "insufficient_data"
    assert any("protocol" in issue for issue in result["issues"])


def test_prospective_uses_existing_eastern_event_cutoff_not_utc_midnight(tmp_path):
    import ml.model_control as control

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metadata_T1.json").write_text(
        json.dumps({"selection_exposure": {"through_date": "2026-06-01"}})
    )
    rows = pd.DataFrame(
        {
            "act_symbol": ["BEFORE", "AFTER"],
            "earnings_date": pd.Timestamp("2026-07-02"),
            "snapshot_date": pd.Timestamp("2026-07-01"),
            "model_horizon": 1,
            "timing": "bmo",
            "recorded_at": ["2026-07-02T02:00:00Z", "2026-07-02T06:00:00Z"],
            "feature_protocol": "quantiv.earnings-legacy.v1",
            "prediction": 0.05,
            "em_math_pct": 0.08,
            **{f"p{q:02d}": q / 1000 for q in (10, 25, 50, 75, 90)},
        }
    )
    ledger = pd.concat(
        [rows.assign(bundle_id="candidate"), rows.assign(bundle_id="champion")]
    )
    labels = pd.DataFrame(
        {
            "__symbol": rows.act_symbol,
            "__earnings_date": rows.earnings_date,
            "__label_available_at": pd.Timestamp("2026-07-03"),
            "target": 0.05,
        }
    )
    result = control.compare_prospective_outcomes(
        ledger,
        labels,
        bundle,
        bundle,
        champion_id="champion",
        candidate_id="candidate",
        horizon=1,
    )
    assert result["rows"] == 1


def test_empty_eligible_prediction_ledger_can_still_be_signed(tmp_path):
    empty = pd.DataFrame(
        columns=[
            "bundle_id",
            "act_symbol",
            "earnings_date",
            "snapshot_date",
            "model_horizon",
        ]
    )
    path = tmp_path / "ledger.parquet"
    result = append_prediction_ledger(path, [empty])
    assert result.empty
    assert path.is_file()


def test_drift_does_not_hide_a_sparse_supported_cohort(tmp_path):
    from ml.model_control import cohort_drift_report

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    reference = {
        "feature": {"missing_rate": 0.0, "cuts": [0.5], "probabilities": [0.5, 0.5]}
    }
    (bundle / "metadata_T1.json").write_text(
        json.dumps(
            {
                "supported_cohorts": ["strict_options", "optionless"],
                "cohort_reference": {
                    name: {"rows": 200, "feature_reference": reference}
                    for name in ("strict_options", "optionless")
                },
            }
        )
    )
    rows = pd.DataFrame(
        {
            "model_horizon": 1,
            "em_math_pct": [0.08] * 200 + [np.nan] * 5,
            "__cohort": ["strict_options"] * 200 + ["optionless"] * 5,
            "feature_vector": [
                json.dumps({"feature": index % 2}) for index in range(205)
            ],
        }
    )
    report = cohort_drift_report(rows, bundle, horizons=[1])
    assert report["status"] == "insufficient_data"


def test_drift_without_reference_does_not_claim_health(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metadata_T1.json").write_text("{}")
    rows = pd.DataFrame({"model_horizon": [1] * 200, "feature_vector": ["{}"] * 200})
    assert (
        feature_drift_report(rows, bundle, horizons=[1])["status"]
        == "insufficient_data"
    )


def test_legacy_exposure_also_bounds_unrecorded_selection_by_training_time():
    from ml.model_control import selection_exposure_end

    assert selection_exposure_end(
        {
            "trained_at": "2026-07-01T10:00:00Z",
            "validation_split": {"validation_end": "2026-06-01"},
        }
    ) == pd.Timestamp("2026-07-01T10:00:00")
