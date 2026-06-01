#!/usr/bin/env python3
"""Provider capability probing without storing raw vendor payloads."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable

import requests

from provider_specs import EndpointSpec, endpoint_specs
from provider_utils import (
    ProviderQuotaError,
    ProviderUsageLedger,
    api_key_for_provider,
    default_data_dir,
    write_json,
)


CAPABILITIES_PATH = default_data_dir() / "provider_capabilities.json"
ENTITLEMENT_RETRY_DAYS = 30
QUOTA_RETRY_DAYS = 1


def _retry_after(days: int, now: datetime) -> str:
    return (now + timedelta(days=days)).date().isoformat()


def _parse_json_response(response: Any) -> tuple[Any | None, str | None]:
    try:
        return response.json(), None
    except Exception as exc:
        return None, f"invalid JSON response: {exc}"


def _csv_row_count(text: str) -> tuple[int, list[str]]:
    reader = csv.reader(StringIO(text or ""))
    try:
        header = next(reader)
    except StopIteration:
        return 0, []
    rows = sum(1 for row in reader if any(cell.strip() for cell in row))
    return rows, header


def _first_dict(values: list[Any]) -> dict[str, Any] | None:
    for item in values:
        if isinstance(item, dict):
            return item
    return None


def summarize_payload(payload: Any, *, response_kind: str = "json") -> dict[str, Any]:
    if response_kind == "csv":
        rows, header = _csv_row_count(str(payload or ""))
        return {
            "kind": "csv",
            "row_count": rows,
            "columns": header[:30],
            "has_data": rows > 0,
        }

    if isinstance(payload, list):
        sample = _first_dict(payload)
        return {
            "kind": "json_list",
            "row_count": len(payload),
            "top_level_keys": [],
            "sample_keys": sorted(str(k) for k in sample.keys())[:30] if sample else [],
            "has_data": bool(payload),
        }

    if isinstance(payload, dict):
        top_keys = sorted(str(k) for k in payload.keys())[:30]
        rows: list[Any] = []
        found_row_container = False
        for key in (
            "results",
            "feed",
            "data",
            "press_releases",
            "annualEarnings",
            "quarterlyEarnings",
            "put_call_ratios",
            "volume_open_interest_ratios",
            "values",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                found_row_container = True
                rows = value
                break
        sample = _first_dict(rows)
        if rows:
            row_count = len(rows)
            sample_keys = sorted(str(k) for k in sample.keys())[:30] if sample else []
        elif found_row_container:
            row_count = 0
            sample_keys = []
        else:
            row_count = 1 if payload and not _provider_error_message(payload) else 0
            sample_keys = []
        return {
            "kind": "json_object",
            "row_count": row_count,
            "top_level_keys": top_keys,
            "sample_keys": sample_keys,
            "has_data": row_count > 0,
        }

    return {
        "kind": type(payload).__name__,
        "row_count": 0,
        "top_level_keys": [],
        "sample_keys": [],
        "has_data": False,
    }


def _provider_error_message(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in (
            "Error Message",
            "Note",
            "Information",
            "message",
            "error",
            "error_message",
            "statusMessage",
        ):
            value = payload.get(key)
            if value:
                return str(value)
        if payload.get("status") == "error":
            return str(payload)
    if isinstance(payload, str):
        return payload
    return None


def _classify_error_text(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ("premium", "subscription", "not entitled", "not available under", "forbidden")):
        return "entitlement_denied"
    if any(term in lower for term in ("rate limit", "api call frequency", "daily", "quota", "too many requests")):
        return "quota_limited"
    return "provider_error"


def classify_response(spec: EndpointSpec, response: Any, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    status_code = int(getattr(response, "status_code", 0) or 0)
    text = str(getattr(response, "text", "") or "")
    content_type = str((getattr(response, "headers", {}) or {}).get("content-type", "")).lower()

    if status_code in {401, 402, 403}:
        return {
            "status": "entitlement_denied",
            "http_status": status_code,
            "error": text[:240] or f"HTTP {status_code}",
            "retry_after": _retry_after(ENTITLEMENT_RETRY_DAYS, now),
        }
    if status_code == 429:
        return {
            "status": "quota_limited",
            "http_status": status_code,
            "error": text[:240] or "HTTP 429",
            "retry_after": _retry_after(QUOTA_RETRY_DAYS, now),
        }
    if status_code >= 400:
        return {
            "status": "http_error",
            "http_status": status_code,
            "error": text[:240] or f"HTTP {status_code}",
            "retry_after": _retry_after(QUOTA_RETRY_DAYS, now),
        }

    if spec.response_kind == "csv" or "text/csv" in content_type:
        summary = summarize_payload(text, response_kind="csv")
        status = "ok" if summary["has_data"] else "empty"
        return {"status": status, "http_status": status_code, "response_shape": summary}

    payload, parse_error = _parse_json_response(response)
    if parse_error:
        return {
            "status": "malformed",
            "http_status": status_code,
            "error": parse_error[:240],
            "retry_after": _retry_after(QUOTA_RETRY_DAYS, now),
        }

    provider_error = _provider_error_message(payload)
    if provider_error:
        status = _classify_error_text(provider_error)
        retry_days = ENTITLEMENT_RETRY_DAYS if status == "entitlement_denied" else QUOTA_RETRY_DAYS
        return {
            "status": status,
            "http_status": status_code,
            "error": provider_error[:240],
            "retry_after": _retry_after(retry_days, now),
            "response_shape": summarize_payload(payload),
        }

    summary = summarize_payload(payload)
    status = "ok" if summary["has_data"] else "empty"
    out = {"status": status, "http_status": status_code, "response_shape": summary}
    if status == "empty":
        out["retry_after"] = _retry_after(QUOTA_RETRY_DAYS, now)
    return out


def load_capabilities(path: Path = CAPABILITIES_PATH) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"endpoints": {}}
    if not isinstance(body, dict):
        return {"endpoints": {}}
    if not isinstance(body.get("endpoints"), dict):
        body["endpoints"] = {}
    return body


def capability_is_ok(capabilities: dict[str, Any], endpoint_id: str) -> bool:
    endpoint = (capabilities.get("endpoints") or {}).get(endpoint_id)
    return isinstance(endpoint, dict) and endpoint.get("status") == "ok"


def probe_spec(
    spec: EndpointSpec,
    *,
    ledger: ProviderUsageLedger,
    http_get: Callable[..., Any] = requests.get,
    wait_for_minute: bool = False,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    base = {
        "provider": spec.provider,
        "endpoint": spec.id,
        "category": spec.category,
        "purpose": spec.purpose,
        "derived_table": spec.derived_table,
        "heavy": spec.heavy,
        "credit_cost": spec.credit_cost,
        "checked_at": now.isoformat(),
        "doc_url": spec.doc_url,
    }

    api_key = api_key_for_provider(spec.provider)
    if not api_key:
        return {**base, "status": "missing_key", "error": f"{spec.provider} API key missing"}

    try:
        ledger.reserve(
            spec.provider,
            spec.id,
            credits=spec.credit_cost,
            symbols=[] if not spec.symbol_scoped else [str(spec.params.get("symbol") or spec.params.get("ticker") or "")],
            wait_for_minute=wait_for_minute,
        )
    except ProviderQuotaError as exc:
        return {
            **base,
            "status": "quota_blocked",
            "error": str(exc),
            "retry_after": _retry_after(QUOTA_RETRY_DAYS, now),
        }

    try:
        response = http_get(
            spec.url,
            params=spec.request_params(api_key),
            headers=spec.request_headers(api_key),
            timeout=60,
        )
    except requests.RequestException as exc:
        return {
            **base,
            "status": "transport_error",
            "error": str(exc)[:240],
            "retry_after": _retry_after(QUOTA_RETRY_DAYS, now),
        }

    return {**base, **classify_response(spec, response, now=now)}


def write_capabilities(
    results: list[dict[str, Any]],
    *,
    output: Path = CAPABILITIES_PATH,
    sample_symbol: str,
) -> dict[str, Any]:
    existing = load_capabilities(output)
    endpoints = dict(existing.get("endpoints") or {})
    for result in results:
        endpoints[str(result["endpoint"])] = result
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_symbol": sample_symbol,
        "notes": [
            "Entitlement probes store only status/shape metadata, never raw provider payloads.",
            "entitlement_denied endpoints are retried after 30 days by default.",
        ],
        "endpoints": dict(sorted(endpoints.items())),
    }
    write_json(output, payload)
    return payload


def select_specs(
    *,
    providers: set[str] | None = None,
    include_heavy: bool = False,
    sample_symbol: str = "AAPL",
) -> list[EndpointSpec]:
    specs = endpoint_specs(sample_symbol)
    if providers:
        specs = [spec for spec in specs if spec.provider in providers]
    if not include_heavy:
        specs = [spec for spec in specs if not spec.heavy]
    return specs
