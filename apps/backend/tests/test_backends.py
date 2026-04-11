"""Unit tests for backend data classes (no live DB needed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

try:
    from backends import DataBackend
except ImportError:
    pytest.skip("Backend dependencies not installed", allow_module_level=True)


@pytest.mark.asyncio
async def test_data_backend_interface():
    """Base DataBackend methods raise NotImplementedError."""
    backend = DataBackend()
    with pytest.raises(NotImplementedError):
        await backend.get_latest_forecasts("AAPL", ["1d"])
    with pytest.raises(NotImplementedError):
        await backend.get_symbols(7)


@pytest.mark.asyncio
async def test_data_backend_health_default():
    backend = DataBackend()
    result = await backend.health()
    assert result == {"database": "unknown"}
