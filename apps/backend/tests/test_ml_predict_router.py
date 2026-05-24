"""Route-level tests for ML coverage and batch prediction endpoints."""

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))

from models import MLBatchPredictRequest, MLCoverageRequest, MLPredictResponse  # noqa: E402
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
        return [
            {
                "horizon_days": 7,
                "earnings_date": date(2026, 5, 27),
                "snapshot_date": date(2026, 5, 20),
                "snapshot_age_days": 4,
                "spot_price": 180.10,
            }
        ]


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
                source="live",
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
