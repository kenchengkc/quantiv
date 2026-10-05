"""Route-level tests for ML coverage and batch prediction endpoints."""

import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))

from models import (  # noqa: E402
    MLBatchPredictRequest,
    MLCoverageRequest,
    MLPredictRequest,
    MLPredictResponse,
    MLStatusRequest,
)
from routers import ml_predict  # noqa: E402


def test_prediction_cache_key_changes_when_active_bundle_changes(monkeypatch):
    req = MLPredictRequest(symbol="A", horizon_days=7)
    monkeypatch.setattr(ml_predict.predict_service, "get_bundle", lambda _: SimpleNamespace(bundle_id="a" * 64))
    old_key = ml_predict._cache_key(req)
    monkeypatch.setattr(ml_predict.predict_service, "get_bundle", lambda _: SimpleNamespace(bundle_id="b" * 64))
    assert ml_predict._cache_key(req) != old_key


class _PredictionCache:
    """Pause a real cache boundary while a model activation occurs."""
    def __init__(self, *, pause_at):
        self.data = {}
        self.pause_at = pause_at
        self.started = asyncio.Event()
        self.resume = asyncio.Event()
        self.paused = False

    async def _pause(self, operation):
        if operation == self.pause_at and not self.paused:
            self.paused = True
            self.started.set()
            await self.resume.wait()

    async def get(self, key):
        await self._pause("get")
        return self.data.get(key)

    async def setex(self, key, ttl, payload):
        await self._pause("set")
        self.data[key] = payload


def _active_test_models(monkeypatch):
    import pandas as pd
    from lightgbm import LGBMRegressor

    bundles = {}
    features = pd.DataFrame({"log_spot": [4.6] * 20})
    for name, value in (("a", .01), ("b", .09)):
        model = LGBMRegressor(n_estimators=1, n_jobs=1, verbose=-1).fit(features, [value] * len(features)).booster_
        bundles[name] = ml_predict.predict_service._ModelBundle(
            estimator=model, feature_names=["log_spot"],
            quantile_estimators={q: model for q in (10, 25, 50, 75, 90)},
            loaded_at=datetime.now(timezone.utc), model_version=name,
            model_trained_at=None, feature_schema_hash="fixture", val_mae=None,
            bundle_id=name * 64,
        )
    active = {"bundle": bundles["a"]}
    monkeypatch.setattr(ml_predict.predict_service, "get_bundle", lambda _: active["bundle"])
    return active, bundles


def _snapshot(bundle):
    return {
        "snapshot_date": date.today(), "earnings_date": date.today() + timedelta(days=7),
        "feature_vector": {"log_spot": 4.6}, "model_bundle_id": bundle.bundle_id,
        "spot_at_snapshot": 100., "snapshot_age_days": 0,
        "forecast_scored_at": datetime.now(timezone.utc),
    }


def _cached_prediction(bundle, value):
    response = MLPredictResponse(
        symbol="A", horizon_days=7, em_ml_pct=value, em_ml_abs=value * 100.,
        quantiles={q: value for q in (10, 25, 50, 75, 90)}, spot_used=100.,
        feature_snapshot_date=date.today().isoformat(), source="computed",
        inference_mode="snapshot_rescore",
        served_at=datetime.now(timezone.utc), model_version=bundle.model_version,
    ).model_dump(mode="json")
    return json.dumps({**response, "model_bundle_id": bundle.bundle_id})


