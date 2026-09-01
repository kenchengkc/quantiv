"""Health checks cover only dependencies used by spot-updated prediction."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import sys

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from routers import health  # noqa: E402


class _Connection:
    async def fetchval(self, _query: str) -> int:
        return 1


class _Pool:
    @asynccontextmanager
    async def acquire(self):
        yield _Connection()


class _Redis:
    async def ping(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_health_reports_current_serving_dependencies() -> None:
    health.init_router({"db_pool": _Pool(), "redis_client": _Redis()})

    response = await health.health_check()

    assert response.status == "healthy"
    assert response.services == {"postgres": "healthy", "redis": "healthy"}


@pytest.mark.asyncio
async def test_health_degrades_when_dependencies_are_uninitialized() -> None:
    health.init_router({"db_pool": None, "redis_client": None})

    response = await health.health_check()

    assert response.status == "degraded"
    assert response.services == {"postgres": "unhealthy", "redis": "unhealthy"}
