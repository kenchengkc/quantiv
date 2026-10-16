from __future__ import annotations

import json
from datetime import date, datetime, timezone
import subprocess
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


def test_source_freeze_has_provider_current_market_context(tmp_path, monkeypatch):
    import duckdb
    import pandas as pd
    import market_sessions
    from ml.causal_features import causal_macro_features

    completed = date(2026, 10, 15)
    previous = date(2026, 10, 14)
    monkeypatch.setattr(market_sessions, "latest_completed_us_market_session", lambda: completed)
    paths = {name: tmp_path / f"{name}.parquet" for name in ("vix", "volhist")}
    for path in paths.values():
        pd.DataFrame({"date": [previous], "vix_close": [16.87]}).to_parquet(path)

    def python(script, *args, **kwargs):
        provider = ("vix" if script == "scripts/sync_vix.py" else
                    "volhist" if script == "scripts/sync_dolthub.py" and "--volhist" in args else None)
        if provider:
            pd.DataFrame({"date": [previous, completed], "vix_close": [16.87, 16.46]}).to_parquet(paths[provider])
        if script == "scripts/data_release.py":
            with duckdb.connect() as conn:
                conn.execute(f"CREATE VIEW v_vix AS SELECT * FROM read_parquet('{paths['vix']}')")
                prices = pd.DataFrame({"date": [previous, completed] * 2,
                                       "act_symbol": ["SPY", "SPY", "TLT", "TLT"],
                                       "close": [100.0, 101.0, 90.0, 91.0]})
                conn.register("v_ohlcv", prices)
                macro = causal_macro_features(conn, as_of_date=completed)
                current = macro.loc[pd.to_datetime(macro["date"]).dt.date.eq(completed)]
                assert current["vix_current"].tolist() == [16.46], "current VIX must be fetched before source freeze"
            assert pd.read_parquet(paths["volhist"])["date"].max() == completed

    monkeypatch.setattr(pipeline, "_python", python)
    monkeypatch.setattr(pipeline, "_run", lambda *args, **kwargs: None)
    pipeline.prepare_data(tmp_path)


@pytest.mark.parametrize("provider", ["vix", "volhist"])
def test_failed_market_context_fetch_stops_source_publication(tmp_path, monkeypatch, provider):
    published = []

    def python(script, *args, **kwargs):
        if (provider == "vix" and script == "scripts/sync_vix.py" or
                provider == "volhist" and script == "scripts/sync_dolthub.py" and "--volhist" in args):
            if kwargs.get("check", True):
                raise subprocess.CalledProcessError(1, [script, *args])
            return SimpleNamespace(returncode=1)
        if script == "scripts/data_release.py":
            published.append("freeze")

    monkeypatch.setattr(pipeline, "_python", python)
    monkeypatch.setattr(pipeline, "_run", lambda *args, **kwargs: published.append("upload"))
    with pytest.raises(subprocess.CalledProcessError):
        pipeline.prepare_data(tmp_path)
    assert published == []


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
    assert events == ["reserve", "persist", "fit", "package", "archive", "claim", "persist", "evaluate"]
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


def test_failed_candidate_archive_prevents_claim_and_reserved_outcome_reads(tmp_path, monkeypatch):
    import archive_model_candidate

    events = _fit_fixture(tmp_path, monkeypatch)

    def fail(**kwargs):
        raise RuntimeError("candidate archive unavailable")

    monkeypatch.setattr(archive_model_candidate, "archive_candidate", fail)
    with pytest.raises(RuntimeError, match="candidate archive unavailable"):
        pipeline.fit(tmp_path, "reviewed")
    assert "claim" not in events
    assert "evaluate" not in events


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
    monkeypatch.setattr(pipeline, "prepare_publication_inputs", lambda *_: False)
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


