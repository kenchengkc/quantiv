"""Accepted quotes refresh the signed champion without activating another model."""

from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import shutil

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from ml.evidence_receipt import build_evidence_receipt, verify_evidence_receipt
from ml.model_artifact import save_native_model
from ml.data_reconciliation import build_reconciliation_manifest
from ml.model_bundle import (
    ModelBundleError,
    create_signed_bundle,
    create_signed_control_pointer,
    create_signed_registry,
    required_artifact_names,
    verify_control_pointer,
    verify_monitor_receipt,
    verify_registry,
)
from scripts import daily_score, model_publication, validate_ml_pipeline
from scripts.build_data_reconciliation import _event_coverage, _quote_quality
from scripts.event_forecast_ledger import record_publications
from scripts.market_sessions import latest_completed_us_market_session
from scripts.options_snapshot_resilience import finalize_snapshot
from scripts.verify_retrain_data_gate import verify_retrain_data_gate
from scripts.setup_duckdb_from_parquet import setup_views
from tools.frontend_data import forecast_artifacts, shared as frontend_shared
import model_control_plane_impl as control_plane
import ml.model_control as model_control
import ml.live_forecast_validation as live_validation
import ml.pipeline_validation as pipeline_validation


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _write_parquet(root: Path, dataset: str, source_date, rows) -> Path:
    path = root / "parquet" / dataset / f"year={source_date.year}" / f"month={source_date.month:02}" / f"{source_date}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def _signed_champion(data: Path, tmp_path: Path, *, metadata_overrides=None, horizons=(7, 14)) -> Path:
    models = data / "models"
    models.mkdir()
    training = tmp_path / "training"
    training.mkdir()
    inputs = pd.DataFrame({"straddle_pct": np.linspace(.02, .08, 300), "atm_iv": .5,
                           "dte": 14., "em_iv_pct": .5 * np.sqrt(14. / 365.)})
    native = lgb.train(
        {"objective": "regression", "min_data_in_leaf": 5, "num_leaves": 4,
         "verbosity": -1, "num_threads": 1},
        lgb.Dataset(inputs, label=.015 + .5 * inputs["straddle_pct"]),
        num_boost_round=12,
    )
    # Absence of a cohort declaration is the historical strict-options contract.
    # The same fitted model is reused for all quantiles to keep this fixture small.
    metadata = {
        "feature_cols": list(inputs.columns),
        "feature_reference": {"straddle_pct": {
            "missing_rate": 0., "cuts": [.04], "probabilities": [.5, .5],
        }, **{name: {"missing_rate": 0., "cuts": [], "probabilities": []}
              for name in ("atm_iv", "dte", "em_iv_pct")}},
        "quantiles": [.10, .25, .50, .75, .90],
    }
    metadata.update(metadata_overrides or {})
    for horizon in horizons:
        head_metadata = {**metadata, "horizon": horizon}
        for name in required_artifact_names([horizon]):
            if name.endswith(".txt"):
                save_native_model(native, models / name)
            else:
                _write_json(models / name, head_metadata)
        inputs.assign(target=.015 + .5 * inputs["straddle_pct"]).to_parquet(
            training / f"training_T{horizon}.parquet", index=False,
        )
        _write_json(training / f"metadata_T{horizon}.json", head_metadata)
    report = {"status": "passed", "stages": {"models": {"horizons": list(horizons)}}}
    receipt = build_evidence_receipt(
        report, scope="models", repo_root=tmp_path, data_dir=data,
        training_dir=training, models_dir=models, forecast_path=None, horizons=horizons,
    )
    report["evidence_receipt"] = receipt
    report_path = data / "validation/models.json"
    receipt_path = data / "validation/models.receipt.json"
    _write_json(report_path, report)
    _write_json(receipt_path, receipt)
    bundle, manifest = create_signed_bundle(
        models, models / "bundles", receipt_path=receipt_path,
        validation_report_path=report_path, source_revision="accepted-options-test", horizons=horizons,
    )
    identity = manifest["bundle_id"]
    _write_json(models / "control/champion.json", create_signed_control_pointer(
        bundle_id=identity, previous_bundle_id=None, decision={"reason": "existing champion"},
    ))
    _write_json(models / "control/registry.json", create_signed_registry(
        champion_bundle_id=identity, challenger_bundle_id=None, previous_bundle_id=None,
        decision={"reason": "no activation in daily refresh"},
    ))
    return bundle


