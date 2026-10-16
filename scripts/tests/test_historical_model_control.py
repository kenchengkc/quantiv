from __future__ import annotations

import argparse
import json

import pandas as pd
import pytest


def _decision(tmp_path, monkeypatch, *, gate="passed", evidence="passed", drift="passed"):
    import model_control_plane_impl as plane

    candidate = "a" * 64
    root = tmp_path / "models"
    directory = root / "bundles" / candidate
    directory.mkdir(parents=True)
    forecast = tmp_path / "candidate.parquet"
    pd.DataFrame({"model_bundle_id": [candidate], "model_horizon": [7],
                  "em_math_pct": [None], "snapshot_date": ["2026-10-15"]}).to_parquet(forecast)
    manifest = {"bundle_id": candidate, "horizons": [7],
                "historical_admission": {"receipt": {"admission_id": "receipt"}}}
    monkeypatch.setattr(plane, "_retain_candidate", lambda args: {
        "challenger_bundle_id": candidate, "evaluation_bundle_dir": str(directory),
        "champion_bundle_id": None, "previous_bundle_id": None})
    monkeypatch.setattr(plane, "verify_bundle_dir", lambda path: manifest)
    monkeypatch.setattr(plane, "_activation_gate", lambda args: {
        "status": gate, "reason": "current data are stale"})
    monkeypatch.setattr(plane, "validate_forecast_artifact", lambda *a, **kw: {"status": "passed"})
    monkeypatch.setattr(plane, "_rolling_drift_assessment", lambda *a, **kw: {"status": drift})
    calls = []

    def historical(*args, **kwargs):
        calls.append(True)
        return {"status": evidence, "admission_id": "receipt", "admitted_cohorts": {"7": ["optionless"]}}

    monkeypatch.setattr(plane, "verify_historical_candidate", historical)
    monkeypatch.setattr(plane, "create_signed_control_pointer", lambda **kw: kw)
    monkeypatch.setattr(plane, "create_signed_registry", lambda **kw: kw)
    monkeypatch.setattr(plane, "_control_state_after_promotion", lambda root: ({}, {"history": []}))
    args = argparse.Namespace(models_root=root, candidate_forecast=forecast,
        champion_forecast=None, training_dir=tmp_path / "training",
        production_forecast_dir=tmp_path / "forecasts", report=tmp_path / "decision.json")
    return plane, args, calls


def test_historical_admission_can_bootstrap_without_prospective_rows(tmp_path, monkeypatch):
    plane, args, calls = _decision(tmp_path, monkeypatch)
    assert plane.decide(args) == 0
    report = json.loads(args.report.read_text())
    assert report["promoted"] is True
    assert report["historical_evidence"]["admission_id"] == "receipt"
    assert report["prospective_evidence"] is None
    assert calls == [True]


def test_historical_metrics_do_not_override_current_freshness(tmp_path, monkeypatch):
    plane, args, calls = _decision(tmp_path, monkeypatch, gate="held")
    assert plane.decide(args) == 0
    report = json.loads(args.report.read_text())
    assert report["promoted"] is False
    assert "current data are stale" in report["reasons"]
    assert calls == []
    assert not (args.models_root / "control/champion.json").exists()


def test_historical_metrics_do_not_override_failed_live_drift(tmp_path, monkeypatch):
    plane, args, calls = _decision(tmp_path, monkeypatch, drift="failed")
    assert plane.decide(args) == 0
    report = json.loads(args.report.read_text())
    assert report["promoted"] is False
    assert any("drift" in reason for reason in report["reasons"])
    assert not (args.models_root / "control/champion.json").exists()


def test_failed_historical_evidence_cannot_bootstrap(tmp_path, monkeypatch):
    plane, args, calls = _decision(tmp_path, monkeypatch, evidence="failed")
    assert plane.decide(args) == 0
    assert json.loads(args.report.read_text())["promoted"] is False
    assert calls == [True]


def test_historical_candidate_requires_retained_source_and_test_files(tmp_path):
    from historical_candidate_evidence import verify_historical_candidate

    with pytest.raises((ValueError, RuntimeError, OSError)):
        verify_historical_candidate(tmp_path, tmp_path / "missing")


