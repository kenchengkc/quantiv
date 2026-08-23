"""HTTP-level coverage for backend authentication and production middleware."""

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from middleware import hmac_auth  # noqa: E402
from middleware.hmac_auth import _expected_sig  # noqa: E402
from models import MLPredictResponse  # noqa: E402
from routers import ml_predict  # noqa: E402

SECRET = "test-backend-secret"
NOW_MS = 1_800_000_000_000


@pytest.fixture(autouse=True)
def fixed_hmac_clock(monkeypatch):
    monkeypatch.setattr(
        hmac_auth,
        "time",
        SimpleNamespace(time=lambda: NOW_MS / 1000),
    )


@pytest.fixture
def load_main(monkeypatch):
    loaded_modules = []

    def _load(**overrides):
        for name in (
            "ADMIN_API_KEY",
            "BACKEND_SHARED_SECRET",
            "DOCS_ENABLED",
            "ENVIRONMENT",
            "NODE_ENV",
            "RAILWAY_ENVIRONMENT",
            "RAILWAY_ENVIRONMENT_NAME",
            "RATE_LIMIT_DEFAULT",
            "RATE_LIMIT_ENABLED",
            "RATE_LIMIT_OUTAGE_FALLBACK",
            "REDIS_URL",
        ):
            monkeypatch.delenv(name, raising=False)

        defaults = {
            "ENVIRONMENT": "development",
            "RATE_LIMIT_DEFAULT": "60/minute",
            "RATE_LIMIT_ENABLED": "true",
            # Prevent a developer's config/.env.local from selecting a
            # networked limiter backend during these isolated tests.
            "REDIS_URL": "",
        }
        for name, value in {**defaults, **overrides}.items():
            monkeypatch.setenv(name, value)

        sys.modules.pop("main", None)
        module = importlib.import_module("main")
        loaded_modules.append(module)
        return module

    yield _load
    sys.modules.pop("main", None)
    loaded_modules.clear()


def _signed_headers(
    method: str,
    path: str,
    body: bytes = b"",
    *,
    timestamp: str = str(NOW_MS),
) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-quantiv-timestamp": timestamp,
        "x-quantiv-signature": _expected_sig(SECRET, method, path, timestamp, body),
    }


def _prediction(req) -> MLPredictResponse:
    return MLPredictResponse(
        symbol=req.symbol,
        horizon_days=req.horizon_days,
        em_ml_pct=0.07,
        em_ml_abs=7.0,
        quantiles={10: 0.01, 50: 0.05, 90: 0.17},
        spot_used=req.spot_override or 100.0,
        feature_snapshot_date="2026-08-21",
        earnings_date=req.earnings_date,
        source="live",
        served_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )


def test_hmac_routes_predict_and_batch_requests(load_main, monkeypatch):
    main = load_main(BACKEND_SHARED_SECRET=SECRET)

    async def fake_predict(req):
        return _prediction(req)

    monkeypatch.setattr(ml_predict, "_predict_response", fake_predict)
    client = TestClient(main.app)

    predict_body = json.dumps(
        {"symbol": "brk.b", "horizon_days": 7, "spot_override": 100.0},
        separators=(",", ":"),
    ).encode()
    predict = client.post(
        "/api/ml/predict",
        content=predict_body,
        headers=_signed_headers("POST", "/api/ml/predict", predict_body),
    )

    batch_body = json.dumps(
        {
            "items": [
                {"symbol": "AAPL", "horizon_days": 7, "spot_override": 200.0},
                {"symbol": "BF-B", "horizon_days": 14, "spot_override": 50.0},
            ]
        },
        separators=(",", ":"),
    ).encode()
    batch = client.post(
        "/api/ml/batch-predict",
        content=batch_body,
        headers=_signed_headers("POST", "/api/ml/batch-predict", batch_body),
    )

    assert predict.status_code == 200
    assert predict.json()["symbol"] == "BRK.B"
    assert batch.status_code == 200
    assert [item["symbol"] for item in batch.json()["items"]] == ["AAPL", "BF-B"]


def test_hmac_rejects_body_tampering_and_stale_timestamp(load_main, monkeypatch):
    main = load_main(BACKEND_SHARED_SECRET=SECRET)

    async def fake_predict(req):
        return _prediction(req)

    monkeypatch.setattr(ml_predict, "_predict_response", fake_predict)
    client = TestClient(main.app)

    original = b'{"symbol":"AAPL","horizon_days":7}'
    tampered = b'{"symbol":"MSFT","horizon_days":7}'
    tampered_response = client.post(
        "/api/ml/predict",
        content=tampered,
        headers=_signed_headers("POST", "/api/ml/predict", original),
    )

    stale_timestamp = "1700000000000"
    stale_response = client.post(
        "/api/ml/predict",
        content=original,
        headers=_signed_headers(
            "POST",
            "/api/ml/predict",
            original,
            timestamp=stale_timestamp,
        ),
    )

    assert tampered_response.status_code == 401
    assert tampered_response.json() == {"detail": "bad signature"}
    assert stale_response.status_code == 401
    assert stale_response.json() == {"detail": "timestamp out of window"}