@pytest.fixture
def accepted_options(tmp_path, monkeypatch, request):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode())
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode())
    # Freeze one real calendar day before the US session opens; all consumers use
    # one instant, including scored_at and the monitor's prospective checks.
    now = (datetime.fromisoformat(request.param) if hasattr(request, "param") else
           datetime.combine(datetime.now(timezone.utc).date(), time(7), tzinfo=timezone.utc))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    for module in (daily_score, control_plane, model_control, model_publication,
                   validate_ml_pipeline, live_validation, pipeline_validation):
        monkeypatch.setattr(module, "datetime", Clock)
    latest = latest_completed_us_market_session(now)
    previous = latest - timedelta(days=7)
    event = latest + timedelta(days=7)
    good = [f"FRESH{i:03}" for i in range(100)]
    symbols = [*good, "CROSSED", "OFFHEAD"]
    data = tmp_path / "data"
    data.mkdir()
    bundle = _signed_champion(data, tmp_path)
    prices = [
        {"act_symbol": symbol, "date": source, "open": 100., "high": 101.,
         "low": 99., "close": 100., "volume": 100000}
        for symbol in [*symbols, "SPY", "TLT"] for source in (previous, latest)
    ]
    _write_parquet(data, "ohlcv", latest, prices)
    pd.DataFrame([
        {"act_symbol": symbol, "date": event if symbol != "OFFHEAD" else latest + timedelta(days=4), "timing": "bmo"}
        for symbol in symbols
    ]).to_parquet(data / "earnings_calendar.parquet", index=False)
    for source, mid in ((previous, 1.), (latest, 2.)):
        rows = []
        for symbol in symbols:
            for side in ("Call", "Put"):
                crossed = symbol == "CROSSED" and source == latest
                rows.append({
                    "act_symbol": symbol, "date": source, "expiration": event + timedelta(days=7),
                    "strike": 100., "call_put": side, "bid": mid + .2 if crossed else mid - .05,
                    "ask": mid - .2 if crossed else mid + .05, "vol": .5,
                    "delta": .5 if side == "Call" else -.5, "gamma": .1,
                    "theta": -.1, "vega": .1, "rho": .01,
                })
        _write_parquet(data, "options_chain", source, rows)
    vix = data / "parquet/vix/vix.parquet"
    vix.parent.mkdir(parents=True)
    pd.DataFrame({"date": [previous, latest], "vix_close": [20., 20.]}).to_parquet(vix, index=False)
    # These quote/publication tests inject independently verified source
    # coverage at the view boundary; receipt reconstruction has its own suite.
    from scripts import setup_duckdb_from_parquet as source_views
    def action_coverage(connection, _data_dir):
        values = ",".join(f"('{symbol}', DATE '2019-01-01', DATE '{latest}')" for symbol in symbols)
        connection.execute(f"""CREATE OR REPLACE VIEW v_corporate_action_coverage AS
            SELECT * FROM (VALUES {values}) AS coverage(act_symbol,window_start,window_end)""")
    monkeypatch.setattr(source_views, "setup_corporate_action_coverage", action_coverage)
    import event_forecast_eligibility as eligibility
    monkeypatch.setattr(eligibility, "_current_source_coverage",
                        lambda _bundle: {symbol: ("2019-01-01", latest.isoformat()) for symbol in symbols})
    conn = duckdb.connect()
    setup_views(conn, data)
    conn.execute("""
        CREATE VIEW v_volhist AS
        SELECT NULL::DATE AS date, NULL::VARCHAR AS act_symbol,
               NULL::DOUBLE AS hv_current, NULL::DOUBLE AS hv_year_high, NULL::DOUBLE AS hv_year_low,
               NULL::DOUBLE AS iv_current, NULL::DOUBLE AS iv_week_ago, NULL::DOUBLE AS iv_month_ago,
               NULL::DOUBLE AS iv_year_high, NULL::DOUBLE AS iv_year_low WHERE FALSE
    """)
    quality = _quote_quality(conn, now=now)
    coverage = _event_coverage(conn, days_ahead=21)
    assert quality["status"] == "passed"
    assert coverage["status"] == "passed"
    assert coverage["horizon_coverage"]["status"] == "passed"
    manifest = {
        "schema": "quantiv.data-reconciliation.v2", "manifest_id": "sha256:accepted-integration",
        "generated_at": now.isoformat(), "quality": {"decision_safe": True, "critical_exceptions": 0},
        "source_reconciliation": {"source_date": latest.isoformat()},
        "quote_quality": quality, "event_coverage": coverage, "exceptions": [],
    }
    manifest_path = data / "validation/data_reconciliation.json"
    _write_json(manifest_path, manifest)
    decision = finalize_snapshot(manifest_path=manifest_path, data_dir=data, not_before=now.isoformat())
    assert decision.state == "accepted" and decision.can_score
    controls = {name: (data / "models/control" / name).read_bytes()
                for name in ("champion.json", "registry.json")}
    yield {"data": data, "bundle": bundle, "conn": conn, "latest": latest,
           "previous": previous, "now": now, "event": event, "symbols": good,
           "controls": controls, "manifest": manifest_path}
    conn.close()


