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
