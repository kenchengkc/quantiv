"""HMAC auth contract tests — must stay in sync with apps/frontend/lib/backendProxy.ts."""

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


def test_middleware_rejects_missing_headers():
    app = Starlette(
        routes=[Route("/api/ml/predict", lambda r: JSONResponse({"ok": True}), methods=["POST"])]
    )
    app.add_middleware(HmacAuthMiddleware)

    with patch.dict(os.environ, {"BACKEND_SHARED_SECRET": "s3cr3t"}, clear=False):
        client = TestClient(app)
        res = client.post("/api/ml/predict", json={"symbol": "AAPL", "horizon_days": 7})
        assert res.status_code == 401


def test_middleware_accepts_valid_signature():
    secret = "s3cr3t"
    body = b'{"symbol":"AAPL","horizon_days":7}'
    ts = str(int(time.time() * 1000))
    sig = _expected_sig(secret, "POST", "/api/ml/predict", ts, body)

    app = Starlette(
        routes=[Route("/api/ml/predict", lambda r: JSONResponse({"ok": True}), methods=["POST"])]
    )
    app.add_middleware(HmacAuthMiddleware)

    with patch.dict(os.environ, {"BACKEND_SHARED_SECRET": secret}, clear=False):
        client = TestClient(app)
        res = client.post(
            "/api/ml/predict",
            content=body,
            headers={
                "content-type": "application/json",
                "x-quantiv-timestamp": ts,
                "x-quantiv-signature": sig,
            },
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True}


def test_health_exempt_without_hmac():
    with patch.dict(os.environ, {"BACKEND_SHARED_SECRET": "s3cr3t"}, clear=False):
        app = Starlette(routes=[Route("/health", lambda r: JSONResponse({"status": "ok"}))])
        app.add_middleware(HmacAuthMiddleware)
        client = TestClient(app)
        assert client.get("/health").status_code == 200
