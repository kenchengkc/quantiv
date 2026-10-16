import json
import io
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException

from apps.backend.tests.test_ml_predict_router import (
    _PredictionCache, _active_test_models, _cached_prediction, _Pool, _StatusConn,
)
from models import MLPredictRequest
from routers import ml_predict
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ml.model_bundle import create_signed_monitor_receipt, ModelBundleError
from services import publication_authority as authority
from ml.forecast_snapshot import forecast_snapshot_id


NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


@pytest.fixture
def publication(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    monkeypatch.setenv("R2_BUCKET", "test")
    objects = {"control/current_data_release.json": json.dumps({
        "release_id": "c" * 64, "manifest": f"control/releases/{'c' * 64}.json",
    }).encode()}

    class Client:
        def get_object(self, *, Bucket, Key):
            return {"Body": io.BytesIO(objects[Key])}

    monkeypatch.setattr(authority.r2_models, "_build_client", lambda: Client())
    ledger = tmp_path / "prediction_ledger.parquet"
    ledger.write_bytes(b"retained authenticated ledger")
    report_path = tmp_path / "latest_monitoring.json"

    def publish(*, bundle_id="a" * 64, events=("A|2026-10-21",), snapshot="2026-10-14", horizon=7, **changes):
        bindings = [forecast_snapshot_id({"act_symbol": event.split("|")[0], "earnings_date": event.split("|")[1],
            "snapshot_date": snapshot, "model_horizon": horizon, "model_bundle_id": bundle_id,
            "scored_at": NOW, "spot_price": 100., "feature_vector": {"log_spot": 4.6}}) for event in events]
        report = {"schema": "quantiv.model-monitoring.v1", "status": "passed" if events else "failed",
                  "monitored_at": NOW.isoformat(), "snapshot_date": snapshot,
                  "champion_bundle_id": bundle_id, "current_data_release_id": "c" * 64,
                  "publication_eligibility": {"schema": "quantiv.event-forecast-eligibility.v1",
                                              "eligible_event_keys": list(events), "eligible_snapshot_ids": bindings,
                                              "eligible_snapshot_bindings": {f"{event}|{horizon}": binding for event, binding in zip(events, bindings)}}, **changes}
        report_path.write_text(json.dumps(report))
        receipt = create_signed_monitor_receipt(ledger_path=ledger, report_path=report_path,
            snapshot_date=snapshot, private_key=private)
        objects["models/monitoring/latest_monitoring.json"] = report_path.read_bytes()
        objects["models/monitoring/latest_monitoring.receipt.json"] = json.dumps(receipt).encode()
    publish()
    return objects, publish


@pytest.mark.asyncio
async def test_missing_current_authority_cannot_serve_cached_legacy_prediction(monkeypatch):
    _, bundles = _active_test_models(monkeypatch)
    monkeypatch.delenv("R2_BUCKET", raising=False)
    request = MLPredictRequest(symbol="A", horizon_days=7)
    cache = _PredictionCache(pause_at=None)
    cache.data[ml_predict._cache_key(request)] = _cached_prediction(bundles["a"], .01)
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": cache})
    with pytest.raises(HTTPException) as exc:
        await ml_predict._predict_response(request)
    assert exc.value.status_code == 503


@pytest.mark.parametrize("mutation", ["signature", "report", "stale", "source", "bundle", "missing"])
def test_current_authority_rejects_missing_tampered_stale_or_mismatched_evidence(publication, mutation):
    objects, publish = publication
    if mutation == "signature":
        receipt = json.loads(objects["models/monitoring/latest_monitoring.receipt.json"])
        receipt["signature"]["value"] = "a" * 88
        objects["models/monitoring/latest_monitoring.receipt.json"] = json.dumps(receipt).encode()
    elif mutation == "report":
        objects["models/monitoring/latest_monitoring.json"] += b" "
    elif mutation == "stale":
        publish(snapshot="2026-10-12")
    elif mutation == "source":
        objects["control/current_data_release.json"] = json.dumps({
            "release_id": "d" * 64, "manifest": f"control/releases/{'d' * 64}.json"}).encode()
    elif mutation == "bundle":
        publish(bundle_id="b" * 64)
    else:
        del objects["models/monitoring/latest_monitoring.receipt.json"]
    with pytest.raises((ModelBundleError, ValueError, KeyError)):
        authority.load_current_authority("a" * 64, now=NOW)


def test_authority_accepts_one_session_grace_without_redating_or_wrong_head(publication):
    _, publish = publication
    publish(snapshot="2026-10-13", events=("A|2026-10-20",))
    current = authority.load_current_authority("a" * 64, now=NOW)
    assert current.snapshot_date == date(2026, 10, 13)
    assert current.allows("A", 7, date(2026, 10, 20))
    assert not current.allows("A", 6, date(2026, 10, 20))
    with pytest.raises(ModelBundleError):
        authority.load_current_authority("a" * 64, now=datetime(2026, 10, 15, 20, tzinfo=timezone.utc))


def test_rollback_requires_a_fresh_monitor_for_the_restored_bundle(publication):
    _, publish = publication
    with pytest.raises(ModelBundleError):
        authority.load_current_authority("b" * 64, now=NOW)
    publish(bundle_id="b" * 64)
    current = authority.load_current_authority("b" * 64, now=NOW)
    assert current.allows("A", 7, date(2026, 10, 21))
    assert not current.accepts_snapshot("A", 7, {"snapshot_date": "2026-10-13", "earnings_date": "2026-10-21"})


def test_grace_cannot_serve_an_event_from_a_previous_calendar_day(publication):
    _, publish = publication
    publish(events=("A|2026-10-15",), horizon=1)
    current = authority.load_current_authority("a" * 64, now=NOW)
    assert current.allows("A", 1, date(2026, 10, 15))
    next_day = authority.load_current_authority("a" * 64,
        now=datetime(2026, 10, 16, 4, tzinfo=timezone.utc))
    assert next_day.snapshot_date == date(2026, 10, 14)
    assert not next_day.allows("A", 1, date(2026, 10, 15))


def test_same_session_old_neon_vector_is_not_new_publication_evidence(publication):
    current = authority.load_current_authority("a" * 64, now=NOW)
    old = {"snapshot_date": date(2026, 10, 14), "earnings_date": date(2026, 10, 21),
           "feature_vector": {"log_spot": 1.}, "model_bundle_id": "a" * 64,
           "spot_at_snapshot": 100., "forecast_scored_at": NOW}
    assert not current.accepts_snapshot("A", 7, old)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["quarantine", "source"])