def test_explicit_historical_candidate_is_not_replaced_by_older_frozen_challenger(tmp_path, monkeypatch):
    import sys
    import model_control_plane as wrapper

    record = tmp_path / "candidate.json"
    record.write_text(json.dumps({"bundle_id": "a" * 64, "bundle_dir": str(tmp_path / "bundle")}))
    arguments = ["model_control_plane.py", "decide", "--candidate-manifest", str(record)]
    monkeypatch.setattr(sys, "argv", arguments.copy())
    monkeypatch.setattr(wrapper._impl, "verify_bundle_dir", lambda _: {"historical_admission": {"receipt": {}}})

    def registry(_):
        raise AssertionError("older frozen selection must not override an explicitly tested historical candidate")

    monkeypatch.setattr(wrapper._impl, "_control_state", registry)
    wrapper._resolve_registered_candidate()
    assert sys.argv == arguments


@pytest.fixture
def promoted_historical_candidate(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from ml.model_bundle import create_signed_control_pointer, create_signed_registry
    import ml.prospective_admission as prospective
    import model_control_plane_impl as plane

    key = Ed25519PrivateKey.generate()
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode())
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    current, previous = "b" * 64, "a" * 64
    root = tmp_path / "models"
    control, bundle = root / "control", root / "bundles" / current
    control.mkdir(parents=True)
    bundle.mkdir(parents=True)
    (bundle / "metadata_T7.json").write_text(json.dumps({"feature_protocol": "quantiv.earnings-causal.v2"}))
    original_decision = {"action": "promote", "candidate_bundle_id": current}
    (control / "champion.json").write_text(json.dumps(create_signed_control_pointer(
        bundle_id=current, previous_bundle_id=previous, decision=original_decision)))
    (control / "registry.json").write_text(json.dumps(create_signed_registry(
        champion_bundle_id=current, previous_bundle_id=previous, challenger_bundle_id=None,
        decision=original_decision, history=[original_decision])))
    originals = {path: path.read_bytes() for path in control.iterdir()}
    record = tmp_path / "candidate.json"
    record.write_text(json.dumps({"bundle_id": current, "bundle_dir": str(bundle)}))
    forecast = tmp_path / "candidate.parquet"
    pd.DataFrame({"model_bundle_id": [current], "model_horizon": [7],
                  "em_math_pct": [None], "snapshot_date": ["2026-10-15"]}).to_parquet(forecast)
    manifest = {"bundle_id": current, "horizons": [7], "receipt_id": "model-receipt",
                "historical_admission": {"receipt": {"admission_id": "historical-receipt"},
                                         "source_manifest": {"bundle_id": "c" * 64}}}
    monkeypatch.setattr(plane, "verify_bundle_dir", lambda _: manifest)
    monkeypatch.setattr(plane, "_has_complete_candidate_evidence", lambda *a: True)
    monkeypatch.setattr(prospective, "declare_admission", lambda *a, **kw: {
        "comparators": {"7": {"optionless": "incumbent"}}})
    monkeypatch.setattr(plane, "_activation_gate", lambda _: {"status": "passed"})
    monkeypatch.setattr(plane, "validate_forecast_artifact", lambda *a, **kw: {"status": "passed"})
    monkeypatch.setattr(plane, "_rolling_drift_assessment", lambda *a, **kw: {"status": "warning"})
    monkeypatch.setattr(plane, "verify_historical_candidate", lambda *a: {
        "status": "passed", "admission_id": "historical-receipt", "admitted_cohorts": {"7": ["optionless"]}})
    monkeypatch.setattr(plane, "shadow_score_report", lambda *a, **kw: {"status": "passed"})
    args = argparse.Namespace(models_root=root, candidate_manifest=record, candidate_forecast=forecast,
        champion_forecast=forecast, training_dir=tmp_path / "training",
        production_forecast_dir=tmp_path / "forecasts", report=tmp_path / "decision.json")
    return plane, args, originals