def test_signed_monitor_binds_the_current_immutable_data_release(accepted_options):
    store = accepted_options
    release_id = "c" * 64
    _write_json(store["data"] / "control/current_data_release.json", {
        "release_id": release_id, "manifest": f"control/releases/{release_id}.json",
    })
    frame = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    _, _, report, candidate = _monitor_fixture_rows(store, frame)
    assert report["current_data_release_id"] == release_id
    _write_json(store["data"] / "control/current_data_release.json", {
        "release_id": "d" * 64, "manifest": f"control/releases/{'d' * 64}.json",
    })
    with pytest.raises(RuntimeError, match="data release"):
        model_publication.verify_publication(store["data"], forecast_path=candidate)


def _monitor_fixture_rows(store, frame, *, database=None, production=False):
    data = store["data"]
    forecast = data / "validation/scored.parquet"
    archive = data / "forecasts/event_forecast_archive.parquet"
    archive.parent.mkdir(exist_ok=True)
    # A real retained artifact is captured before this run scores new rows.
    pd.DataFrame({"act_symbol": ["PREVIOUS"], "earnings_date": [pd.Timestamp("2026-09-01")],
                  "snapshot_date": [pd.Timestamp("2026-08-31")], "em_ml_pct": [.03]}).to_parquet(archive, index=False)
    model_publication.snapshot_published_forecasts(data)
    forecast = daily_score.save_forecasts(
        frame, data, output_path=None if production else forecast,
    )
    result = control_plane.monitor(argparse.Namespace(
        models_root=data / "models", forecast_dir=data / "forecasts",
        forecast_path=forecast, days_ahead=21, database=database,
    ))
    policy = model_publication.verify_publication(data, not_before=store["now"].isoformat(), forecast_path=forecast)
    report = json.loads((data / "models/monitoring/latest_monitoring.json").read_text())
    receipt = json.loads((data / "models/monitoring/latest_monitoring.receipt.json").read_text())
    verify_monitor_receipt(receipt, ledger_path=data / "models/monitoring/prediction_ledger.parquet",
                          report_path=data / "models/monitoring/latest_monitoring.json")
    return result, policy, report, forecast


def _assert_controls_unchanged(store):
    root = store["data"] / "models/control"
    for name, before in store["controls"].items():
        assert (root / name).read_bytes() == before
    pointer = verify_control_pointer(json.loads((root / "champion.json").read_text()))
    registry = verify_registry(json.loads((root / "registry.json").read_text()))
    assert pointer["champion_bundle_id"] == store["bundle"].name == registry["champion_bundle_id"]
    assert registry["challenger_bundle_id"] is None


