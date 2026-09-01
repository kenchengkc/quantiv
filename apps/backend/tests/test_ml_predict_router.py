"""Route-level tests for ML coverage and batch prediction endpoints."""

import sys
from datetime import date, datetime, timezone
from pathlib import Path

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
