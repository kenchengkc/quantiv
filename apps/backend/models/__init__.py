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
    snapshot_age_days: Optional[int] = Field(
        None,
        description="Age in calendar days of the feature snapshot used for re-scoring.",
    )
    forecast_scored_at: Optional[datetime] = Field(
        None,
        description="Timestamp from the nightly/weekly scoring run that wrote this feature row.",
    )
    model_version: Optional[str] = Field(
        None,
        description="Version string from the LightGBM model metadata.",
    )
    model_trained_at: Optional[datetime] = Field(
        None,
        description="Training timestamp from the model metadata, when available.",
    )
    model_loaded_at: Optional[datetime] = Field(
        None,
        description="When this backend process loaded the model bundle.",
    )
    feature_schema_hash: Optional[str] = Field(
        None,
        description="SHA-256 hash of the model feature order used for inference.",
    )


class MLCoverageRequest(BaseModel):
    """Coverage/introspection request for the persisted feature snapshots."""

    symbol: Optional[str] = Field(
        None,
        min_length=1,
        max_length=10,
        description="Optional ticker to inspect for event-level availability.",
    )
    earnings_date: Optional[date] = Field(
        None,
        description="Optional event date to pair with symbol.",
    )
    fresh_window_days: int = Field(
        7,
        ge=1,
        le=60,
        description="Snapshot freshness window used for fresh coverage counts.",
    )


class MLCoverageHorizonRow(BaseModel):
    horizon_days: int
    total_rows: int
    fresh_rows: int
    fresh_symbols: int
    fresh_events: int
    earliest_snapshot: Optional[date] = None
    latest_snapshot: Optional[date] = None


class MLAvailableHorizon(BaseModel):
    horizon_days: int
    earnings_date: date
    snapshot_date: date
    snapshot_age_days: int
    live_eligible: bool = Field(
        ...,
        description="True when this snapshot is recent enough for /api/ml/predict.",
    )
    unavailable_reason: Optional[str] = Field(
        None,
        description="Machine-readable reason when live_eligible is false.",
    )
    spot_price: Optional[float] = None
    forecast_scored_at: Optional[datetime] = None


class MLEventHorizonStatus(BaseModel):
    horizon_days: int
    earnings_date: Optional[date] = None
    snapshot_date: Optional[date] = None
    snapshot_age_days: Optional[int] = None
    live_eligible: bool = Field(
        False,
        description="True when /api/ml/predict can use this event/horizon now.",
    )
    unavailable_reason: Optional[str] = Field(
        None,
        description=(
            "Machine-readable reason when unavailable: no_snapshot, "
            "missing_feature_vector, or snapshot_stale."
        ),
    )
    spot_price: Optional[float] = None
    forecast_scored_at: Optional[datetime] = None


class MLCoverageResponse(BaseModel):
    total_feature_rows: int
    fresh_window_days: int
    fresh_distinct_symbols: int
    fresh_distinct_events: int
    supported_horizons: List[int] = Field(default_factory=list)
    rows_by_horizon: List[MLCoverageHorizonRow]
    symbol: Optional[str] = None
    earnings_date: Optional[date] = None
    available_horizons: List[MLAvailableHorizon] = Field(default_factory=list)
    event_horizon_statuses: List[MLEventHorizonStatus] = Field(default_factory=list)
    checked_at: datetime


class MLBatchPredictRequest(BaseModel):
    """Batch wrapper around MLPredictRequest.

    Responses are per item; one unavailable symbol should not fail the
    whole batch because coverage is intentionally sparse today.
    """

    items: List[MLPredictRequest] = Field(..., min_length=1, max_length=100)
    allow_partial: bool = Field(
        True,
        description="Accepted for client intent; route always returns per-item results.",
    )


class MLBatchPredictItemResponse(BaseModel):
    ok: bool
    symbol: str
    horizon_days: int
    earnings_date: Optional[date] = None
    response: Optional[MLPredictResponse] = None
    error_status: Optional[int] = None
    error: Optional[str] = None


class MLBatchPredictResponse(BaseModel):
    items: List[MLBatchPredictItemResponse]
    served_at: datetime


class MLStatusRequest(BaseModel):
    fresh_window_days: int = Field(
        7,
        ge=1,
        le=60,
        description="Snapshot freshness window used for data coverage counts.",
    )


class MLStatusModelRow(BaseModel):
    horizon_days: int
    point_model_exists: bool
    quantile_model_count: int
    feature_count: Optional[int] = None
    feature_schema_hash: Optional[str] = None
    model_version: Optional[str] = None
    trained_at: Optional[datetime] = None
    val_mae: Optional[float] = None
    loaded: bool = False
    loaded_at: Optional[datetime] = None
    model_mtime: Optional[datetime] = None
    metadata_mtime: Optional[datetime] = None


class MLStatusDataRow(BaseModel):
    total_feature_rows: int
    fresh_feature_rows: int
    fresh_distinct_symbols: int
    fresh_distinct_events: int
    latest_snapshot_date: Optional[date] = None
    latest_scored_at: Optional[datetime] = None


class MLStatusHorizonRow(BaseModel):
    horizon_days: int
    total_feature_rows: int
    fresh_feature_rows: int
    latest_snapshot_date: Optional[date] = None
    latest_scored_at: Optional[datetime] = None


class MLStatusCoverageGapRow(BaseModel):
    horizon_days: int
    model_available: bool
    has_any_feature_rows: bool
    has_fresh_feature_rows: bool
    total_feature_rows: int
    fresh_feature_rows: int
    unavailable_reason: Optional[str] = Field(
        None,
        description=(
            "None when usable; otherwise model_missing, no_feature_rows, "
            "or no_fresh_feature_rows."
        ),
    )


class MLStatusImportRow(BaseModel):
    parquet_file: str
    imported_at: datetime
    import_mode: str
    source_rows: int
    selected_rows: int
    duplicate_rows: int
    duplicate_keys: int
    rows_upserted: int
    feature_vector_rows: int
    distinct_symbols: int
    distinct_events: int
    min_snapshot_date: Optional[date] = None
    max_snapshot_date: Optional[date] = None
    horizons: Dict[str, int] = Field(default_factory=dict)


class MLStatusResponse(BaseModel):
    ok: bool
    status: str
    checked_at: datetime
    fresh_window_days: int
    max_snapshot_age_days: int
    models_dir: str
    supported_horizons: List[int] = Field(default_factory=list)
    available_model_horizons: List[int]
    loaded_model_horizons: List[int]
    missing_model_horizons: List[int] = Field(default_factory=list)
    missing_fresh_horizons: List[int] = Field(default_factory=list)
    redis_available: bool
    postgres_available: bool
    data: Optional[MLStatusDataRow] = None
    rows_by_horizon: List[MLStatusHorizonRow] = Field(default_factory=list)
    coverage_gaps: List[MLStatusCoverageGapRow] = Field(default_factory=list)
    latest_import: Optional[MLStatusImportRow] = None
    models: List[MLStatusModelRow] = Field(default_factory=list)
