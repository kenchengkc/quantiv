"""Exercise retained learning evidence with real signatures and model artifacts."""

from argparse import Namespace
import json
from pathlib import Path
import shutil

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import lightgbm as lgb
import pandas as pd
import pytest

import model_control_plane as control_entrypoint
import model_control_plane_impl as control
import model_promotion_receipts as receipts
from ml.candidate_evidence import sign_candidate_evidence, verify_candidate_evidence
from ml.evidence_receipt import build_evidence_receipt, publish_evidence_receipt
from ml.model_artifact import load_native_model, save_native_model
from ml.model_bundle import (
    ModelBundleError,
    create_signed_bundle,
    required_artifact_names,
)
from ml.model_protocol import FEATURE_PROTOCOL_CAUSAL, TARGET_PROTOCOL_CAUSAL
from scripts import archive_model_candidate as archive
from scripts.data_release import build_release
from scripts.tests.historical_admission_fixtures import write_action_receipt
from scripts.verify_historical_training_gate import verify_historical_training_gate


@pytest.fixture
def learning_run(tmp_path, monkeypatch):
    repository = Path(__file__).resolve().parents[2]
    for relative in (
        "apps/ml/feature_engineering.py",
        "apps/ml/ml/causal_features.py",
        "apps/ml/ml/corporate_actions.py",
        "apps/ml/ml/model_protocol.py",
        "config/market_sessions.json",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / relative, destination)
    monkeypatch.setattr(archive, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(receipts, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        receipts,
        "FEATURE_ENGINEERING_PATH",
        tmp_path / "apps/ml/feature_engineering.py",
    )
    monkeypatch.setattr(control_entrypoint, "REPO_ROOT", tmp_path)
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    public_path = tmp_path / "public.pem"
    public_path.write_bytes(public)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public_path))

    data = tmp_path / "data"
    prices = data / "parquet/ohlcv/history.parquet"
    prices.parent.mkdir(parents=True)
    pd.DataFrame(
        {"act_symbol": ["TEST"], "date": ["2026-09-01"], "close": [100.0]}
    ).to_parquet(prices)
    write_action_receipt(data)
    build_release(data)
    events = pd.DataFrame(
        {"act_symbol": ["TEST"], "date": ["2026-09-01"], "timing": ["bmo"]}
    )
    events.to_csv(data / "earnings_calendar.csv", index=False)
    events.to_parquet(data / "earnings_calendar.parquet", index=False)
    admission = verify_historical_training_gate(data_dir=data)
    (data / "validation").mkdir()
    (data / "validation/historical_training_admission.json").write_text(
        json.dumps(admission)
    )

    training = data / "ml_training"
    training.mkdir()
    frame = pd.DataFrame(
        {
            "straddle_pct": [0.03, 0.04, 0.05],
            "target": [0.04, 0.05, 0.03],
            "__earnings_date": pd.to_datetime(
                ["2026-01-20", "2026-02-20", "2026-03-20"]
            ).date,
            "__snapshot_date": pd.to_datetime(
                ["2026-01-19", "2026-02-19", "2026-03-19"]
            ).date,
            "__pre_price_date": pd.to_datetime(
                ["2026-01-16", "2026-02-19", "2026-03-19"]
            ).date,
            "__post_price_date": pd.to_datetime(
                ["2026-01-20", "2026-02-20", "2026-03-20"]
            ).date,
            "__label_available_at": pd.to_datetime(
                ["2026-01-20", "2026-02-20", "2026-03-20"]
            ).date,
            "__label_source": "ohlcv_session_close",
            "__target_protocol": TARGET_PROTOCOL_CAUSAL,
            "__cohort": "strict_options",
        }
    )
    frame.to_parquet(training / "training_T1.parquet", index=False)
    metadata = {
        "feature_protocol": FEATURE_PROTOCOL_CAUSAL,
        "target_protocol": TARGET_PROTOCOL_CAUSAL,
        "feature_cols": ["straddle_pct"],
        "supported_cohorts": ["strict_options"],
        "n_samples": 3,
    }
    (training / "metadata_T1.json").write_text(json.dumps(metadata))
    models = data / "models"
    models.mkdir()
    estimator = lgb.train(
        {
            "objective": "regression",
            "min_data_in_leaf": 1,
            "num_leaves": 2,
            "verbosity": -1,
            "num_threads": 1,
        },
        lgb.Dataset(frame[["straddle_pct"]], label=frame["target"]),
        num_boost_round=2,
    )
    for name in required_artifact_names([1]):
        if name.endswith(".txt"):
            save_native_model(estimator, models / name)
        else:
            (models / name).write_text(json.dumps(metadata))
    report = {"status": "passed", "issues": [], "stages": {"models": {"horizons": [1]}}}

    def package(revision):
        report["evidence_receipt"] = build_evidence_receipt(
            report,
            scope="models",
            repo_root=tmp_path,
            data_dir=data,
            training_dir=training,
            models_dir=models,
            forecast_path=None,
            horizons=[1],
        )
        _, latest = publish_evidence_receipt(
            report, receipt_dir=models / "receipts", scope="models", forecast_path=None
        )
        report_path = data / "validation/models.json"
        report_path.write_text(json.dumps(report))
        bundle_dir, manifest = create_signed_bundle(
            models,
            models / "bundles",
            receipt_path=latest,
            validation_report_path=report_path,
            source_revision=revision,
            horizons=[1],
        )
        candidate = data / "validation/candidate_bundle.json"
        candidate.write_text(
            json.dumps(
                {
                    "bundle_id": manifest["bundle_id"],
                    "bundle_dir": str(bundle_dir),
                    "receipt_id": manifest["receipt_id"],
                }
            )
        )
        promotion = data / "validation/promotion"
        receipts.build_promotion_receipts(
            candidate,
            training_dir=training,
            temporal_path=promotion / "temporal_integrity.json",
            statistical_path=promotion / "statistical_selection.json",
            source_revision=revision,
        )
        return bundle_dir, manifest, candidate

    bundle_dir, manifest, candidate = package("first-fit")
    return Namespace(
        root=tmp_path,
        data=data,
        models=models,
        training=training,
        bundle_dir=bundle_dir,
        manifest=manifest,
        candidate=candidate,
        package=package,
        report=report,
    )


