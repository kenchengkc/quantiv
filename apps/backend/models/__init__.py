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