@pytest.mark.asyncio
async def test_activation_during_cache_hit_never_serves_previous_bundle(monkeypatch):
    active, bundles = _active_test_models(monkeypatch)
    req = MLPredictRequest(symbol="A", horizon_days=7)
    cache = _PredictionCache(pause_at="get")
    old_key = ml_predict._cache_key(req)
    cache.data[old_key] = _cached_prediction(bundles["a"], .01)
    active["bundle"] = bundles["b"]
    new_key = ml_predict._cache_key(req)
    cache.data[new_key] = _cached_prediction(bundles["b"], .09)
    active["bundle"] = bundles["a"]
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": cache})
    task = asyncio.create_task(ml_predict._predict_response(req))
    await asyncio.wait_for(cache.started.wait(), timeout=2)
    active["bundle"] = bundles["b"]
    cache.resume.set()
    response = await asyncio.wait_for(task, timeout=2)
    assert response.model_version == "b"
    assert response.em_ml_pct == pytest.approx(.09)
    assert response.source == "cached"


@pytest.mark.asyncio
async def test_activation_during_cache_miss_caches_inference_under_its_own_bundle(monkeypatch):
    active, bundles = _active_test_models(monkeypatch)
    req = MLPredictRequest(symbol="A", horizon_days=7)
    cache = _PredictionCache(pause_at="get")
    old_key = ml_predict._cache_key(req)

    async def snapshot(*_args):
        return _snapshot(active["bundle"])

    monkeypatch.setattr(ml_predict.predict_service, "fetch_latest_feature_snapshot", snapshot)
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": cache})
    task = asyncio.create_task(ml_predict._predict_response(req))
    await asyncio.wait_for(cache.started.wait(), timeout=2)
    active["bundle"] = bundles["b"]
    cache.resume.set()
    response = await asyncio.wait_for(task, timeout=2)
    assert response.model_version == "b"
    assert response.em_ml_pct == pytest.approx(.09)
    assert old_key not in cache.data
    cached = json.loads(cache.data[ml_predict._cache_key(req)])
    assert cached["model_bundle_id"] == "b" * 64
    assert cached["model_version"] == "b"


@pytest.mark.asyncio
async def test_activation_during_snapshot_fetch_retries_with_the_new_bundle(monkeypatch):
    active, bundles = _active_test_models(monkeypatch)
    req = MLPredictRequest(symbol="A", horizon_days=7)
    cache = _PredictionCache(pause_at=None)
    started, resume = asyncio.Event(), asyncio.Event()

    async def snapshot(*_args):
        selected = active["bundle"]
        if selected is bundles["a"]:
            started.set()
            await resume.wait()
        return _snapshot(selected)

    monkeypatch.setattr(ml_predict.predict_service, "fetch_latest_feature_snapshot", snapshot)
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": cache})
    task = asyncio.create_task(ml_predict._predict_response(req))
    await asyncio.wait_for(started.wait(), timeout=2)
    active["bundle"] = bundles["b"]
    resume.set()
    response = await asyncio.wait_for(task, timeout=2)
    assert response.model_version == "b"
    assert response.em_ml_pct == pytest.approx(.09)
    cached = json.loads(cache.data[ml_predict._cache_key(req)])
    assert cached["model_bundle_id"] == "b" * 64


@pytest.mark.asyncio
async def test_activation_during_cache_write_never_returns_superseded_inference(monkeypatch):
    active, bundles = _active_test_models(monkeypatch)
    req = MLPredictRequest(symbol="A", horizon_days=7)
    cache = _PredictionCache(pause_at="set")
    old_key = ml_predict._cache_key(req)

    async def snapshot(*_args):
        return _snapshot(active["bundle"])

    monkeypatch.setattr(ml_predict.predict_service, "fetch_latest_feature_snapshot", snapshot)
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": cache})
    task = asyncio.create_task(ml_predict._predict_response(req))
    await asyncio.wait_for(cache.started.wait(), timeout=2)
    active["bundle"] = bundles["b"]
    cache.resume.set()
    response = await asyncio.wait_for(task, timeout=2)
    assert response.model_version == "b"
    assert response.em_ml_pct == pytest.approx(.09)
    assert json.loads(cache.data[old_key])["model_bundle_id"] == "a" * 64
    assert json.loads(cache.data[ml_predict._cache_key(req)])["model_bundle_id"] == "b" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", [None, "b" * 64])
