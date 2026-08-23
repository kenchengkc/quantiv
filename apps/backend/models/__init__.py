"""Pydantic models for Quantiv API request/response schemas."""

import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _normalize_ml_symbol(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    symbol = value.strip().upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("symbol must match ^[A-Z][A-Z0-9.-]{0,9}$")
    return symbol


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    services: dict[str, str]


# ─── /api/ml/predict (Phase 1) ────────────────────────────────────────────


class MLPredictRequest(BaseModel):
    """Re-inference request. The route reads a recent feature snapshot from
    em_forecasts, substitutes `spot_override` in for the spot-derived
    features, and re-runs the LightGBM head for `horizon_days`."""

    symbol: str = Field(..., min_length=1, max_length=10, description="e.g. AAPL")
    horizon_days: int = Field(..., description="Must be one of 1, 2, 3, 7, 14, 21")
    spot_override: float | None = Field(
        None, gt=0,
        description="Live spot price (Finnhub). Omit to score with the snapshot's spot.",
    )
    earnings_date: date | None = Field(
        None,
        description="Pin to a specific earnings event; otherwise the latest snapshot wins.",
    )

    @field_validator("symbol", mode="before")
    @classmethod
    def validate_symbol(cls, value: Any) -> Any:
        return _normalize_ml_symbol(value)


class MLPredictResponse(BaseModel):
    symbol: str
    horizon_days: int
    em_ml_pct: float = Field(..., description="Point prediction as a fraction (0.05 = 5%)")
    em_ml_abs: float = Field(..., description="Absolute $ move (= em_ml_pct * spot_used)")
    quantiles: dict[int, float] = Field(
        default_factory=dict,
        description="Quantile predictions keyed by percentile (10, 25, 50, 75, 90).",
    )
    spot_used: float
    feature_snapshot_date: str = Field(
        ..., description="ET date of the chain snapshot whose features we re-scored.",
    )
    earnings_date: date | None = None
    source: str = Field(
        ...,
        description="'live' = backend ran the model; 'cached' = served from Upstash within TTL.",
    )
    served_at: datetime
    snapshot_age_days: int | None = Field(
        None,
        description="Age in calendar days of the feature snapshot used for re-scoring.",
    )
    forecast_scored_at: datetime | None = Field(
        None,
        description="Timestamp from the nightly/weekly scoring run that wrote this feature row.",
    )
    model_version: str | None = Field(
        None,
        description="Version string from the LightGBM model metadata.",
    )
    model_trained_at: datetime | None = Field(
        None,
        description="Training timestamp from the model metadata, when available.",
    )
    model_loaded_at: datetime | None = Field(
        None,
        description="When this backend process loaded the model bundle.",
    )
    feature_schema_hash: str | None = Field(
        None,
        description="SHA-256 hash of the model feature order used for inference.",
    )


class MLCoverageRequest(BaseModel):
    """Coverage/introspection request for the persisted feature snapshots."""

    symbol: str | None = Field(
        None,
        min_length=1,
        max_length=10,
        description="Optional ticker to inspect for event-level availability.",
    )
    earnings_date: date | None = Field(
        None,
        description="Optional event date to pair with symbol.",
    )
    fresh_window_days: int = Field(
        7,
        ge=1,
        le=60,
        description="Snapshot freshness window used for fresh coverage counts.",
    )

    @field_validator("symbol", mode="before")
    @classmethod
    def validate_symbol(cls, value: Any) -> Any:
        if value is None:
            return None
        return _normalize_ml_symbol(value)


class MLCoverageHorizonRow(BaseModel):
    horizon_days: int
    total_rows: int
    fresh_rows: int
    fresh_symbols: int
    fresh_events: int
    earliest_snapshot: date | None = None
    latest_snapshot: date | None = None


class MLAvailableHorizon(BaseModel):
    horizon_days: int
    earnings_date: date
    snapshot_date: date
    snapshot_age_days: int
    live_eligible: bool = Field(
        ...,
        description="True when this snapshot is recent enough for /api/ml/predict.",
    )
    unavailable_reason: str | None = Field(
        None,
        description="Machine-readable reason when live_eligible is false.",
    )
    spot_price: float | None = None
    forecast_scored_at: datetime | None = None


class MLEventHorizonStatus(BaseModel):
    horizon_days: int
    earnings_date: date | None = None
    snapshot_date: date | None = None
    snapshot_age_days: int | None = None
    live_eligible: bool = Field(
        False,
        description="True when /api/ml/predict can use this event/horizon now.",
    )
    unavailable_reason: str | None = Field(
        None,
        description=(
            "Machine-readable reason when unavailable: no_snapshot, "
            "missing_feature_vector, or snapshot_stale."
        ),
    )
    spot_price: float | None = None
    forecast_scored_at: datetime | None = None


class MLCoverageResponse(BaseModel):
    total_feature_rows: int
    fresh_window_days: int
    fresh_distinct_symbols: int
    fresh_distinct_events: int
    supported_horizons: list[int] = Field(default_factory=list)
    rows_by_horizon: list[MLCoverageHorizonRow]
    symbol: str | None = None
    earnings_date: date | None = None
    available_horizons: list[MLAvailableHorizon] = Field(default_factory=list)
    event_horizon_statuses: list[MLEventHorizonStatus] = Field(default_factory=list)
    checked_at: datetime


class MLBatchPredictRequest(BaseModel):
    """Batch wrapper around MLPredictRequest.

    Responses are per item; one unavailable symbol should not fail the
    whole batch because coverage is intentionally sparse today.
    """

    items: list[MLPredictRequest] = Field(..., min_length=1, max_length=100)
    allow_partial: bool = Field(
        True,
        description="Accepted for client intent; route always returns per-item results.",
    )


class MLBatchPredictItemResponse(BaseModel):
    ok: bool
    symbol: str
    horizon_days: int
    earnings_date: date | None = None
    response: MLPredictResponse | None = None
    error_status: int | None = None
    error: str | None = None


class MLBatchPredictResponse(BaseModel):
    items: list[MLBatchPredictItemResponse]
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
    feature_count: int | None = None
    feature_schema_hash: str | None = None
    model_version: str | None = None
    trained_at: datetime | None = None
    val_mae: float | None = None
    loaded: bool = False
    loaded_at: datetime | None = None
    model_mtime: datetime | None = None
    metadata_mtime: datetime | None = None


class MLStatusDataRow(BaseModel):
    total_feature_rows: int
    fresh_feature_rows: int
    fresh_distinct_symbols: int
    fresh_distinct_events: int
    latest_snapshot_date: date | None = None
    latest_scored_at: datetime | None = None


class MLStatusHorizonRow(BaseModel):
    horizon_days: int
    total_feature_rows: int
    fresh_feature_rows: int
    latest_snapshot_date: date | None = None
    latest_scored_at: datetime | None = None


class MLStatusCoverageGapRow(BaseModel):
    horizon_days: int
    model_available: bool
    has_any_feature_rows: bool
    has_fresh_feature_rows: bool
    total_feature_rows: int
    fresh_feature_rows: int
    unavailable_reason: str | None = Field(
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
    min_snapshot_date: date | None = None
    max_snapshot_date: date | None = None
    model_bundle_id: str | None = None
    horizons: dict[str, int] = Field(default_factory=dict)


class MLStatusResponse(BaseModel):
    ok: bool
    status: str
    checked_at: datetime
    fresh_window_days: int
    max_snapshot_age_days: int
    models_dir: str
    supported_horizons: list[int] = Field(default_factory=list)
    available_model_horizons: list[int]
    loaded_model_horizons: list[int]
    missing_model_horizons: list[int] = Field(default_factory=list)
    missing_fresh_horizons: list[int] = Field(default_factory=list)
    redis_available: bool
    postgres_available: bool
    data: MLStatusDataRow | None = None
    rows_by_horizon: list[MLStatusHorizonRow] = Field(default_factory=list)
    coverage_gaps: list[MLStatusCoverageGapRow] = Field(default_factory=list)
    latest_import: MLStatusImportRow | None = None
    models: list[MLStatusModelRow] = Field(default_factory=list)
