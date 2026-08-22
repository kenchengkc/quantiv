"""HMAC request-authentication middleware for the Quantiv backend.

The Railway service is publicly reachable (CORS allow-list aside) but
only the Next.js proxy on Vercel knows BACKEND_SHARED_SECRET, so any
request without a valid HMAC pair gets 401. The contract:

  X-Quantiv-Timestamp: <millis since epoch>
  X-Quantiv-Signature: hex(hmac_sha256(secret, canonical))

Where canonical =
  f"{method}\n{path}\n{timestamp}\n{sha256_hex(body)}"

Symmetric with apps/frontend/lib/backendProxy.ts.

Exempt paths:
  - /health (so Railway checks work)
  - enabled docs/openapi routes supplied by main.py
  - /api/admin/* (those use the X-API-Key header instead — separate threat
    model, separate secret)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from collections.abc import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# Reject requests whose timestamp is more than this many seconds off the
# server clock in either direction. 30s tolerates normal cross-host clock
# skew while making a captured-header replay window impractically small.
MAX_TIMESTAMP_SKEW_SECONDS = 30

# Paths the middleware always skips entirely.
_EXEMPT_PATHS = frozenset({"/health"})
_EXEMPT_PREFIXES = ("/api/admin/",)  # X-API-Key on its own dependency


def _exempt(path: str, extras: Iterable[str] = ()) -> bool:
    return (
        path in _EXEMPT_PATHS
        or path in extras
        or path.startswith(_EXEMPT_PREFIXES)
    )


def _canonical(method: str, path: str, timestamp: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{method}\n{path}\n{timestamp}\n{body_hash}"


def _expected_sig(secret: str, method: str, path: str, timestamp: str, body: bytes) -> str:
    canonical = _canonical(method, path, timestamp, body)
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=401)


class HmacAuthMiddleware(BaseHTTPMiddleware):
    """Verify the X-Quantiv-Signature header against the request body.

    Skips auth when BACKEND_SHARED_SECRET isn't set — useful for local
    `python main.py` runs that don't have the env wired up. On Railway the
    env var must be set, otherwise unauth'd traffic walks straight in.
    """

    def __init__(self, app, extra_exempt: Iterable[str] = ()) -> None:
        super().__init__(app)
        self._extra_exempt = tuple(extra_exempt)

    async def dispatch(self, request: Request, call_next):
        secret = os.getenv("BACKEND_SHARED_SECRET")
        if not secret:
            # Local dev without the secret wired — let the request through.
            return await call_next(request)

        if _exempt(request.url.path, self._extra_exempt):
            return await call_next(request)

        timestamp = request.headers.get("x-quantiv-timestamp", "")
        signature = request.headers.get("x-quantiv-signature", "")
        if not timestamp or not signature:
            return _unauthorized("missing HMAC headers")

        try:
            ts_ms = int(timestamp)
        except ValueError:
            return _unauthorized("invalid timestamp")
        now_ms = int(time.time() * 1000)
        if abs(now_ms - ts_ms) > MAX_TIMESTAMP_SKEW_SECONDS * 1000:
            return _unauthorized("timestamp out of window")

        # Starlette can only read the body once; cache it and patch the
        # receive callable so the downstream handler still sees it.
        body = await request.body()
        expected = _expected_sig(secret, request.method, request.url.path, timestamp, body)
        if not hmac.compare_digest(expected, signature):
            return _unauthorized("bad signature")

        async def _replay() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = _replay  # type: ignore[attr-defined]
        response: Response = await call_next(request)
        return response


__all__ = ["MAX_TIMESTAMP_SKEW_SECONDS", "HmacAuthMiddleware"]
