"""Only a complete valid independent panel can reject a frozen challenger."""

import argparse
import json
import shutil

import numpy as np
import pandas as pd
import pytest

from ml.model_control import _cohort_paired_metrics


def _panel(count=200, *, corrupt=False, optionless_rows=0):
    size = count + optionless_rows
    actual = np.linspace(.001, .1, size)
    candidate = actual + .3
    if corrupt:
        candidate[0] = np.nan
    quantiles = np.tile([.01, .025, .05, .075, .09], (size, 1))
    report = _cohort_paired_metrics(
        actual, candidate, actual, quantiles, quantiles,
        np.full(size, .2), np.full(size, .2),
        np.array(["strict_options"] * count + ["optionless"] * optionless_rows),
        supported_cohorts=["strict_options", "optionless"] if optionless_rows else ["strict_options"],
    )
    return {"status": report["status"], "method": "prospective_paired_evidence",
            "horizons": {"1": report}, "issues": report["issues"],
            "evaluation_complete": report.get("evaluation_complete", False),
            "failure_kind": report.get("failure_kind")}


def test_numeric_quality_failure_is_distinct_from_invalid_or_incomplete_evidence():
    failed = _panel()
    assert failed["status"] == "failed"
    assert failed["evaluation_complete"] is True
    assert failed["failure_kind"] == "quality"
    for incomplete in (_panel(199), _panel(corrupt=True), _panel(optionless_rows=199)):
        assert incomplete["evaluation_complete"] is False
        assert incomplete["failure_kind"] != "quality"


def test_signed_malformed_admission_is_incomplete_candidate_evidence(tmp_path, monkeypatch):
    import model_control_plane_impl as plane
    from ml.candidate_evidence import sign_candidate_evidence
    from test_model_control import _keys, _bundle
    from test_model_control_plane_script import _archive_fixture

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    bundle = _bundle(tmp_path / "candidate", private, target_shift=.01)
    root = tmp_path / "models"
    shutil.copytree(bundle, root / "bundles" / bundle.name)
    _archive_fixture(root, bundle, private)
    archive = root / "candidates" / bundle.name
    assert plane._has_complete_candidate_evidence(root, bundle.name, [1])

    (archive / "historical_admission.json").write_text("[]")
    sign_candidate_evidence(archive, bundle.name, private_key=private)

    assert plane._has_complete_candidate_evidence(root, bundle.name, [1]) is False


@pytest.mark.parametrize("kind", ["complete_failed", "insufficient", "corrupt", "sparse_cohort", "held", "drift_hold"])
def test_completed_failure_retires_only_the_challenger_and_allows_next_candidate(tmp_path, monkeypatch, kind):
    import model_control_plane_impl as plane
    from ml.model_bundle import verify_registry
    from test_model_control import _bundle
    from test_model_control_plane_script import _monitor_fixture, _archive_fixture

    args = _monitor_fixture(tmp_path, monkeypatch)
    private = __import__("os").environ["MODEL_BUNDLE_SIGNING_KEY"].encode()
    candidate = _bundle(tmp_path / "candidate", private, target_shift=.01, metadata_overrides={
        "feature_protocol": "quantiv.earnings-causal.v2", "target_protocol": "quantiv.session-reaction.v2",
    })
    shutil.copytree(candidate, args.models_root / "bundles" / candidate.name)
    _archive_fixture(args.models_root, candidate, private)
    candidate_manifest = tmp_path / "candidate.json"
    candidate_manifest.write_text(json.dumps({"bundle_id": candidate.name,
                                             "bundle_dir": str(args.models_root / "bundles" / candidate.name)}))
    forecasts = pd.read_parquet(args.forecast_path)
    forecasts["model_bundle_id"] = candidate.name
    candidate_forecast = tmp_path / "candidate.parquet"
    forecasts.to_parquet(candidate_forecast, index=False)
    decide_args = argparse.Namespace(**vars(args), candidate_manifest=candidate_manifest,
        candidate_forecast=candidate_forecast, champion_forecast=args.forecast_path,
        report=tmp_path / "decision.json", training_dir=tmp_path / "training",
        production_forecast_dir=args.forecast_dir)
    panel = _panel(199) if kind == "insufficient" else _panel(corrupt=kind == "corrupt", optionless_rows=199 if kind == "sparse_cohort" else 0)
    monkeypatch.setattr(plane, "_activation_gate", lambda _: {"status": "held", "reason": "held"} if kind == "held" else {"status": "passed"})
    monkeypatch.setattr(plane, "validate_forecast_artifact", lambda *a, **k: {"status": "passed"})
    monkeypatch.setattr(plane, "_rolling_drift_assessment", lambda *a, **k: {"status": "insufficient_data" if kind == "drift_hold" else "passed"})
    monkeypatch.setattr(plane, "compare_on_common_holdout", lambda *a, **k: {"status": "insufficient_data", "issues": ["no common holdout"]})
    monkeypatch.setattr(plane, "_prospective_comparison", lambda *a, **k: panel)
    monkeypatch.setattr(plane, "shadow_score_report", lambda *a, **k: {"status": "passed", "issues": []})
    pointer = args.models_root / "control/champion.json"
    pointer_before = pointer.read_bytes()
    bundle_before = {path.name: path.read_bytes() for path in candidate.iterdir() if path.is_file()}
    assert plane.decide(decide_args) == 0
    report = json.loads(decide_args.report.read_text())
    assert report["promoted"] is False
    assert pointer.read_bytes() == pointer_before
    assert {path.name: path.read_bytes() for path in candidate.iterdir() if path.is_file()} == bundle_before
    registry = verify_registry(json.loads((args.models_root / "control/registry.json").read_text()))
    if kind == "complete_failed":
        assert report["action"] == "reject_challenger"
        assert report["challenger_rejected"] is True
        assert registry["challenger_bundle_id"] is None
        newer = _bundle(tmp_path / "newer", private, target_shift=.02, metadata_overrides={
            "feature_protocol": "quantiv.earnings-causal.v2", "target_protocol": "quantiv.session-reaction.v2",
        })
        shutil.copytree(newer, args.models_root / "bundles" / newer.name)
        _archive_fixture(args.models_root, newer, private)
        candidate_manifest.write_text(json.dumps({"bundle_id": newer.name,
            "bundle_dir": str(args.models_root / "bundles" / newer.name)}))
        retained = plane._retain_candidate(decide_args)
        assert retained["challenger_bundle_id"] == newer.name
    else:
        assert report["action"] == "retain_champion"
        assert registry["challenger_bundle_id"] == candidate.name