def test_accepted_fresh_quotes_reach_signed_champion_and_public_forecast(accepted_options, monkeypatch):
    store = accepted_options
    frame = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    assert set(frame["act_symbol"]) == set(store["symbols"])
    assert len(frame) == 100
    assert set(pd.to_datetime(frame["snapshot_date"]).dt.date) == {store["latest"]}
    assert set(frame["model_horizon"]) == {7}
    assert all(json.loads(value)["straddle_pct"] == pytest.approx(.04) for value in frame["feature_vector"])
    assert np.allclose(frame["em_math_pct"], .04)
    model = daily_score.load_models(store["bundle"])[7]
    old_quotes = pd.DataFrame([json.loads(value) for value in frame["feature_vector"]])
    old_quotes["straddle_pct"] = .02
    assert not np.allclose(frame["em_ml_pct"], model["model"].predict(old_quotes[model["feature_cols"]]))
    result, policy, report, forecast = _monitor_fixture_rows(store, frame, production=True)
    assert result == 0 and policy["can_publish_ml"] is True
    assert report["feature_drift"]["rolling_evidence"]["current_snapshot_fresh"] is True
    assert report["feature_drift"]["horizons"]["7"]["rows"] == 100
    assert report["champion_bundle_id"] == store["bundle"].name
    forecast = Path(policy["eligible_forecast_path"])
    # The frontend consumes the same eligible scored rows; stale T14 and the
    # off-head event must never be substituted for this current T7 prediction.
    forecast_dir = store["data"] / "forecasts"
    eligible = pd.read_parquet(forecast_dir / "event_prediction_ledger.parquet")
    eligible = eligible.loc[eligible["freeze_eligible"].fillna(False)]
    assert len(eligible) == 100
    assert len(pd.read_parquet(forecast_dir / "event_forecast_archive.parquet")) == 100
    receipt_dir = forecast_dir / "receipts"
    monkeypatch.setattr("sys.argv", [
        "validate_ml_pipeline.py", "forecasts", "--data-dir", str(store["data"]),
        "--forecast-path", str(forecast), "--report", str(store["data"] / "validation/daily_forecasts.json"),
        "--publish-receipt-dir", str(receipt_dir),
    ])
    assert validate_ml_pipeline.main() == 0
    receipt_path = receipt_dir / "latest_forecasts.json"
    receipt = verify_evidence_receipt(json.loads(receipt_path.read_text()), expected_scope="forecasts")
    assert receipt["quality"]["status"] == "passed"
    forecast_receipt = next(item for item in receipt["artifacts"] if item["name"] == "forecast_snapshot")
    assert forecast_receipt["members"][0]["path"].endswith(forecast.name)
    public_dir = store["data"] / "public"
    monkeypatch.setattr(frontend_shared, "PUBLIC_DIR", public_dir)
    monkeypatch.setattr(forecast_artifacts, "PUBLIC_DIR", public_dir)
    monkeypatch.setattr(forecast_artifacts, "FORECAST_RECEIPT_PATH", receipt_path)
    assert forecast_artifacts.publish_forecast_evidence(expected_forecast_path=forecast) is True
    evidence = json.loads((public_dir / "evidence/forecast.json").read_text())
    assert evidence["receipt_id"] == receipt["receipt_id"]
    assert evidence["quality"]["status"] == "passed"
    assert evidence["coverage"]["rows"] == 100
    monkeypatch.setattr(forecast_artifacts, "EVENT_PREDICTION_LEDGER_PATH", forecast_dir / "event_prediction_ledger.parquet")
    public = forecast_artifacts.load_ml_forecasts(
        forecast_path=forecast, eligible_forecast_ids=set(policy["eligible_forecast_ids"]),
        eligible_event_keys=set(policy["eligible_event_keys"]),
    )
    assert len(public) == 100
    published = forecast_artifacts.ml_fields(public[store["symbols"][0], store["event"].isoformat()])
    assert pd.Timestamp(published["ml_snapshot_date"]).date() == store["latest"]
    assert published["model_horizon"] == 7
    assert published["em_ml_pct"] == pytest.approx(frame.iloc[0]["em_ml_pct"], abs=5e-7)
    record_publications({row["forecast_id"] for row in public.values()}, forecast_dir, published_at=store["now"])
    publications = pd.read_parquet(forecast_dir / "event_prediction_publications.parquet")
    assert len(publications) == 100
    assert set(publications["forecast_id"]) == set(eligible["forecast_id"])
    _assert_controls_unchanged(store)


