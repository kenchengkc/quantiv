"""HMAC authentication plus request-level operational telemetry.

Every backend request receives a correlation ID and one structured completion
record. Railway's managed runtime logs are the backend telemetry sink; Vercel
provides the corresponding managed frontend runtime/analytics surface.  Logs
intentionally exclude bodies, query strings, credentials, and signatures.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
import uuid
from collections.abc import Iterable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = structlog.get_logger(__name__)
MAX_TIMESTAMP_SKEW_SECONDS = 30
_EXEMPT_PATHS = frozenset({"/health"})
_EXEMPT_PREFIXES = ("/api/admin/",)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _exempt(path: str, extras: Iterable[str] = ()) -> bool:
    return path in _EXEMPT_PATHS or path in extras or path.startswith(_EXEMPT_PREFIXES)


def _canonical(method: str, path: str, timestamp: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{method}\n{path}\n{timestamp}\n{body_hash}"


def _expected_sig(secret: str, method: str, path: str, timestamp: str, body: bytes) -> str:
    canonical = _canonical(method, path, timestamp, body)
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=401)


def _correlation_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "").strip()
    return supplied if _REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex


class HmacAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate protected routes and emit privacy-safe request telemetry."""

    def __init__(self, app, extra_exempt: Iterable[str] = ()) -> None:
        super().__init__(app)
        self._extra_exempt = tuple(extra_exempt)

    async def _authenticated_response(self, request: Request, call_next) -> Response:
        secret = os.getenv("BACKEND_SHARED_SECRET")
        if not secret or _exempt(request.url.path, self._extra_exempt):
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

        body = await request.body()
        expected = _expected_sig(secret, request.method, request.url.path, timestamp, body)
        if not hmac.compare_digest(expected, signature):
            return _unauthorized("bad signature")

        async def _replay() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = _replay  # type: ignore[attr-defined]
        return await call_next(request)

    async def dispatch(self, request: Request, call_next):
        request_id = _correlation_id(request)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await self._authenticated_response(request, call_next)
        except Exception:  # noqa: BLE001 - record then preserve framework exception behavior
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            logger.exception(
                "backend_request_exception",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "backend_request_complete",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


__all__ = ["MAX_TIMESTAMP_SKEW_SECONDS", "HmacAuthMiddleware"]