async def test_previous_unbound_or_miskeyed_cache_entries_are_recomputed(monkeypatch, marker):
    active, bundles = _active_test_models(monkeypatch)
    req = MLPredictRequest(symbol="A", horizon_days=7)
    cache = _PredictionCache(pause_at=None)
    payload = json.loads(_cached_prediction(bundles["b"], .09))
    if marker is None:
        payload.pop("model_bundle_id")
    else:
        payload["model_bundle_id"] = marker
    cache.data[ml_predict._cache_key(req)] = json.dumps(payload)

    async def snapshot(*_args):
        return _snapshot(active["bundle"])

    monkeypatch.setattr(ml_predict.predict_service, "fetch_latest_feature_snapshot", snapshot)
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": cache})
    response = await ml_predict._predict_response(req)
    assert response.source == "computed"
    assert response.model_version == "a"
    assert response.em_ml_pct == pytest.approx(.01)
    assert json.loads(cache.data[ml_predict._cache_key(req)])["model_bundle_id"] == "a" * 64


@pytest.mark.asyncio
async def test_repeated_activation_fails_closed_without_caching_a_prediction(monkeypatch):
    active, bundles = _active_test_models(monkeypatch)

    class RotatingCache(_PredictionCache):
        async def get(self, key):
            active["bundle"] = bundles["b"] if active["bundle"] is bundles["a"] else bundles["a"]
            await asyncio.sleep(0)
            return None

    cache = RotatingCache(pause_at=None)

    async def snapshot(*_args):
        return _snapshot(active["bundle"])

    monkeypatch.setattr(ml_predict.predict_service, "fetch_latest_feature_snapshot", snapshot)
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": cache})
    with pytest.raises(HTTPException) as error:
        await asyncio.wait_for(ml_predict._predict_response(MLPredictRequest(symbol="A", horizon_days=7)), timeout=2)
    assert error.value.status_code == 503
    assert "retry" in error.value.detail
    assert cache.data == {}


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


class _CoverageConn:
    async def fetchrow(self, *_args):
        return {
            "total_feature_rows": 158,
            "fresh_distinct_symbols": 82,
            "fresh_distinct_events": 94,
        }

    async def fetch(self, query, *_args):
        if "GROUP BY model_horizon" in query:
            return [
                {
                    "horizon_days": 7,
                    "total_rows": 42,
                    "fresh_rows": 42,
                    "fresh_symbols": 40,
                    "fresh_events": 42,
                    "earliest_snapshot": date(2026, 5, 18),
                    "latest_snapshot": date(2026, 5, 21),
                }
            ]
        if "has_feature_vector" in query:
            return [
                {
                    "horizon_days": 7,
                    "earnings_date": date(2026, 5, 27),
                    "snapshot_date": date(2026, 5, 20),
                    "snapshot_age_days": 4,
                    "spot_price": 180.10,
                    "scored_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
                    "has_feature_vector": True,
                },
                {
                    "horizon_days": 14,
                    "earnings_date": date(2026, 5, 27),
                    "snapshot_date": date(2026, 5, 10),
                    "snapshot_age_days": 14,
                    "spot_price": 175.00,
                    "scored_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
                    "has_feature_vector": True,
                },
                {
                    "horizon_days": 21,
                    "earnings_date": date(2026, 5, 27),
                    "snapshot_date": date(2026, 5, 6),
                    "snapshot_age_days": 18,
                    "spot_price": 172.00,
                    "scored_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
                    "has_feature_vector": False,
                },
            ]
        return [
            {
                "horizon_days": 7,
                "earnings_date": date(2026, 5, 27),
                "snapshot_date": date(2026, 5, 20),
                "snapshot_age_days": 4,
                "spot_price": 180.10,
                "scored_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
            },
            {
                "horizon_days": 14,
                "earnings_date": date(2026, 5, 27),
                "snapshot_date": date(2026, 5, 10),
                "snapshot_age_days": 14,
                "spot_price": 175.00,
                "scored_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
            }
        ]


