"""Pydantic models for Quantiv API request/response schemas."""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, date


class ExpectedMoveRequest(BaseModel):
    symbol: str = Field(..., description="Stock symbol (e.g., AAPL)")
    horizons: List[str] = Field(default=["to_exp", "1d", "5d"], description="Forecast horizons")
    include_live: bool = Field(default=True, description="Include live market data")


class ExpectedMoveResponse(BaseModel):
    symbol: str
    timestamp: datetime
    forecasts: List[Dict[str, Any]]
    live_data: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any]


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    services: Dict[str, str]


class EmForecastLatestResponse(BaseModel):
    symbol: str
    exp: date
    quote_ts: datetime
    horizon: str
    em_baseline: Optional[float] = None
    band68_low: Optional[float] = None
    band68_high: Optional[float] = None
    band95_low: Optional[float] = None
    band95_high: Optional[float] = None
    metadata: Dict[str, Any]


class EmHistoryItem(BaseModel):
    quote_ts: datetime
    em_baseline: Optional[float] = None
    band68_low: Optional[float] = None
    band68_high: Optional[float] = None
    band95_low: Optional[float] = None
    band95_high: Optional[float] = None


class EmHistoryResponse(BaseModel):
    symbol: str
    exp: date
    window: str
    items: List[EmHistoryItem]
    metadata: Dict[str, Any]


class EmExpiriesResponse(BaseModel):
    symbol: str
    expiries: List[date]
    metadata: Dict[str, Any]


# ─── /api/ml/predict (Phase 1) ────────────────────────────────────────────


class MLPredictRequest(BaseModel):
    """Re-inference request. The route reads a recent feature snapshot from
    em_forecasts, substitutes `spot_override` in for the spot-derived
    features, and re-runs the LightGBM head for `horizon_days`."""

    symbol: str = Field(..., min_length=1, max_length=10, description="e.g. AAPL")
    horizon_days: int = Field(..., description="Must be one of 1, 2, 3, 7, 14, 21")
    spot_override: Optional[float] = Field(
        None, gt=0,
        description="Live spot price (Finnhub). Omit to score with the snapshot's spot.",
    )
    earnings_date: Optional[date] = Field(
        None,
        description="Pin to a specific earnings event; otherwise the latest snapshot wins.",
    )


class MLPredictResponse(BaseModel):
    symbol: str
    horizon_days: int
    em_ml_pct: float = Field(..., description="Point prediction as a fraction (0.05 = 5%)")
    em_ml_abs: float = Field(..., description="Absolute $ move (= em_ml_pct * spot_used)")
    quantiles: Dict[int, float] = Field(
        default_factory=dict,
        description="Quantile predictions keyed by percentile (10, 25, 50, 75, 90).",
    )
    spot_used: float
    feature_snapshot_date: str = Field(
        ..., description="ET date of the chain snapshot whose features we re-scored.",
    )
    earnings_date: Optional[date] = None
    source: str = Field(
        ...,
        description="'live' = backend ran the model; 'cached' = served from Upstash within TTL.",
    )
    served_at: datetime
