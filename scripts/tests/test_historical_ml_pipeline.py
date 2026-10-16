from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import historical_ml_pipeline as pipeline


def test_source_coverage_end_stays_on_completed_us_session_after_utc_midnight(tmp_path, monkeypatch):
    import market_sessions

    class Evening(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)

    calls = []
    monkeypatch.setattr(market_sessions, "datetime", Evening)
    monkeypatch.setattr(pipeline, "_python", lambda *args, **kw: calls.append(tuple(map(str, args))))
    monkeypatch.setattr(pipeline, "_run", lambda *args, **kw: None)
    pipeline.prepare_data(tmp_path)
    assert ("scripts/sync_dolthub.py", "--corporate-actions", "--end-date", "2026-10-15") in calls


def _fit_fixture(tmp_path, monkeypatch):
    import archive_model_candidate
    import ml.historical_admission as admission
    import ml.model_bundle as bundles
    import ml.pipeline_validation as validation

    events = []
    workspace = tmp_path / "historical_admission/workspace"
    source = workspace / "models/bundles" / ("a" * 64)
    source.mkdir(parents=True)
    (workspace / "models/receipts").mkdir()
    (workspace / "models/receipts/latest_models.json").write_text("{}")
    (tmp_path / "validation/promotion").mkdir(parents=True)
    (tmp_path / "validation/historical_training_admission.json").write_text("{}")
    monkeypatch.setattr(bundles, "DAILY_HORIZONS", [7])
    monkeypatch.setattr(validation, "validate_model_artifacts", lambda *a, **kw: {"status": "passed"})

    def prepare(**kwargs):
        assert kwargs["source_data_dir"] == tmp_path
        assert kwargs["reservation_registry"] == tmp_path / "models/historical_reservations"
        events.append("reserve")
        return tmp_path / "sealed/reservation.json", {}

    def python(script, *args, **kwargs):
        if script == "apps/ml/model_trainer.py":
            events.append("fit")
        if script == "scripts/package_model_bundle.py":
            events.append("package")
            (tmp_path / "validation/historical_source_bundle.json").write_text(json.dumps({"bundle_dir": str(source)}))

    def evaluate(**kwargs):
        assert kwargs["reservation_registry"] == tmp_path / "models/historical_reservations"
        events.append("evaluate")
        kwargs["output_dir"].mkdir(parents=True)
        return kwargs["output_dir"] / "receipt.json", {
            "status": "failed", "admission_id": "held", "admitted_cohorts": {}}

    monkeypatch.setattr(pipeline, "_python", python)
    monkeypatch.setattr(pipeline, "_run", lambda *a, **kw: SimpleNamespace(stdout="bundles/\n"))
    monkeypatch.setattr(pipeline, "publish_reservation_registry", lambda *a: events.append("persist"))
    monkeypatch.setattr(admission, "prepare_historical_admission", prepare)
    monkeypatch.setattr(admission, "claim_historical_candidate", lambda *a: events.append("claim"))
    monkeypatch.setattr(admission, "evaluate_historical_admission", evaluate)
    monkeypatch.setattr(archive_model_candidate, "prepare_candidate_evidence", lambda **kw: source)
    monkeypatch.setattr(archive_model_candidate, "archive_candidate", lambda **kw: events.append("archive"))
    return events


def test_exact_candidate_commit_is_durable_before_test_outcomes_are_read(tmp_path, monkeypatch):
    events = _fit_fixture(tmp_path, monkeypatch)
    pipeline.fit(tmp_path, "reviewed")
    assert events == ["reserve", "persist", "fit", "package", "claim", "persist", "evaluate", "archive"]
    assert json.loads((tmp_path / "validation/historical_fit.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("publication", [1, 2])
def test_failed_remote_precommit_prevents_fitting_or_test_evaluation(tmp_path, monkeypatch, publication):
    events = _fit_fixture(tmp_path, monkeypatch)
    writes = []

    def persist(*args):
        writes.append(True)
        if len(writes) == publication:
            raise RuntimeError("immutable commit failed")

    monkeypatch.setattr(pipeline, "publish_reservation_registry", persist)
    with pytest.raises(RuntimeError, match="immutable commit"):
        pipeline.fit(tmp_path, "reviewed")
    assert "evaluate" not in events
    if publication == 1:
        assert "fit" not in events


def test_reservation_remote_failure_stops_before_training(tmp_path, monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("remote unavailable")

    monkeypatch.setattr(pipeline, "_run", run)
    with pytest.raises(RuntimeError, match="remote unavailable"):
        pipeline.restore_reservation_registry(tmp_path, "r2:test")
    assert len(calls) == 1


def test_registry_is_immutable_and_read_back_before_use(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "_run", lambda *args, **kw: calls.append(args))
    pipeline.publish_reservation_registry(tmp_path, "r2:test")
    assert calls[0] == ("rclone", "copy", str(tmp_path),
                        "r2:test/models/historical_reservations", "--immutable", "--checksum")
    assert calls[1] == ("rclone", "check", str(tmp_path),
                        "r2:test/models/historical_reservations", "--one-way", "--download")


def test_first_registry_does_not_hide_storage_errors(tmp_path, monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="bundles/\ncontrol/\n")

    monkeypatch.setattr(pipeline, "_run", run)
    assert pipeline.restore_reservation_registry(tmp_path, "r2:test") == tmp_path / "models/historical_reservations"
    assert len(calls) == 1


def test_held_activation_retains_candidate_without_external_activation(tmp_path, monkeypatch):
    import ml.model_bundle as bundles

    candidate, source = "a" * 64, "b" * 64
    monkeypatch.setattr(bundles, "verify_bundle_dir", lambda _: {
        "bundle_id": candidate, "receipt_id": "receipt", "historical_admission": {
            "source_manifest": {"bundle_id": source}}})
    calls = []
    monkeypatch.setattr(pipeline, "_run", lambda *args, **kw: calls.append(args))

    def python(script, *args, **kwargs):
        calls.append((script, *map(str, args)))
        if script == "scripts/model_control_plane.py" and args[0] == "decide":
            path = args[args.index("--report") + 1]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"promoted": False}))

    monkeypatch.setattr(pipeline, "_python", python)
    pipeline.activate(tmp_path, candidate)
    assert any("--candidate-control-only" in call for call in calls)
    assert not any("scripts/activate_model_bundle.py" in call for call in calls)
    assert not any("scripts/import_recent_to_postgres.py" in call for call in calls)
    assert not any("--model-recovery" in call for call in calls)


@pytest.mark.parametrize("value", ["../a", "", "A" * 64, "a" * 63])
def test_candidate_identity_cannot_escape_storage(value):
    with pytest.raises(ValueError, match="identity"):
        pipeline._bundle_id(value)