class _StatusConn:
    async def fetchrow(self, query, *_args):
        if "FROM em_forecast_imports" in query:
            return {
                "parquet_file": "forecasts_2026-05-24.parquet",
                "imported_at": datetime(2026, 5, 24, 1, 2, 3, tzinfo=timezone.utc),
                "import_mode": "full",
                "source_rows": 424,
                "selected_rows": 424,
                "duplicate_rows": 20,
                "duplicate_keys": 10,
                "rows_upserted": 414,
                "feature_vector_rows": 414,
                "distinct_symbols": 180,
                "distinct_events": 190,
                "min_snapshot_date": date(2026, 5, 12),
                "max_snapshot_date": date(2026, 5, 24),
                "model_bundle_id": "bundle-2026-05-24",
                "horizons": {"7": 100, "14": 140, "21": 174},
            }
        return {
            "total_feature_rows": 168,
            "fresh_feature_rows": 100,
            "fresh_distinct_symbols": 86,
            "fresh_distinct_events": 99,
            "latest_snapshot_date": date(2026, 5, 22),
            "latest_scored_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
        }

    async def fetch(self, *_args):
        return [
            {
                "horizon_days": 7,
                "total_feature_rows": 46,
                "fresh_feature_rows": 46,
                "latest_snapshot_date": date(2026, 5, 22),
                "latest_scored_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
            }
        ]

    async def fetchval(self, *_args):
        return True


@pytest.mark.asyncio
async def test_coverage_endpoint_returns_totals_and_event_availability():
    ml_predict.init_router({"db_pool": _Pool(_CoverageConn()), "redis_client": None})

    response = await ml_predict.coverage_endpoint(
        MLCoverageRequest(symbol="crm", earnings_date=date(2026, 5, 27)),
    )

    assert response.total_feature_rows == 158
    assert response.fresh_distinct_symbols == 82
    assert response.rows_by_horizon[0].horizon_days == 7
    assert response.rows_by_horizon[0].fresh_events == 42
    assert response.symbol == "CRM"
    assert response.available_horizons[0].snapshot_age_days == 4
    assert response.available_horizons[0].spot_update_eligible is True
    assert response.available_horizons[0].unavailable_reason is None
    assert response.available_horizons[0].forecast_scored_at == datetime(
        2026, 5, 24, tzinfo=timezone.utc
    )
    assert response.available_horizons[1].spot_update_eligible is False
    assert response.available_horizons[1].unavailable_reason == "snapshot_stale"
    assert response.supported_horizons == [1, 2, 3, 7, 14, 21]
    by_horizon = {row.horizon_days: row for row in response.event_horizon_statuses}
    assert by_horizon[1].spot_update_eligible is False
    assert by_horizon[1].unavailable_reason == "no_snapshot"
    assert by_horizon[7].spot_update_eligible is True
    assert by_horizon[7].unavailable_reason is None
    assert by_horizon[14].unavailable_reason == "snapshot_stale"
    assert by_horizon[21].unavailable_reason == "missing_feature_vector"


@pytest.mark.asyncio
async def test_batch_predict_returns_per_item_errors(monkeypatch):
    async def fake_predict(req):
        if req.symbol.upper() == "OK":
            return MLPredictResponse(
                symbol="OK",
                horizon_days=req.horizon_days,
                em_ml_pct=0.07,
                em_ml_abs=7.0,
                quantiles={10: 0.01, 50: 0.05, 90: 0.17},
                spot_used=req.spot_override or 100.0,
                feature_snapshot_date="2026-05-20",
                earnings_date=req.earnings_date,
                source="computed",
                inference_mode="spot_updated_snapshot",
                served_at=datetime.now(timezone.utc),
            )
        raise HTTPException(status_code=404, detail="No fresh feature snapshot")

    monkeypatch.setattr(ml_predict, "_predict_response", fake_predict)
    request = MLBatchPredictRequest(
        items=[
            {"symbol": "OK", "horizon_days": 7, "spot_override": 100.0},
            {"symbol": "MISS", "horizon_days": 7, "spot_override": 100.0},
        ]
    )

    response = await ml_predict.batch_predict_endpoint(request)

    assert [item.ok for item in response.items] == [True, False]
    assert response.items[0].response is not None
    assert response.items[1].error_status == 404
    assert "No fresh" in (response.items[1].error or "")