def _verify_frozen(run, evidence):
    return receipts.verify_promotion_receipts(
        run.manifest["bundle_id"],
        training_dir=evidence / "training",
        temporal_path=evidence / "promotion/temporal_integrity.json",
        statistical_path=evidence / "promotion/statistical_selection.json",
        archived_evidence_dir=evidence,
    )


def test_signed_archive_survives_new_fit_and_empty_upcoming_events(
    learning_run, monkeypatch
):
    run = learning_run
    release_pointer = run.data / "control/current_data_release.json"
    release_before = release_pointer.read_bytes()
    serving = run.data / "forecasts/forecasts_2026-09-30.parquet"
    serving.parent.mkdir()
    serving.write_bytes(b"prior-validated-production")
    evidence = archive.prepare_candidate_evidence(
        bundle_dir=run.bundle_dir, data_dir=run.data
    )
    assert (
        verify_candidate_evidence(evidence, run.manifest["bundle_id"])["bundle_id"]
        == run.manifest["bundle_id"]
    )
    assert load_native_model(run.bundle_dir / "lgbm_T1.txt").feature_name() == [
        "straddle_pct"
    ]
    frozen_proof = _verify_frozen(run, evidence)
    args = Namespace(
        candidate_manifest=run.candidate,
        models_root=run.models,
        report=run.data / "validation/register.json",
    )
    first = control._retain_candidate(args)
    assert first["challenger_bundle_id"] == run.manifest["bundle_id"]
    assert first["champion_bundle_id"] is None

    # A later run replaces mutable fitting inputs, code, and latest receipts.
    frame = pd.read_parquet(run.training / "training_T1.parquet")
    frame.loc[0, "straddle_pct"] = 0.08
    frame.to_parquet(run.training / "training_T1.parquet", index=False)
    source = run.root / "apps/ml/feature_engineering.py"
    source.write_text(source.read_text() + "\n# Later feature builder revision.\n")
    newer_dir, newer_manifest, _ = run.package("second-fit")
    archive.prepare_candidate_evidence(bundle_dir=newer_dir, data_dir=run.data)
    retained = control._retain_candidate(args)
    assert retained["candidate_bundle_id"] == newer_manifest["bundle_id"]
    assert retained["challenger_bundle_id"] == run.manifest["bundle_id"]
    assert _verify_frozen(run, evidence) == frozen_proof
    assert (
        archive.prepare_candidate_evidence(bundle_dir=run.bundle_dir, data_dir=run.data)
        == evidence
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "model_control_plane.py",
            "decide",
            "--candidate-manifest",
            retained["evaluation_manifest"],
            "--archived-evidence-dir",
            retained["evaluation_evidence_dir"],
        ],
    )
    assert control_entrypoint._validate_decision_evidence() == frozen_proof

    from scripts.daily_score import save_forecasts

    candidate_forecasts = run.data / "validation/candidate_forecasts.parquet"
    save_forecasts(pd.DataFrame(), run.data, output_path=candidate_forecasts)
    assert pd.read_parquet(candidate_forecasts).empty
    calls = []
    monkeypatch.setattr(
        archive.subprocess, "run", lambda argv, **kwargs: calls.append(argv)
    )
    result = archive.archive_candidate(
        bundle_dir=run.bundle_dir, evidence_dir=evidence, remote="r2:test"
    )
    assert result["bundle_id"] == run.manifest["bundle_id"]
    assert [call[1] for call in calls] == ["copy", "copy", "check", "check"]
    assert all("--immutable" in call for call in calls if call[1] == "copy")
    assert all(
        "/models/bundles/" in call[3] or "/models/candidates/" in call[3]
        for call in calls
    )
    assert not (run.models / "control/champion.json").exists()
    assert serving.read_bytes() == b"prior-validated-production"
    assert release_pointer.read_bytes() == release_before