@pytest.mark.parametrize("mutation", ["stale", "corruption", "sparse"])
def test_signed_current_forecast_holds_survive_repaired_live_scoring(accepted_options, mutation):
    store = accepted_options
    frame = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    # Isolate each existing guard from the repaired live-query behavior.
    frame = frame.loc[pd.to_datetime(frame["snapshot_date"]).dt.date.eq(store["latest"]) & frame["em_math_pct"].notna()].copy()
    if mutation == "stale":
        frame["snapshot_date"] = store["previous"]
    elif mutation == "corruption":
        frame["feature_vector"] = json.dumps({"straddle_pct": None})
    elif mutation == "sparse":
        frame = frame.iloc[:19].copy()
    result, policy, report, _ = _monitor_fixture_rows(store, frame)
    assert result == 1 and policy["can_publish_ml"] is False
    assert report["status"] == "failed"
    expected = {"sparse": "insufficient_data"}.get(mutation, "critical")
    assert report["feature_drift"]["status"] == expected
    assert forecast_artifacts.load_ml_forecasts(publication_held=True) == {}
    assert (model_publication.backup_dir(store["data"]) / "event_forecast_archive.parquet").read_bytes() == (
        store["data"] / "forecasts/event_forecast_archive.parquet").read_bytes()
    _assert_controls_unchanged(store)


def test_sparse_fresh_options_publish_only_valid_current_champion_events(accepted_options, monkeypatch):
    """Exercise real quote views, signed selection, full validation and public cleanup."""
    store = accepted_options
    data, latest = store["data"], store["latest"]
    partition = data / f"parquet/options_chain/year={latest.year}/month={latest.month:02}/{latest}.parquet"
    quotes = pd.read_parquet(partition)
    rejected = quotes.loc[quotes["act_symbol"].eq("CROSSED")]
    # These current-source events are in the options universe, but each actual
    # crossed quote fails the unchanged individual contract.
    bad_symbols = [f"REJECTED{i:03}" for i in range(100)]
    extra_quotes = [rejected.assign(act_symbol=symbol) for symbol in bad_symbols]
    pd.concat([quotes, *extra_quotes], ignore_index=True).to_parquet(partition, index=False)
    calendar = pd.read_parquet(data / "earnings_calendar.parquet")
    extra_events = pd.DataFrame({"act_symbol": bad_symbols, "date": store["event"], "timing": "bmo"})
    pd.concat([calendar, extra_events], ignore_index=True).to_parquet(data / "earnings_calendar.parquet", index=False)
    setup_views(store["conn"], data)
    quality = _quote_quality(store["conn"], now=store["now"])
    coverage = _event_coverage(store["conn"], days_ahead=21)
    assert quality["source_date"] == latest.isoformat() and not quality["errors"]
    assert coverage["coverage_pct"] < .65 and coverage["status"] == "failed"
    manifest = build_reconciliation_manifest(
        generated_at=store["now"].isoformat(),
        datasets={"options": {"status": "passed", "lag_days": 0, "max_lag_days": 0}},
        event_coverage=coverage, quote_quality=quality,
        duplicates={"options": {"duplicate_rows": 0}}, symbol_mappings={},
        corporate_actions={"continuity_status": "enforced"},
        pipeline_controls={"quarantine": {"status": "enforced"}, "idempotent_replay": {"status": "verified"}},
        source_reconciliation={"status": "passed", "source_date": latest.isoformat()},
    )
    assert manifest["quality"]["decision_safe"] is True
    assert manifest["quality"]["model_activation_eligible"] is False
    assert not any(item["severity"] == "critical" for item in manifest["exceptions"])
    _write_json(store["manifest"], manifest)
    decision = finalize_snapshot(manifest_path=store["manifest"], data_dir=data, not_before=store["now"].isoformat())
    assert decision.state == "accepted" and decision.active_source_date == latest.isoformat()
    with pytest.raises(RuntimeError, match="model activation is blocked"):
        verify_retrain_data_gate(data_dir=data, now=store["now"])
    good = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    assert set(good["act_symbol"]) == set(store["symbols"])
    assert set(pd.to_datetime(good["snapshot_date"]).dt.date) == {latest}
    unsupported = good.iloc[:1].copy()
    unsupported["act_symbol"] = "UNSUPPORTED"
    unsupported["em_math_pct"] = np.nan
    unsupported["__cohort"] = "optionless"
    vector = json.loads(unsupported.iloc[0]["feature_vector"])
    vector["straddle_pct"] = None
    unsupported["feature_vector"] = json.dumps(vector)
    result, policy, report, candidate = _monitor_fixture_rows(store, pd.concat([good, unsupported]))
    assert result == 1 and policy["state"] == "partial" and policy["can_publish_ml"]
    assert report["feature_drift"]["status"] == "unsupported_cohort"
    selected = Path(policy["eligible_forecast_path"])
    assert set(pd.read_parquet(selected)["act_symbol"]) == set(store["symbols"])
    receipt_dir = data / "forecasts/receipts"
    monkeypatch.setattr("sys.argv", [
        "validate_ml_pipeline.py", "forecasts", "--data-dir", str(data),
        "--forecast-path", str(selected), "--report", str(data / "validation/daily_forecasts.json"),
        "--publish-receipt-dir", str(receipt_dir),
    ])
    assert validate_ml_pipeline.main() == 0
    receipt_path = receipt_dir / "latest_forecasts.json"
    public_dir = data / "public"
    monkeypatch.setattr(frontend_shared, "PUBLIC_DIR", public_dir)
    monkeypatch.setattr(forecast_artifacts, "PUBLIC_DIR", public_dir)
    monkeypatch.setattr(forecast_artifacts, "FORECAST_RECEIPT_PATH", receipt_path)
    assert forecast_artifacts.publish_forecast_evidence(expected_forecast_path=selected)
    with pytest.raises(ValueError, match="forecast|receipt"):
        forecast_artifacts.publish_forecast_evidence(expected_forecast_path=candidate)
    # A held event's older ledger forecast cannot appear through the subset loader.
    old = daily_score.save_forecasts(unsupported.assign(em_ml_pct=.03), data,
                                    output_path=data / "validation/old-unpublished.parquet")
    monkeypatch.setattr(forecast_artifacts, "EVENT_PREDICTION_LEDGER_PATH", old)
    public = forecast_artifacts.load_ml_forecasts(
        forecast_path=selected, eligible_forecast_ids=set(policy["eligible_forecast_ids"]),
        eligible_event_keys=set(policy["eligible_event_keys"]),
    )
    assert len(public) == 100 and ("UNSUPPORTED", store["event"].isoformat()) not in public
    first = store["symbols"][0]
    current = {"ticker": first, "earnings_date": store["event"].isoformat(),
               **forecast_artifacts.ml_fields(public[first, store["event"].isoformat()])}
    held = {"ticker": "UNSUPPORTED", "earnings_date": store["event"].isoformat(), "em_ml_pct": .03}
    unscored = {"ticker": "CROSSED", "earnings_date": store["event"].isoformat(), "em_ml_pct": .04}
    reported = {"ticker": "PREVIOUS", "earnings_date": store["previous"].isoformat(), "em_ml_pct": .02}
    _write_json(public_dir / "weekly.json", {"events": [current, held, unscored, reported]})
    forecast_artifacts.withhold_upcoming_ml(
        public_dir, today=store["now"].date(), eligible_forecast_ids=set(policy["eligible_forecast_ids"]),
        eligible_event_keys=set(policy["eligible_event_keys"]), align_components=False,
    )
    events = json.loads((public_dir / "weekly.json").read_text())["events"]
    assert events[0]["ml_publication_status"] == "eligible" and events[0]["em_ml_pct"] > 0
    assert all(item["ml_publication_status"] == "held" and "em_ml_pct" not in item for item in events[1:3])
    assert events[3]["em_ml_pct"] == .02
    _assert_controls_unchanged(store)


