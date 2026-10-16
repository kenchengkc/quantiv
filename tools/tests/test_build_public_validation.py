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
            "coverage": {"rows": 12, "events": 7, "horizons": list(HORIZONS)},
            "controls": {"exceptions": 0},
            "artifact_bundles": [
                {"name": "model_bundle", "sha256": model_sha}
            ],
        },
    )
    _write_json(
        repo / "apps/frontend/public/control-plane.json",
        {
            "status": "advisory",
            "publication_eligible": True,
            "data": {"status": "advisory"},
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
    forecast_horizons: tuple[int, ...] = HORIZONS,
    horizons: tuple[int, ...] = HORIZONS,
) -> tuple[str, dict, bytes]:
    models = repo / "data/models"
    training = repo / "data/ml_training"
    models.mkdir(parents=True, exist_ok=True)
    training.mkdir(parents=True, exist_ok=True)

    metadata_by_name = {
        f"metadata_T{horizon}.json": _metadata(
            horizon, model_mae=model_mae, baseline_mae=baseline_mae
        )
        for horizon in horizons
    }
    for name in required_artifact_names(horizons):
        path = models / name
        if name in metadata_by_name:
            _write_json(path, metadata_by_name[name])
        else:
            path.write_bytes(f"artifact:{name}".encode())
    for horizon in horizons:
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
                "horizons": {str(horizon): {} for horizon in horizons},
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
        horizons=horizons,
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
        horizons=horizons,
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

    # Forecast validation reads models from the content-addressed bundle path,
    # not data/models directly. The evidence bundle digest therefore differs
    # even though every authenticated model byte is identical.
    forecast_path = repo / "data/forecasts/forecasts_2026-01-02.parquet"
    forecast_path.parent.mkdir(parents=True, exist_ok=True)
    forecast_path.write_bytes(b"forecast")
    forecast_report = {
        "status": "passed",
        "issues": [],
        "stages": {
            "forecasts": {
                "status": "passed",
                "horizons": list(forecast_horizons),
            }
        },
    }
    forecast_receipt = build_evidence_receipt(
        forecast_report,
        scope="forecasts",
        repo_root=repo,
        data_dir=repo / "data",
        training_dir=training,
        models_dir=bundle_dir,
        forecast_path=forecast_path,
        horizons=horizons,
    )
    forecast_model_sha = _receipt_artifact(forecast_receipt, "model_bundle")["sha256"]
    assert forecast_model_sha != _receipt_artifact(model_receipt, "model_bundle")["sha256"]
    _write_common_public_evidence(repo, model_sha=forecast_model_sha)
    public_path = repo / "apps/frontend/public/evidence/forecast.json"
    public = json.loads(public_path.read_text())
    public["coverage"]["horizons"] = list(forecast_horizons)
    _write_json(public_path, public)
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
    forecast = json.loads(
        (tmp_path / "apps/frontend/public/evidence/forecast.json").read_text()
    )
    forecast_model_sha = forecast["artifact_bundles"][0]["sha256"]

    assert payload["model_source"]["kind"] == "signed_champion"
    assert payload["model_source"]["bundle_id"] == champion
    assert payload["model_source"]["verification_status"] == "verified"
    assert payload["model_source"]["source_revision"] == "source-revision-test"
    assert payload["model_source"]["model_validation_receipt_id"] == model_receipt["receipt_id"]
    assert payload["model_source"]["artifact_sha256"] == forecast_model_sha
    assert payload["model_source"]["artifact_sha256"] != _receipt_artifact(
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


@pytest.mark.parametrize("horizons", [HORIZONS, (3, 7, 14, 21)])
def test_signed_champion_rejects_passed_forecast_from_other_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, horizons
) -> None:
    _install_signed_champion(tmp_path, monkeypatch, forecast_horizons=horizons)
    path = tmp_path / "apps/frontend/public/evidence/forecast.json"
    forecast = json.loads(path.read_text())
    forecast["artifact_bundles"][0]["sha256"] = "other-model-digest"
    _write_json(path, forecast)

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


@pytest.mark.parametrize("horizons", [(3, 7, 14, 21), (1,), (21, 3)])
def test_signed_champion_accepts_forecast_horizon_subset(tmp_path, monkeypatch, horizons):
    _install_signed_champion(tmp_path, monkeypatch, forecast_horizons=horizons)
    payload = build_validation(tmp_path)
    assert payload["current_evidence"]["forecast_model_matches_evaluation"] is True
    assert payload["summary"]["supported_horizons"] == list(HORIZONS)


@pytest.mark.parametrize("horizons", [None, [], [999], [True], [3, 3], ["3"], "3"])
def test_signed_champion_rejects_invalid_forecast_horizons(tmp_path, monkeypatch, horizons):
    _install_signed_champion(tmp_path, monkeypatch)
    path = tmp_path / "apps/frontend/public/evidence/forecast.json"
    forecast = json.loads(path.read_text())
    forecast["coverage"]["horizons"] = horizons
    _write_json(path, forecast)
    with pytest.raises(ModelBundleError, match="horizons"):
        build_validation(tmp_path)


def test_signed_champion_rejects_subset_digest_for_different_horizons(tmp_path, monkeypatch):
    _install_signed_champion(tmp_path, monkeypatch, forecast_horizons=(3, 7, 14, 21))
    path = tmp_path / "apps/frontend/public/evidence/forecast.json"
    forecast = json.loads(path.read_text())
    forecast["coverage"]["horizons"] = [1, 2, 3, 7, 14, 21]
    _write_json(path, forecast)
    with pytest.raises(ModelBundleError, match="forecast evidence"):
        build_validation(tmp_path)


def test_public_validation_reports_every_signed_daily_head(tmp_path, monkeypatch):
    _install_signed_champion(tmp_path, monkeypatch, horizons=(4, 25), forecast_horizons=(25,))
    result = build_validation(tmp_path)
    assert result["summary"]["supported_horizons"] == [4, 25]
    assert [row["horizon_days"] for row in result["horizons"]] == [4, 25]
    assert set(result["evaluation_evidence"]["receipt"]["metadata_sha256"]) == {"4", "25"}
    assert result["current_evidence"]["forecast_model_matches_evaluation"] is True


@pytest.fixture
def historical_champion(tmp_path, monkeypatch):
    import pandas as pd
    from apps.ml.tests.test_historical_admission import _candidate
    import ml.historical_admission as engine
    from ml.model_bundle import create_historically_admitted_bundle

    prepare = engine.prepare_historical_admission

    def prepare_cohorts(**kwargs):
        path = kwargs['source_training_dir'] / 'training_T7.parquet'
        strict = pd.read_parquet(path)
        optionless = strict.copy()
        optionless['__symbol'] = 'O' + optionless['__symbol']
        optionless['straddle_pct'] = float('nan')
        optionless['target'] += .001
        # Calibration uses all rows; the benchmark comparison has fewer matches.
        optionless.loc[optionless.index % 20 == 0, 'hist_move_med_4q'] = float('nan')
        pd.concat([strict, optionless], ignore_index=True).to_parquet(path, index=False)
        return prepare(**kwargs)

    monkeypatch.setattr(engine, 'prepare_historical_admission', prepare_cohorts)

    def install(name='historical', selection_mae=.9):
        repo = tmp_path / name
        repo.mkdir()
        metadata = _metadata(7, model_mae=selection_mae, baseline_mae=1.)
        metadata.pop('horizon')
        metadata.pop('feature_cols')
        source, reservation, receipt, private, public = _candidate(
            repo, monkeypatch, horizons=(7, 14), metadata_overrides=metadata)
        _, historical = engine.evaluate_historical_admission(source, reservation, receipt,
            repo / 'dev', repo / 'evidence', private_key=private, public_key=public,
            reservation_registry=repo / 'registry')
        models = repo / 'data/models'
        serving, manifest = create_historically_admitted_bundle(source, models / 'bundles',
            admission_receipt=historical, source_revision='historical-test', private_key=private)
        _write_json(models / 'candidates' / source.name / 'model_validation_receipt.json', receipt)
        _write_json(models / 'control/champion.json', create_signed_control_pointer(
            bundle_id=serving.name, previous_bundle_id=None, decision={'action': 'promote'}, private_key=private))
        return repo, manifest, historical, receipt

    return install


def test_historical_projection_uses_signed_admitted_cells_and_correct_denominators(historical_champion, monkeypatch):
    repo, manifest, historical, receipt = historical_champion()
    assert receipt['horizons'] == [7, 14]
    assert manifest['horizons'] == [7]
    assert manifest['serving_capabilities'] == {'7': ['optionless', 'strict_options']}
    payload = build_validation(repo)
    rows = {row['cohort']: row for row in payload['horizons']}
    assert set(rows) == {'optionless', 'strict_options'}
    assert payload['summary']['supported_horizons'] == [7]
    assert payload['summary']['metric_rows'] == 2
    for cohort, row in rows.items():
        cell = historical['horizons']['7']['cohorts'][cohort]
        assert row['metric_scope'] == 'historical_out_of_sample'
        assert row['model_mae'] == cell['model_mae']
        assert row['baseline_mae'] == row['straddle_baseline_mae'] == cell['baseline_mae']
        assert row['n_validation'] == 220
        assert row['n_matched_baseline'] == (209 if cohort == 'optionless' else 220)
        assert row['coverage']['p10'] == cell['coverage']['q10_coverage']
        assert row['model_rmse'] is None
    expected_mae = (rows['optionless']['model_mae'] * 209 + rows['strict_options']['model_mae'] * 220) / 429
    assert payload['summary']['weighted_model_mae'] == pytest.approx(expected_mae)
    assert payload['summary']['weighted_baseline_mae'] == pytest.approx(
        (rows['optionless']['baseline_mae'] * 209 + rows['strict_options']['baseline_mae'] * 220) / 429)
    assert payload['summary']['matched_baseline_row_observations'] == 429
    assert payload['summary']['validation_row_observations'] == 440
    assert rows['optionless']['coverage']['p10'] != rows['strict_options']['coverage']['p10']
    assert payload['summary']['weighted_coverage']['p10'] == pytest.approx(
        (rows['optionless']['coverage']['p10'] + rows['strict_options']['coverage']['p10']) / 2)
    assert rows['optionless']['baseline_label'] == 'Historical move'
    assert rows['strict_options']['baseline_label'] == 'Market straddle'
    protocol = payload['validation_protocol']
    assert protocol['evaluation_scope'] == 'historical_out_of_sample'
    assert protocol['walk_forward']['evaluation_scope'] == 'development'
    assert protocol['historical_admission']['admission_id'] == historical['admission_id']
    evaluation = payload['evaluation_evidence']['receipt']
    assert evaluation['source_bundle_id'] == historical['bundle_id']
    assert evaluation['historical_admission_id'] == historical['admission_id']
    assert evaluation['serving_capabilities'] == manifest['serving_capabilities']
    assert evaluation['metrics'] == payload['horizons']
    import tools.validate_public_contracts as contracts
    public = repo / 'apps/frontend/public'
    _write_json(public / 'evidence/model-validation.json', payload)
    monkeypatch.setattr(contracts, 'PUBLIC', public)
    contracts.validate_model_validation()


def test_selection_metrics_do_not_become_historical_headline_metrics(historical_champion):
    first, *_ = historical_champion('first', selection_mae=.9)
    before = build_validation(first)
    second, *_ = historical_champion('second', selection_mae=.001)
    after = build_validation(second)
    assert before['summary'] == after['summary']
    assert before['horizons'] == after['horizons']
    assert before['validation_protocol']['walk_forward'] != after['validation_protocol']['walk_forward']


@pytest.mark.parametrize('mutation', ['metric', 'receipt_member', 'receipt_horizon'])
def test_historical_public_projection_rejects_tampered_evidence(historical_champion, mutation):
    repo, manifest, historical, receipt = historical_champion()
    if mutation == 'metric':
        path = repo / 'data/models/bundles' / manifest['bundle_id'] / 'manifest.json'
        changed = json.loads(path.read_text())
        changed['historical_admission']['receipt']['horizons']['7']['cohorts']['optionless']['model_mae'] = 0.
    else:
        path = repo / 'data/models/candidates' / historical['bundle_id'] / 'model_validation_receipt.json'
        changed = json.loads(path.read_text())
        if mutation == 'receipt_member':
            _receipt_artifact(changed, 'model_bundle')['members'].append({'path': 'metadata_T25.json', 'bytes': 1, 'sha256': 'f' * 64})
        else:
            changed['horizons'].append(25)
    _write_json(path, changed)
    with pytest.raises(ModelBundleError):
        build_validation(repo)


@pytest.mark.parametrize('mutation', ['duplicate', 'unauthorized_head', 'missing_cohort'])
def test_historical_contract_rejects_changed_metric_cell_scope(historical_champion, monkeypatch, mutation):
    import tools.validate_public_contracts as contracts
    repo, *_ = historical_champion()
    payload = build_validation(repo)
    if mutation == 'duplicate':
        payload['horizons'].append(payload['horizons'][0])
    elif mutation == 'unauthorized_head':
        payload['horizons'][0]['horizon_days'] = 14
    else:
        payload['horizons'].pop()
    public = repo / 'apps/frontend/public'
    _write_json(public / 'evidence/model-validation.json', payload)
    monkeypatch.setattr(contracts, 'PUBLIC', public)
    with pytest.raises(contracts.ContractError, match='admitted'):
        contracts.validate_model_validation()
