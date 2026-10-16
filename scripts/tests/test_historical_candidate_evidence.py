from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.ml.tests.test_historical_admission import _candidate, _fixture_source_report  # noqa: E402
from ml.historical_admission import evaluate_historical_admission  # noqa: E402
from ml.model_bundle import create_historically_admitted_bundle  # noqa: E402
from historical_candidate_evidence import verify_historical_candidate  # noqa: E402


@pytest.fixture
def retained_candidate(tmp_path, monkeypatch, request):
    import archive_model_candidate as archive
    import model_promotion_receipts as promotion
    import ml.historical_admission as historical_engine

    if getattr(request, "param", None) == "dual_cohort":
        prepare = historical_engine.prepare_historical_admission

        def prepare_dual_cohort(**kwargs):
            # Add independent optionless issuers before reservation and fitting;
            # both cohorts then have 220 genuine held-out native predictions.
            path = kwargs["source_training_dir"] / "training_T7.parquet"
            strict = pd.read_parquet(path)
            optionless = strict.copy()
            optionless["__symbol"] = "O" + optionless["__symbol"]
            optionless["straddle_pct"] = float("nan")
            pd.concat([strict, optionless], ignore_index=True).to_parquet(path, index=False)
            return prepare(**kwargs)

        monkeypatch.setattr(historical_engine, "prepare_historical_admission", prepare_dual_cohort)

    real_root = Path(__file__).resolve().parents[2]
    for relative in ("apps/ml/feature_engineering.py", "apps/ml/ml/causal_features.py",
        "apps/ml/ml/corporate_actions.py", "apps/ml/ml/model_protocol.py", "config/market_sessions.json",
        "scripts/corporate_action_universe.py", "scripts/verify_historical_training_gate.py"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(real_root / relative, target)
    monkeypatch.setattr(archive, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(promotion, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(promotion, "FEATURE_ENGINEERING_PATH", tmp_path / "apps/ml/feature_engineering.py")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calendar = data_dir / "earnings_calendar.csv"
    calendar.write_text("act_symbol,date,timing\nS0,2023-01-01,bmo\n")
    report = _fixture_source_report()
    digest = hashlib.sha256(calendar.read_bytes()).hexdigest()
    report.update(earnings_calendar_sha256=digest,
                  calendar_source={"path": calendar.name, "sha256": digest, "events": 1})
    source, reservation, model_receipt, private, public = _candidate(tmp_path, monkeypatch, source_report=report)
    models_root = data_dir / "models"
    source_dir = models_root / "bundles" / source.name
    source_dir.parent.mkdir(parents=True)
    shutil.copytree(source, source_dir)
    shutil.copytree(tmp_path / "dev", data_dir / "ml_training")
    receipt_dir = models_root / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "latest_models.json").write_text(json.dumps(model_receipt))
    validation = data_dir / "validation"
    validation.mkdir()
    shutil.copy2(tmp_path / "historical_admission.json", validation / "historical_training_admission.json")
    promotion.build_promotion_receipts(source_dir / "manifest.json", training_dir=data_dir / "ml_training",
        temporal_path=validation / "promotion/temporal_integrity.json",
        statistical_path=validation / "promotion/statistical_selection.json")
    archive_dir = archive.prepare_candidate_evidence(bundle_dir=source_dir, data_dir=data_dir)
    evidence_dir = models_root / "historical_admissions" / source.name
    _, historical = evaluate_historical_admission(source_dir, reservation, model_receipt,
        data_dir / "ml_training", evidence_dir, private_key=private, public_key=public,
        reservation_registry=tmp_path / "registry")
    serving_dir, _ = create_historically_admitted_bundle(source_dir, models_root / "bundles",
        admission_receipt=historical, source_revision="admitted", private_key=private)
    return models_root, serving_dir, archive_dir, evidence_dir


def test_retained_native_candidate_archive_and_historical_admission_replay_before_promotion(retained_candidate):
    models_root, serving, _, _ = retained_candidate
    report = verify_historical_candidate(models_root, serving)
    assert report["status"] == "passed"
    assert report["admitted_cohorts"] == {"7": ["strict_options"]}
    assert report["source_promotion_evidence"]["temporal_integrity"]["receipt_id"]


@pytest.mark.parametrize("retained_candidate", ["dual_cohort"], indirect=True)
def test_signed_dual_cohort_candidate_replays_despite_canonical_serving_order(retained_candidate):
    models_root, serving, _, _ = retained_candidate
    manifest = json.loads((serving / "manifest.json").read_text())
    assert manifest["serving_capabilities"] == {"7": ["optionless", "strict_options"]}
    assert manifest["historical_admission"]["receipt"]["admitted_cohorts"] == {
        "7": ["strict_options", "optionless"]}
    report = verify_historical_candidate(models_root, serving)
    assert report["status"] == "passed"
    assert report["admitted_cohorts"] == {"7": ["strict_options", "optionless"]}


@pytest.mark.parametrize("capabilities", [
    {"7": ["optionless", "strict_options"]},
    {"7": ["strict_options"], "14": ["strict_options"]},
])
def test_signed_candidate_cannot_expand_historical_cohort_or_head_scope(retained_candidate, capabilities):
    from ml.model_bundle import _bundle_core, _sign

    models_root, serving, _, _ = retained_candidate
    manifest = json.loads((serving / "manifest.json").read_text())
    core = _bundle_core(**{key: manifest[key] for key in (
        "artifacts", "receipt_id", "horizons", "source_revision", "created_at", "historical_admission")},
        serving_capabilities=capabilities)
    forged = models_root / "bundles" / core["bundle_id"]
    shutil.copytree(serving, forged)
    (forged / "manifest.json").write_text(json.dumps(_sign(core, os.environ["MODEL_BUNDLE_SIGNING_KEY"])))
    with pytest.raises(RuntimeError, match="historical|capabilities"):
        verify_historical_candidate(models_root, forged)


@pytest.mark.parametrize("member", ["reservation.json", "candidate_binding.json", "final_test_T7.parquet",
    "historical_training_admission.json", "predictions_T7.parquet", "development_T7.parquet"])
@pytest.mark.parametrize("corruption", ["missing", "changed"])
def test_retained_historical_release_rejects_missing_or_tampered_inputs(retained_candidate, member, corruption):
    models_root, serving, _, evidence = retained_candidate
    path = evidence / member
    if corruption == "missing":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"modified")
    with pytest.raises((RuntimeError, ValueError, OSError)):
        verify_historical_candidate(models_root, serving)


def test_retained_fitting_archive_cannot_be_changed_after_signing(retained_candidate):
    models_root, serving, archive, _ = retained_candidate
    (archive / "historical_admission.json").write_text("{}")
    with pytest.raises((RuntimeError, ValueError)):
        verify_historical_candidate(models_root, serving)