def test_hmac_rejections_do_not_consume_rate_limit(load_main, monkeypatch):
    main = load_main(
        BACKEND_SHARED_SECRET=SECRET,
        RATE_LIMIT_DEFAULT="1/minute",
    )

    async def fake_predict(req):
        return _prediction(req)

    monkeypatch.setattr(ml_predict, "_predict_response", fake_predict)
    client = TestClient(main.app)
    body = b'{"symbol":"AAPL","horizon_days":7}'

    assert client.post("/api/ml/predict", content=body).status_code == 401
    assert client.post("/api/ml/predict", content=body).status_code == 401

    headers = _signed_headers("POST", "/api/ml/predict", body)
    assert client.post("/api/ml/predict", content=body, headers=headers).status_code == 200
    assert client.post("/api/ml/predict", content=body, headers=headers).status_code == 429


def test_ml_routes_reject_malformed_symbols(load_main, monkeypatch):
    main = load_main(BACKEND_SHARED_SECRET=SECRET)
    called = False

    async def fake_predict(req):
        nonlocal called
        called = True
        return _prediction(req)

    monkeypatch.setattr(ml_predict, "_predict_response", fake_predict)
    client = TestClient(main.app)

    for symbol in ("-SPY", "AAPL$", "../../ETC", "AAPL/US"):
        body = json.dumps(
            {"symbol": symbol, "horizon_days": 7},
            separators=(",", ":"),
        ).encode()
        response = client.post(
            "/api/ml/predict",
            content=body,
            headers=_signed_headers("POST", "/api/ml/predict", body),
        )
        assert response.status_code == 422

    assert called is False


def test_docs_default_off_in_production_and_overrideable(load_main):
    production = load_main(
        ENVIRONMENT="",
        RAILWAY_ENVIRONMENT_NAME="production",
        BACKEND_SHARED_SECRET=SECRET,
    )
    production_client = TestClient(production.app)

    assert production_client.get("/docs").status_code == 401
    signed_docs = production_client.get(
        "/docs",
        headers=_signed_headers("GET", "/docs"),
    )
    assert signed_docs.status_code == 404
    assert production.app.openapi_url is None

    enabled = load_main(
        ENVIRONMENT="production",
        BACKEND_SHARED_SECRET=SECRET,
        DOCS_ENABLED="true",
    )
    enabled_client = TestClient(enabled.app)
    assert enabled_client.get("/docs").status_code == 200
    assert enabled_client.get("/docs/oauth2-redirect").status_code == 200
    assert enabled_client.get("/openapi.json").status_code == 200
    assert enabled_client.get("/docs/not-a-route").status_code == 401

    development = load_main(ENVIRONMENT="development", BACKEND_SHARED_SECRET=SECRET)
    assert TestClient(development.app).get("/docs").status_code == 200


def test_legacy_expected_move_routes_are_not_registered(load_main):
    main = load_main(BACKEND_SHARED_SECRET=SECRET)
    client = TestClient(main.app)

    for path in (
        "/api/expected-move",
        "/em/forecast",
        "/em/history",
        "/em/expiries",
        "/em/ml-forecast",
        "/em/ml-info",
        "/api/ml/predict/AAPL",
    ):
        response = client.get(path, headers=_signed_headers("GET", path))
        assert response.status_code == 404


def test_global_rate_limit_and_exempt_operational_routes(load_main, monkeypatch):
    main = load_main(RATE_LIMIT_DEFAULT="2/minute")

    async def fake_predict(req):
        return _prediction(req)

    monkeypatch.setattr(ml_predict, "_predict_response", fake_predict)
    client = TestClient(main.app)
    body = b'{"symbol":"AAPL","horizon_days":7}'

    statuses = [
        client.post(
            "/api/ml/predict",
            content=body,
            headers={"content-type": "application/json"},
        ).status_code
        for _ in range(3)
    ]

    assert main.limiter._storage_uri == "memory://"
    assert statuses == [200, 200, 429]
    assert [client.get("/health").status_code for _ in range(4)] == [200] * 4
    assert [
        client.post("/api/admin/sync-models").status_code for _ in range(4)
    ] == [503] * 4


def test_rate_limit_kill_switch(load_main, monkeypatch):
    main = load_main(
        RATE_LIMIT_DEFAULT="1/minute",
        RATE_LIMIT_ENABLED="false",
    )

    async def fake_predict(req):
        return _prediction(req)

    monkeypatch.setattr(ml_predict, "_predict_response", fake_predict)
    client = TestClient(main.app)
    body = b'{"symbol":"AAPL","horizon_days":7}'

    assert main.limiter.enabled is False
    assert [
        client.post(
            "/api/ml/predict",
            content=body,
            headers={"content-type": "application/json"},
        ).status_code
        for _ in range(3)
    ] == [200] * 3


def test_redis_url_selects_shared_rate_limit_storage(load_main):
    main = load_main(
        RATE_LIMIT_ENABLED="false",
        REDIS_URL="redis://localhost:6379/9",
    )

    assert main.limiter._storage_uri == "redis://localhost:6379/9"
    assert main.limiter._swallow_errors is True


def test_redis_rate_limit_failure_fails_open(load_main, monkeypatch):
    main = load_main(
        REDIS_URL="redis://127.0.0.1:1/9",
        RATE_LIMIT_DEFAULT="1/minute",
    )

    async def fake_predict(req):
        return _prediction(req)

    monkeypatch.setattr(ml_predict, "_predict_response", fake_predict)
    response = TestClient(main.app).post(
        "/api/ml/predict",
        content=b'{"symbol":"AAPL","horizon_days":7}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200


def test_rate_limit_exemptions_ignore_internal_router_entries(load_main):
    main = load_main()

    # FastAPI 0.141 inserts _IncludedRouter objects without `.path`.
    main._exempt_operational_routes(
        [SimpleNamespace(), SimpleNamespace(endpoint=lambda: None)]
    )