def test_stale_source_and_tampered_signed_evidence_remain_unpublishable(accepted_options):
    store = accepted_options
    later = store["now"] + timedelta(days=7)
    assert _quote_quality(store["conn"], now=later)["status"] == "failed"
    frame = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    frame = frame.loc[pd.to_datetime(frame["snapshot_date"]).dt.date.eq(store["latest"]) & frame["em_math_pct"].notna()].copy()
    _, _, _, forecast = _monitor_fixture_rows(store, frame)
    report = store["data"] / "models/monitoring/latest_monitoring.json"
    report.write_text("{}")
    with pytest.raises(ModelBundleError, match="digest|size"):
        model_publication.verify_publication(store["data"], forecast_path=forecast)
    _assert_controls_unchanged(store)


@pytest.mark.parametrize("prior_observations", [False, True])
def test_no_current_exact_head_has_signed_hold_without_reusing_old_forecast(
    accepted_options, prior_observations,
):
    store = accepted_options
    ledger_before = None
    if prior_observations:
        current = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
        result, policy, _, _ = _monitor_fixture_rows(store, current)
        assert result == 0 and policy["can_publish_ml"]
        ledger_before = pd.read_parquet(store["data"] / "models/monitoring/prediction_ledger.parquet")
        assert len(ledger_before) == 100
    pd.DataFrame([{
        "act_symbol": "OFFHEAD", "date": store["latest"] + timedelta(days=4), "timing": "bmo",
    }]).to_parquet(store["data"] / "earnings_calendar.parquet", index=False)
    frame = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    assert frame.empty
    result, policy, report, forecast = _monitor_fixture_rows(store, frame)
    assert pd.read_parquet(forecast).empty
    assert result == 1 and policy["can_publish_ml"] is False
    assert report["forecast_rows"] == 0
    assert report["snapshot_date"] == store["latest"].isoformat()
    assert report["feature_drift"]["status"] == "insufficient_data"
    assert report["feature_drift"]["reason"] == "no_eligible_current_forecasts"
    assert report["feature_drift"]["rolling_evidence"] == {
        "current_rows": 0, "current_snapshot_fresh": False,
    }
    ledger_after = pd.read_parquet(store["data"] / "models/monitoring/prediction_ledger.parquet")
    if ledger_before is not None:
        pd.testing.assert_frame_equal(ledger_before, ledger_after)
        assert report["ledger_rows"] == 100
    else:
        assert ledger_after.empty and report["ledger_rows"] == 0
    retained = store["data"] / "forecasts/event_forecast_archive.parquet"
    assert retained.read_bytes() == (model_publication.backup_dir(store["data"]) / retained.name).read_bytes()
    _assert_controls_unchanged(store)


