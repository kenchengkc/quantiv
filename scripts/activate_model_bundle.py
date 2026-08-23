#!/usr/bin/env python3
"""Activate the exact promoted model bundle and write a verifiable handoff receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


REQUIRED_HORIZONS = {1, 2, 3, 7, 14, 21}


def _json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_bundle_id(decision_path: Path) -> str:
    decision = _json_object(decision_path)
    if decision.get("schema") != "quantiv.model-decision.v1":
        raise ValueError("unsupported model decision schema")
    if decision.get("status") != "passed":
        raise ValueError("model decision did not pass")
    bundle_id = str(decision.get("champion_bundle_id") or "")
    if len(bundle_id) != 64 or any(char not in "0123456789abcdef" for char in bundle_id):
        raise ValueError("model decision has an invalid champion bundle ID")
    return bundle_id


def _validate_response(payload: dict[str, Any], expected: str) -> None:
    if payload.get("status") != "activated":
        raise ValueError("backend did not report an activated model bundle")
    if payload.get("bundle_id") != expected:
        raise ValueError("backend activated a different bundle than the promotion decision")
    models_dir = str(payload.get("models_dir") or "")
    if not models_dir.rstrip("/").endswith(f"/versions/{expected}"):
        raise ValueError("backend serving path does not resolve to the expected version")
    preflight = payload.get("preflight")
    if not isinstance(preflight, list):
        raise ValueError("backend response is missing native-model preflight results")
    observed_horizons: set[int] = set()
    for row in preflight:
        if not isinstance(row, dict):
            raise ValueError("backend preflight row is invalid")
        horizon = row.get("horizon_days")
        if not isinstance(horizon, int):
            raise ValueError("backend preflight horizon is invalid")
        if row.get("quantile_model_count") != 5:
            raise ValueError(f"T-{horizon} did not preflight all quantile heads")
        if not isinstance(row.get("feature_count"), int) or row["feature_count"] <= 0:
            raise ValueError(f"T-{horizon} did not report a usable feature schema")
        schema_hash = str(row.get("feature_schema_hash") or "")
        if len(schema_hash) != 64:
            raise ValueError(f"T-{horizon} did not report a feature-schema digest")
        observed_horizons.add(horizon)
    if observed_horizons != REQUIRED_HORIZONS:
        raise ValueError(
            f"backend preflight horizons {sorted(observed_horizons)} do not match "
            f"required horizons {sorted(REQUIRED_HORIZONS)}"
        )


def activate(
    *,
    backend_url: str,
    admin_api_key: str,
    decision_path: Path,
    receipt_path: Path,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    parsed_url = urlparse(backend_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("backend URL must be an absolute HTTP(S) URL")
    if not admin_api_key:
        raise ValueError("ADMIN_API_KEY is required for a serving activation")

    expected = expected_bundle_id(decision_path)
    endpoint = backend_url.rstrip("/") + "/api/admin/sync-models"
    body = json.dumps({"expected_bundle_id": expected}).encode()
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": admin_api_key},
    )
    try:
        with opener(request, timeout=90) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"serving activation failed with HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"serving activation request failed: {type(exc).__name__}") from exc

    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise ValueError("backend activation response was not JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("backend activation response was not an object")
    _validate_response(payload, expected)

    core = {
        "schema": "quantiv.serving-activation.v1",
        "status": "passed",
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "expected_bundle_id": expected,
        "activated_bundle_id": payload["bundle_id"],
        "decision_sha256": _sha256(decision_path),
        "backend_origin": f"{parsed_url.scheme}://{parsed_url.netloc}",
        "preflight": payload["preflight"],
    }
    receipt = {
        **core,
        "receipt_id": "sha256:" + hashlib.sha256(_canonical(core)).hexdigest(),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    temporary.replace(receipt_path)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-url",
        default=os.getenv("RAILWAY_BACKEND_URL") or "https://api.usequantiv.com",
    )
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    key = os.getenv("ADMIN_API_KEY", "")
    receipt = activate(
        backend_url=args.backend_url,
        admin_api_key=key,
        decision_path=args.decision,
        receipt_path=args.receipt,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
