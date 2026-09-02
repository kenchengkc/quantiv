"""Operational health endpoint for the spot-updated prediction service."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from models import HealthResponse

router = APIRouter()
_state: dict[str, Any] = {}


def init_router(state: dict[str, Any]) -> None:
    _state.clear()
    _state.update(state)


def _build_revision() -> str | None:
    for name in (
        "RAILWAY_GIT_COMMIT_SHA",
        "GIT_COMMIT_SHA",
        "GITHUB_SHA",
        "SOURCE_VERSION",
    ):
        value = os.getenv(name)
        if value:
            return value.strip()
    return None


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Report the two dependencies used by the current serving path."""
    services: dict[str, str] = {}

    try:
        pool = _state.get("db_pool")
        if pool is None:
            raise RuntimeError("Postgres pool is not initialized")
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        services["postgres"] = "healthy"
    except Exception:
        services["postgres"] = "unhealthy"

    try:
        redis_client = _state.get("redis_client")
        if redis_client is None:
            raise RuntimeError("Redis client is not initialized")
        await redis_client.ping()
        services["redis"] = "healthy"
    except Exception:
        services["redis"] = "unhealthy"

    status = "healthy" if all(value == "healthy" for value in services.values()) else "degraded"
    return HealthResponse(
        status=status,
        timestamp=datetime.now(timezone.utc),
        services=services,
        revision=_build_revision(),
    )