def test_empty_current_forecast_cannot_replace_tampered_prior_ledger(accepted_options):
    store = accepted_options
    current = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    _monitor_fixture_rows(store, current)
    ledger_path = store["data"] / "models/monitoring/prediction_ledger.parquet"
    ledger = pd.read_parquet(ledger_path)
    ledger.loc[0, "prediction"] += .1
    ledger.to_parquet(ledger_path, index=False)
    monitoring = store["data"] / "models/monitoring"
    report_before = (monitoring / "latest_monitoring.json").read_bytes()
    receipt_before = (monitoring / "latest_monitoring.receipt.json").read_bytes()
    with pytest.raises(ModelBundleError, match="digest|size"):
        _monitor_fixture_rows(store, pd.DataFrame())
    assert (monitoring / "latest_monitoring.json").read_bytes() == report_before
    assert (monitoring / "latest_monitoring.receipt.json").read_bytes() == receipt_before
    _assert_controls_unchanged(store)


def test_malformed_empty_artifact_does_not_create_a_signed_hold(accepted_options):
    store = accepted_options
    forecast = store["data"] / "validation/malformed.parquet"
    pd.DataFrame(columns=["model_bundle_id"]).to_parquet(forecast, index=False)
    with pytest.raises(ValueError, match="forecast artifact lacks required columns"):
        control_plane.monitor(argparse.Namespace(
            models_root=store["data"] / "models", forecast_dir=store["data"] / "forecasts",
            forecast_path=forecast, days_ahead=21,
        ))
    assert not (store["data"] / "models/monitoring/latest_monitoring.receipt.json").exists()
    _assert_controls_unchanged(store)