def test_signed_archive_rejects_changed_training_before_remote_copy(
    learning_run, monkeypatch
):
    run = learning_run
    evidence = archive.prepare_candidate_evidence(
        bundle_dir=run.bundle_dir, data_dir=run.data
    )
    (evidence / "training/training_T1.parquet").write_bytes(b"altered-training")
    calls = []
    monkeypatch.setattr(
        archive.subprocess, "run", lambda *args, **kwargs: calls.append(args)
    )
    with pytest.raises(ModelBundleError, match="digest mismatch"):
        archive.archive_candidate(
            bundle_dir=run.bundle_dir, evidence_dir=evidence, remote="r2:test"
        )
    assert calls == []


def test_archive_cannot_bind_another_valid_validation_receipt_to_bundle(learning_run):
    run = learning_run
    run.report["stages"]["models"]["later_run"] = True
    run.report["evidence_receipt"] = build_evidence_receipt(
        run.report,
        scope="models",
        repo_root=run.root,
        data_dir=run.data,
        training_dir=run.training,
        models_dir=run.models,
        forecast_path=None,
        horizons=[1],
    )
    assert run.report["evidence_receipt"]["receipt_id"] != run.manifest["receipt_id"]
    publish_evidence_receipt(
        run.report,
        receipt_dir=run.models / "receipts",
        scope="models",
        forecast_path=None,
    )
    with pytest.raises(
        (ModelBundleError, ValueError),
        match="receipt.*(bundle|manifest|match|candidate)",
    ):
        archive.prepare_candidate_evidence(bundle_dir=run.bundle_dir, data_dir=run.data)


def test_archived_receipt_must_match_bundle_even_with_valid_archive_signature(
    learning_run,
):
    run = learning_run
    evidence = archive.prepare_candidate_evidence(
        bundle_dir=run.bundle_dir, data_dir=run.data
    )
    run.report["stages"]["models"]["later_run"] = True
    replacement = build_evidence_receipt(
        run.report,
        scope="models",
        repo_root=run.root,
        data_dir=run.data,
        training_dir=run.training,
        models_dir=run.models,
        forecast_path=None,
        horizons=[1],
    )
    (evidence / "model_validation_receipt.json").write_text(json.dumps(replacement))
    sign_candidate_evidence(evidence, run.manifest["bundle_id"])
    with pytest.raises(
        (ModelBundleError, ValueError),
        match="receipt.*(bundle|manifest|match|candidate)",
    ):
        _verify_frozen(run, evidence)


@pytest.mark.parametrize("member", ["training", "calendar", "source"])
def test_archive_preparation_rejects_inputs_changed_after_validation(learning_run, member):
    run = learning_run
    if member == "training":
        path = run.training / "training_T1.parquet"
        frame = pd.read_parquet(path)
        frame.loc[0, "target"] = .99
        frame.to_parquet(path, index=False)
    elif member == "calendar":
        path = run.data / "earnings_calendar.parquet"
        frame = pd.read_parquet(path)
        frame.loc[0, "timing"] = "amc"
        frame.to_parquet(path, index=False)
    else:
        path = run.root / "apps/ml/ml/causal_features.py"
        path.write_text(path.read_text() + "\n# modified after validation\n")
    with pytest.raises((ModelBundleError, ValueError), match="(changed|digest|admitted|validated|stale)"):
        archive.prepare_candidate_evidence(bundle_dir=run.bundle_dir, data_dir=run.data)
    assert not (run.models / "candidates" / run.manifest["bundle_id"]).exists()


def test_packaging_rejects_trees_that_changed_after_model_validation(learning_run):
    run = learning_run
    metadata = run.models / "metadata_T1.json"
    payload = json.loads(metadata.read_text())
    payload["changed_after_validation"] = True
    metadata.write_text(json.dumps(payload))
    existing = set((run.models / "bundles").iterdir())
    with pytest.raises(ModelBundleError, match="model changed after validation"):
        create_signed_bundle(
            run.models, run.models / "bundles",
            receipt_path=run.models / "receipts/latest_models.json",
            validation_report_path=run.data / "validation/models.json",
            source_revision="changed-model", horizons=[1],
        )
    assert set((run.models / "bundles").iterdir()) == existing
