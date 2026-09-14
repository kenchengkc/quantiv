"""HMAC auth and request-telemetry contract tests."""

import hashlib
import hmac
import os
import time
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from middleware.hmac_auth import HmacAuthMiddleware, _canonical, _expected_sig


def test_canonical_format_matches_frontend_contract():
    body = b'{"symbol":"CRM","horizon_days":7}'
    ts = "1700000000000"
    canonical = _canonical("POST", "/api/ml/predict", ts, body)
    assert canonical == (
        "POST\n/api/ml/predict\n1700000000000\n" + hashlib.sha256(body).hexdigest()
    )


def test_expected_sig_known_vector():
    secret = "test-secret"
    body = b"{}"
    ts = "1"
    sig = _expected_sig(secret, "POST", "/health", ts, body)
    assert sig == hmac.new(
        secret.encode(), _canonical("POST", "/health", ts, body).encode(), hashlib.sha256
    ).hexdigest()


def _app(path: str = "/api/ml/predict") -> Starlette:
    app = Starlette(routes=[Route(path, lambda r: JSONResponse({"ok": True}), methods=["GET", "POST"])])
    app.add_middleware(HmacAuthMiddleware)
    return app


def test_middleware_rejects_missing_headers_and_correlates_response():
    with patch.dict(os.environ, {"BACKEND_SHARED_SECRET": "s3cr3t"}, clear=False):
        res = TestClient(_app()).post("/api/ml/predict", json={"symbol": "AAPL"})
        assert res.status_code == 401
        assert len(res.headers["x-request-id"]) == 32


def test_middleware_accepts_valid_signature_and_preserves_safe_request_id():
    secret = "s3cr3t"
    body = b'{"symbol":"AAPL","horizon_days":7}'
    ts = str(int(time.time() * 1000))
    sig = _expected_sig(secret, "POST", "/api/ml/predict", ts, body)
    with patch.dict(os.environ, {"BACKEND_SHARED_SECRET": secret}, clear=False):
        res = TestClient(_app()).post(
            "/api/ml/predict",
            content=body,
            headers={
                "content-type": "application/json",
                "x-quantiv-timestamp": ts,
                "x-quantiv-signature": sig,
                "x-request-id": "vercel_01.test-request",
            },
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True}
        assert res.headers["x-request-id"] == "vercel_01.test-request"


def test_unsafe_request_id_is_replaced():
    with patch.dict(os.environ, {"BACKEND_SHARED_SECRET": ""}, clear=False):
        res = TestClient(_app("/health")).get("/health", headers={"x-request-id": "bad id\nvalue"})
        assert res.status_code == 200
        assert res.headers["x-request-id"] != "bad id\nvalue"
        assert len(res.headers["x-request-id"]) == 32


def test_telemetry_survives_a_frozen_wall_clock():
    """Latency is measured on the monotonic clock, not the wall clock.

    Tests pin the replay window by freezing `hmac_auth.time`. Measuring latency
    through that same module made every one of those tests fail on a missing
    `perf_counter`, so the two clocks have to stay independent.
    """
    from types import SimpleNamespace

    from middleware import hmac_auth

    frozen = SimpleNamespace(time=lambda: 1_800_000_000.0)
    with patch.object(hmac_auth, "time", frozen), patch.dict(
        os.environ, {"BACKEND_SHARED_SECRET": ""}, clear=False
    ):
        res = TestClient(_app("/health")).get("/health")
        assert res.status_code == 200
        assert len(res.headers["x-request-id"]) == 32


def test_health_exempt_without_hmac():
    with patch.dict(os.environ, {"BACKEND_SHARED_SECRET": "s3cr3t"}, clear=False):
        app = Starlette(routes=[Route("/health", lambda r: JSONResponse({"status": "ok"}))])
        app.add_middleware(HmacAuthMiddleware)
        res = TestClient(app).get("/health")
        assert res.status_code == 200
        assert res.headers.get("x-request-id")