def test_empty_causal_champion_records_genuine_shadow_without_paired_comparison(
    accepted_options, tmp_path, monkeypatch,
):
    store = accepted_options
    protocol = "quantiv.earnings-causal.v2"
    bundles = {}
    for name, cohorts in (("champion", ["strict_options"]),
                          ("previous", ["strict_options", "optionless"])):
        root = tmp_path / name
        root.mkdir()
        data = root / "data"
        data.mkdir()
        bundle = _signed_champion(data, root, metadata_overrides={
            "feature_protocol": protocol, "target_protocol": "quantiv.session-reaction.v2",
            "supported_cohorts": cohorts,
        })
        destination = store["data"] / "models/bundles" / bundle.name
        shutil.copytree(bundle, destination)
        bundles[name] = destination
    control = store["data"] / "models/control"
    _write_json(control / "champion.json", create_signed_control_pointer(
        bundle_id=bundles["champion"].name, previous_bundle_id=bundles["previous"].name,
        decision={"reason": "existing causal champion"},
    ))
    _write_json(control / "registry.json", create_signed_registry(
        champion_bundle_id=bundles["champion"].name, challenger_bundle_id=None,
        previous_bundle_id=bundles["previous"].name,
        decision={"reason": "existing previous model retained for shadow"},
    ))
    store["bundle"] = bundles["champion"]
    store["controls"] = {name: (control / name).read_bytes()
                         for name in ("champion.json", "registry.json")}
    pd.DataFrame([{"act_symbol": "CROSSED", "date": store["event"], "timing": "bmo"}]).to_parquet(
        store["data"] / "earnings_calendar.parquet", index=False,
    )
    database = store["data"] / "shadow.duckdb"
    with duckdb.connect(str(database)) as connection:
        setup_views(connection, store["data"])
        connection.execute("""
            CREATE VIEW v_volhist AS SELECT NULL::DATE AS date, NULL::VARCHAR AS act_symbol,
                NULL::DOUBLE AS hv_current, NULL::DOUBLE AS hv_year_high,
                NULL::DOUBLE AS hv_year_low, NULL::DOUBLE AS iv_current,
                NULL::DOUBLE AS iv_week_ago, NULL::DOUBLE AS iv_month_ago,
                NULL::DOUBLE AS iv_year_high, NULL::DOUBLE AS iv_year_low WHERE FALSE
        """)
        champion = daily_score.get_bundle_forecasts(connection, bundles["champion"], 21)
        shadow = daily_score.get_bundle_forecasts(connection, bundles["previous"], 21)
    assert champion.empty
    assert len(shadow) == 1 and shadow.iloc[0]["__cohort"] == "optionless"
    assert shadow.iloc[0]["feature_protocol"] == protocol
    # The monitor imports the scorer through its command-line module name.
    import daily_score as command_score
    monkeypatch.setattr(command_score, "datetime", daily_score.datetime)
    result, policy, report, _ = _monitor_fixture_rows(store, champion, database=database)
    assert result == 1 and policy["can_publish_ml"] is False
    assert report["feature_drift"]["reason"] == "no_eligible_current_forecasts"
    assert report["shadow_scoring"]["previous"]["status"] == "insufficient_data"
    ledger = pd.read_parquet(store["data"] / "models/monitoring/prediction_ledger.parquet")
    assert ledger["act_symbol"].tolist() == ["CROSSED"]
    assert ledger["role"].tolist() == ["previous"]
    assert ledger["bundle_id"].tolist() == [bundles["previous"].name]
    assert ledger["feature_protocol"].tolist() == [protocol]
    assert ledger.iloc[0]["feature_vector"] == shadow.iloc[0]["feature_vector"]
    _assert_controls_unchanged(store)


def test_rejected_current_quotes_do_not_become_strict_champion_features(accepted_options):
    store = accepted_options
    data = store["data"]
    latest = store["latest"]
    candidate = data / "parquet/options_chain" / f"year={latest.year}" / f"month={latest.month:02}" / f"{latest}.parquet"
    rows = pd.read_parquet(candidate)
    rows["bid"] = 3.
    rows["ask"] = 1.
    rows.to_parquet(candidate, index=False)
    quality = _quote_quality(store["conn"], now=store["now"])
    assert quality["status"] == "failed"
    assert store["conn"].execute("SELECT COUNT(*) FROM v_straddle_features WHERE date = ?", [latest]).fetchone()[0] == 0
    # Existing resilience restores its prior published options partition. This
    # read-only test release identifies that partition without touching models.
    previous = store["previous"]
    prior_relative = f"parquet/options_chain/year={previous.year}/month={previous.month:02}/{previous}.parquet"
    _write_json(data / "control/releases/prior.json", {"files": [{"path": prior_relative}]})
    _write_json(data / "control/current_data_release.json", {"manifest": "control/releases/prior.json"})
    manifest = json.loads(store["manifest"].read_text())
    manifest["quote_quality"] = quality
    manifest["quality"] = {"decision_safe": False, "critical_exceptions": 1}
    manifest["exceptions"] = [{"severity": "critical", "code": "option_quote_quality_below_limit", "summary": "crossed quotes"}]
    _write_json(store["manifest"], manifest)
    decision = finalize_snapshot(manifest_path=store["manifest"], data_dir=data, not_before=store["now"].isoformat())
    assert decision.state == "fallback" and not decision.can_score
    assert not candidate.exists()
    setup_views(store["conn"], data)
    frame = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    assert frame.empty
    result, policy, _, _ = _monitor_fixture_rows(store, frame)
    assert result == 1 and not policy["can_publish_ml"]
    _assert_controls_unchanged(store)