def test_historical_publication_validates_exact_signed_monitor_subset(tmp_path, monkeypatch):
    import model_publication

    candidate = tmp_path / "forecasts/candidate.parquet"
    selected = tmp_path / "forecasts/eligible/selected.parquet"
    bundle = tmp_path / "models/bundles" / ("a" * 64)
    calls = []

    def verify(data, *, forecast_path):
        assert data == tmp_path and forecast_path == candidate
        return {"can_publish_ml": True, "state": "partial", "eligible_forecast_path": str(selected)}

    monkeypatch.setattr(model_publication, "verify_publication", verify)
    monkeypatch.setattr(pipeline, "_python", lambda *args: calls.append(args))
    pipeline.validate_selected_publication(tmp_path, bundle, candidate)
    assert len(calls) == 1
    call = calls[0]
    assert call[:2] == ("scripts/validate_ml_pipeline.py", "forecasts")
    assert call[call.index("--forecast-path") + 1] == selected
    assert call[call.index("--models-dir") + 1] == bundle
    assert call[call.index("--publish-receipt-dir") + 1] == tmp_path / "forecasts/receipts"


def test_held_historical_publication_does_not_issue_an_eligible_receipt(tmp_path, monkeypatch):
    import model_publication

    monkeypatch.setattr(model_publication, "verify_publication", lambda *a, **kw: {"can_publish_ml": False})
    calls = []
    monkeypatch.setattr(pipeline, "_python", lambda *args: calls.append(args))
    pipeline.validate_selected_publication(tmp_path, tmp_path / "bundle", tmp_path / "candidate.parquet")
    assert calls == []


@pytest.mark.parametrize("failure", [None, "non_options", "tampered_source", "unpublished_source", "old_report", "old_accepted_report"])
def test_saved_options_publication_revalidates_without_enabling_stale_quotes(tmp_path, failure):
    import hashlib
    from data_release import build_release
    from frontend_data.options_policy import options_display_available

    source = tmp_path / "parquet/options_chain/year=2026/month=09/2026-09-30.parquet"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"published immutable options")
    build_release(tmp_path)
    report = {
        "schema": "quantiv.data-reconciliation.v2",
        "quality": {"decision_safe": False, "critical_exceptions": 1},
        "exceptions": [{"severity": "critical", "code": "ohlcv_stale" if failure == "non_options" else "options_stale"}],
        "source_reconciliation": {"source_date": "2026-09-30"},
        "quote_quality": {"source_date": "2026-09-30"},
        "datasets": {},
    }
    if failure == "old_accepted_report":
        report["quality"] = {"decision_safe": True, "critical_exceptions": 0}
        report["exceptions"] = []
    report["manifest_id"] = "sha256:" + hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    if failure in {"old_report", "old_accepted_report"}:
        report["generated_at"] = "2020-01-01T00:00:00+00:00"
    manifest = tmp_path / "validation/data_reconciliation.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(report))
    before = manifest.read_bytes()
    if failure == "tampered_source":
        source.write_bytes(b"changed")
    extra = source.with_name("2026-10-01.parquet")
    if failure == "unpublished_source":
        extra.write_bytes(b"unpublished source")
    if failure:
        with pytest.raises(RuntimeError):
            pipeline.prepare_publication_inputs(tmp_path)
        if failure == "unpublished_source":
            assert extra.read_bytes() == b"unpublished source"
        assert manifest.read_bytes() == before
        return
    pipeline.prepare_publication_inputs(tmp_path)
    assert options_display_available(tmp_path, as_of_date=date(2026, 9, 30), verify_fallback=True) is False
    status = json.loads((tmp_path / "validation/options_snapshot_status.json").read_text())
    assert status["policy"]["scoring_allowed"] is False
    assert status["policy"]["thresholds_changed"] is False
    assert status["active_source_date"] == "2026-09-30"
    assert status["fallback_manifest_id"] == report["manifest_id"]
    assert manifest.read_bytes() == before
    assert source.read_bytes() == b"published immutable options"