async def test_same_day_source_or_quarantine_change_revokes_cached_legacy_prediction(publication, monkeypatch, mutation):
    objects, publish = publication
    _, bundles = _active_test_models(monkeypatch)

    async def current(bundle_id):
        try:
            return authority.load_current_authority(bundle_id, now=NOW)
        except Exception as exc:
            raise HTTPException(status_code=503) from exc

    monkeypatch.setattr(ml_predict, "_publication_authority", current)
    request = MLPredictRequest(symbol="A", horizon_days=7, earnings_date=date(2026, 10, 21))
    cache = _PredictionCache(pause_at=None)
    admitted = authority.load_current_authority("a" * 64, now=NOW)
    payload = json.loads(_cached_prediction(bundles["a"], .01))
    payload.update(feature_snapshot_date="2026-10-14", earnings_date="2026-10-21")
    payload["_snapshot_id"] = next(iter(admitted.eligible_snapshot_ids))
    cache.data[ml_predict._cache_key(request, publication_id=admitted.cache_identity)] = json.dumps(payload)
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": cache})
    assert (await ml_predict._predict_response(request)).source == "cached"
    if mutation == "quarantine":
        publish(events=())
    else:
        objects["control/current_data_release.json"] = json.dumps({
            "release_id": "d" * 64, "manifest": f"control/releases/{'d' * 64}.json"}).encode()
    with pytest.raises(HTTPException) as exc:
        await ml_predict._predict_response(request)
    assert exc.value.status_code == (404 if mutation == "quarantine" else 503)