@pytest.mark.asyncio
async def test_status_endpoint_returns_model_and_data_metadata(monkeypatch):
    monkeypatch.setattr(
        ml_predict.predict_service,
        "model_inventory",
        lambda: [
            {
                "horizon_days": 7,
                "point_model_exists": True,
                "quantile_model_count": 5,
                "feature_count": 40,
                "feature_schema_hash": "abc123",
                "model_version": "v3",
                "trained_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
                "val_mae": 0.04,
                "loaded": True,
                "loaded_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
                "model_mtime": datetime(2026, 5, 24, tzinfo=timezone.utc),
                "metadata_mtime": datetime(2026, 5, 24, tzinfo=timezone.utc),
            }
        ],
    )
    monkeypatch.setattr(ml_predict.predict_service, "loaded_horizons", lambda: [7])
    monkeypatch.setattr(ml_predict.predict_service, "_models_dir", lambda: "/tmp/models")
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": None})

    response = await ml_predict.status_endpoint(MLStatusRequest())

    assert response.ok is True
    assert response.status == "ok"
    assert response.data is not None
    assert response.data.total_feature_rows == 168
    assert response.rows_by_horizon[0].fresh_feature_rows == 46
    assert response.latest_import is not None
    assert response.latest_import.parquet_file == "forecasts_2026-05-24.parquet"
    assert response.latest_import.source_rows == 424
    assert response.latest_import.rows_upserted == 414
    assert response.latest_import.model_bundle_id == "bundle-2026-05-24"
    assert response.latest_import.horizons["21"] == 174
    assert response.supported_horizons == [1, 2, 3, 7, 14, 21]
    assert response.available_model_horizons == [7]
    assert response.loaded_model_horizons == [7]
    assert response.missing_model_horizons == [1, 2, 3, 14, 21]
    assert response.missing_fresh_horizons == [1, 2, 3, 14, 21]
    assert response.coverage_gaps[0].unavailable_reason == "model_missing"
    assert response.coverage_gaps[3].horizon_days == 7
    assert response.coverage_gaps[3].unavailable_reason is None
    assert response.models[0].feature_schema_hash == "abc123"
    assert response.redis_available is False


@pytest.mark.asyncio
async def test_predict_response_includes_debug_metadata(monkeypatch):
    monkeypatch.setattr(ml_predict.predict_service, "get_bundle", lambda _: SimpleNamespace(bundle_id="a" * 64))
    class _Result:
        em_ml_pct = 0.07
        em_ml_abs = 7.0
        quantiles = {10: 0.01, 50: 0.05, 90: 0.17}
        spot_used = 100.0
        feature_snapshot_date = "2026-05-20"
        model_version = "v3"
        model_trained_at = datetime(2026, 5, 24, tzinfo=timezone.utc)
        model_loaded_at = datetime(2026, 5, 24, tzinfo=timezone.utc)
        feature_schema_hash = "abc123"

    async def fake_snapshot(*_args):
        return {
            "snapshot_date": date(2026, 5, 20),
            "earnings_date": date(2026, 5, 27),
            "feature_vector": {"log_spot": 4.6},
            "model_bundle_id": "a" * 64,
            "spot_at_snapshot": 99.0,
            "forecast_scored_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
            "snapshot_age_days": 4,
        }

    monkeypatch.setattr(ml_predict.predict_service, "fetch_latest_feature_snapshot", fake_snapshot)
    monkeypatch.setattr(ml_predict.predict_service, "predict", lambda **_kwargs: _Result())
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": None})

    response = await ml_predict._predict_response(
        MLPredictRequest(symbol="CRM", horizon_days=7, spot_override=100.0),
    )

    assert response.snapshot_age_days == 4
    assert response.forecast_scored_at == datetime(2026, 5, 24, tzinfo=timezone.utc)
    assert response.model_version == "v3"
    assert response.feature_schema_hash == "abc123"
    assert response.inference_mode == "spot_updated_snapshot"
    assert response.market_data_mode == "end_of_day"
    assert response.decision_scope == "end_of_day_research"
    assert response.live_trading_eligible is False
    assert response.updated_inputs == ["spot"]


