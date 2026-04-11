"""Smoke tests for Pydantic models."""

import sys
from pathlib import Path

# Ensure backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, date
from models import (
    ExpectedMoveRequest,
    ExpectedMoveResponse,
    HealthResponse,
    EmForecastLatestResponse,
    EmHistoryResponse,
    EmExpiriesResponse,
)


def test_expected_move_request_defaults():
    req = ExpectedMoveRequest(symbol="AAPL")
    assert req.symbol == "AAPL"
    assert req.horizons == ["to_exp", "1d", "5d"]
    assert req.include_live is True


def test_expected_move_response():
    resp = ExpectedMoveResponse(
        symbol="AAPL",
        timestamp=datetime.now(),
        forecasts=[{"horizon": "1d", "em_baseline": 3.5}],
        metadata={"forecast_count": 1},
    )
    assert resp.symbol == "AAPL"
    assert len(resp.forecasts) == 1


def test_health_response():
    resp = HealthResponse(
        status="healthy",
        timestamp=datetime.now(),
        services={"postgres": "healthy", "redis": "healthy"},
    )
    assert resp.status == "healthy"


def test_em_forecast_latest_response():
    resp = EmForecastLatestResponse(
        symbol="AAPL",
        exp=date(2025, 1, 17),
        quote_ts=datetime.now(),
        horizon="to_exp",
        em_baseline=5.2,
        band68_low=3.1,
        band68_high=7.3,
        metadata={"source": "test"},
    )
    assert resp.em_baseline == 5.2


def test_em_history_response():
    resp = EmHistoryResponse(
        symbol="AAPL",
        exp=date(2025, 1, 17),
        window="90d",
        items=[],
        metadata={"count": 0},
    )
    assert resp.items == []


def test_em_expiries_response():
    resp = EmExpiriesResponse(
        symbol="AAPL",
        expiries=[date(2025, 1, 17), date(2025, 2, 21)],
        metadata={"count": 2},
    )
    assert len(resp.expiries) == 2