def test_exact_historical_retry_preserves_rollback_and_reauthorizes_handoff(promoted_historical_candidate):
    from ml.model_bundle import verify_control_pointer, verify_registry
    from scripts.activate_model_bundle import expected_bundle_id

    plane, args, originals = promoted_historical_candidate
    assert plane.register_candidate(args) == 0
    assert plane.decide(args) == 0
    report = json.loads(args.report.read_text())
    pointer = verify_control_pointer(json.loads((args.models_root / "control/champion.json").read_text()))
    registry = verify_registry(json.loads((args.models_root / "control/registry.json").read_text()))
    assert pointer["previous_bundle_id"] == registry["previous_bundle_id"] == report["previous_bundle_id"] == "a" * 64
    assert {path: path.read_bytes() for path in originals} == originals
    assert report["action"] == "resume_champion" and report["promoted"] is True
    assert report["shadow_scoring"] is None
    assert expected_bundle_id(args.report) == "b" * 64
    pd.testing.assert_frame_equal(pd.read_parquet(report["production_forecast"]),
                                  pd.read_parquet(args.candidate_forecast))


@pytest.mark.parametrize("failure", ["source", "forecast", "drift", "historical"])
def test_exact_historical_retry_still_holds_failed_current_evidence(
    promoted_historical_candidate, monkeypatch, failure,
):
    plane, args, originals = promoted_historical_candidate
    if failure == "source":
        monkeypatch.setattr(plane, "_activation_gate", lambda _: {"status": "held", "reason": "stale source"})
    elif failure == "forecast":
        monkeypatch.setattr(plane, "validate_forecast_artifact", lambda *a, **kw: {"status": "failed"})
    elif failure == "drift":
        monkeypatch.setattr(plane, "_rolling_drift_assessment", lambda *a, **kw: {"status": "critical"})
    else:
        monkeypatch.setattr(plane, "verify_historical_candidate", lambda *a: {"status": "failed",
            "admission_id": "historical-receipt", "admitted_cohorts": {"7": ["optionless"]}})
    assert plane.register_candidate(args) == 0
    assert plane.decide(args) == 0
    report = json.loads(args.report.read_text())
    assert report["promoted"] is False and report["production_forecast"] is None
    assert {path: path.read_bytes() for path in originals} == originals


def test_historical_pipeline_retry_reaches_remaining_handoff_without_repromotion(
    promoted_historical_candidate, monkeypatch,
):
    import historical_ml_pipeline as pipeline
    import ml.model_bundle as bundles
    from scripts.activate_model_bundle import expected_bundle_id

    plane, args, originals = promoted_historical_candidate
    handed_off = []
    monkeypatch.setattr(bundles, "verify_bundle_dir", plane.verify_bundle_dir)

    def option(values, name):
        return values[values.index(name) + 1]

    def python(script, *values, **kwargs):
        if script == "scripts/daily_score.py":
            pd.read_parquet(args.candidate_forecast).to_parquet(option(values, "--output-path"))
        elif script == "scripts/model_control_plane.py":
            command = argparse.Namespace(**vars(args))
            command.candidate_manifest = option(values, "--candidate-manifest")
            command.report = option(values, "--report")
            if values[0] == "register":
                plane.register_candidate(command)
            else:
                command.candidate_forecast = option(values, "--candidate-forecast")
                command.champion_forecast = option(values, "--champion-forecast")
                plane.decide(command)
        elif script == "scripts/activate_model_bundle.py":
            assert expected_bundle_id(option(values, "--decision")) == "b" * 64
            handed_off.append("activate")
        elif script == "scripts/import_recent_to_postgres.py":
            assert option(values, "--expected-model-bundle-id") == "b" * 64
            handed_off.append("import")
        elif script == "scripts/model_publication.py" and values[0] == "monitor":
            handed_off.append("monitor")

    def run(*values, **kwargs):
        if "--monitoring-only" in values:
            handed_off.append("publish-monitor")

    monkeypatch.setattr(pipeline, "_python", python)
    monkeypatch.setattr(pipeline, "_run", run)
    pipeline.activate(args.models_root.parent, "b" * 64)
    assert handed_off == ["activate", "import", "monitor", "publish-monitor"]
    assert {path: path.read_bytes() for path in originals} == originals
