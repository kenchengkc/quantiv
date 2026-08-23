"""Smoke tests for Pydantic models."""

import sys
from pathlib import Path

# Ensure backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime
from models import (
    HealthResponse,
)


def test_health_response():
    resp = HealthResponse(
        status="healthy",
        timestamp=datetime.now(),
        services={"postgres": "healthy", "redis": "healthy"},
    )
    assert resp.status == "healthy"