@pytest.mark.asyncio
async def test_cached_prediction_is_upgraded_to_current_decision_contract(monkeypatch):
    monkeypatch.setattr(ml_predict.predict_service, "get_bundle", lambda _: SimpleNamespace(bundle_id="a" * 64))
    async def fake_cached_get(_key):
        return {
            "symbol": "CRM",
            "horizon_days": 7,
            "em_ml_pct": 0.07,
            "em_ml_abs": 7.0,
            "quantiles": {10: 0.01, 50: 0.05, 90: 0.17},
            "spot_used": 100.0,
            "feature_snapshot_date": "2026-05-20",
            "earnings_date": "2026-05-27",
            "source": "live",
            "served_at": "2026-05-24T00:00:00Z",
            "model_bundle_id": "a" * 64,
        }

    monkeypatch.setattr(ml_predict, "_cached_get", fake_cached_get)
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": None})

    response = await ml_predict._predict_response(
        MLPredictRequest(symbol="CRM", horizon_days=7, spot_override=100.0),
    )

    assert response.source == "cached"
    assert response.inference_mode == "spot_updated_snapshot"
    assert response.decision_scope == "end_of_day_research"
    assert response.live_trading_eligible is False
    assert response.updated_inputs == ["spot"]


def test_prediction_request_rejects_live_trading_intent() -> None:
    with pytest.raises(ValueError):
        MLPredictRequest(
            symbol="CRM",
            horizon_days=7,
            spot_override=100.0,
            intended_use="live_trading",
        )


@pytest.mark.asyncio
async def test_predict_response_labels_snapshot_rescore_without_spot_override(monkeypatch):
    monkeypatch.setattr(ml_predict.predict_service, "get_bundle", lambda _: SimpleNamespace(bundle_id="a" * 64))
    class _SnapshotResult:
        em_ml_pct = 0.07
        em_ml_abs = 6.93
        quantiles = {10: 0.01, 50: 0.05, 90: 0.17}
        spot_used = 99.0
        feature_snapshot_date = "2026-05-20"
        model_version = "v3"
        model_trained_at = datetime(2026, 5, 24, tzinfo=timezone.utc)
        model_loaded_at = datetime(2026, 5, 24, tzinfo=timezone.utc)
        feature_schema_hash = "abc123"

    async def fake_snapshot(*_args):
        return {
            "snapshot_date": date(2026, 5, 20),
            "earnings_date": date(2026, 5, 27),
            "feature_vector": {"log_spot": 4.6},
            "model_bundle_id": "a" * 64,
            "spot_at_snapshot": 99.0,
            "forecast_scored_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
            "snapshot_age_days": 4,
        }

    monkeypatch.setattr(ml_predict.predict_service, "fetch_latest_feature_snapshot", fake_snapshot)
    monkeypatch.setattr(
        ml_predict.predict_service,
        "predict",
        lambda **_kwargs: _SnapshotResult(),
    )
    ml_predict.init_router({"db_pool": _Pool(_StatusConn()), "redis_client": None})

    response = await ml_predict._predict_response(
        MLPredictRequest(symbol="CRM", horizon_days=7),
    )

    assert response.inference_mode == "snapshot_rescore"
    assert response.updated_inputs == []
