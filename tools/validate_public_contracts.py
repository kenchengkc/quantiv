#!/usr/bin/env python3
"""Validate Quantiv's committed public research contracts without extra deps."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC = REPO_ROOT / "apps" / "frontend" / "public"
SCHEMAS = REPO_ROOT / "schemas"
SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class ContractError(ValueError):
    pass


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON {path}: {exc}") from exc


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{label} must be an array")
    return value


def _required(obj: dict[str, Any], label: str, *keys: str) -> None:
    missing = [key for key in keys if key not in obj]
    if missing:
        raise ContractError(f"{label} missing required keys: {', '.join(missing)}")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{label} must be a non-empty string")
    return value


def validate_schema_documents() -> None:
    files = sorted(SCHEMAS.glob("*.schema.json"))
    if not files:
        raise ContractError("no public JSON Schema files found")
    for path in files:
        schema = _object(_read(path), str(path))
        _required(schema, str(path), "$schema", "$id", "title", "type")
        if schema["$schema"] != "https://json-schema.org/draft/2020-12/schema":
            raise ContractError(f"{path} must use JSON Schema draft 2020-12")
        if schema["type"] != "object":
            raise ContractError(f"{path} top-level type must be object")


def validate_screener() -> None:
    path = PUBLIC / "screener.json"
    payload = _object(_read(path), str(path))
    _required(payload, "screener", "metadata", "events")
    metadata = _object(payload["metadata"], "screener.metadata")
    _required(metadata, "screener.metadata", "version", "as_of_date", "generated_at", "event_count")
    if metadata["version"] != "v1":
        raise ContractError("screener.metadata.version must be v1")
    events = _list(payload["events"], "screener.events")
    if metadata["event_count"] != len(events):
        raise ContractError("screener.metadata.event_count must equal len(events)")
    for index, event_raw in enumerate(events):
        event = _object(event_raw, f"screener.events[{index}]")
        _required(event, f"screener.events[{index}]", "ticker", "earnings_date", "as_of_date", "em_method")
        symbol = _string(event["ticker"], f"screener.events[{index}].ticker")
        if not SYMBOL_RE.fullmatch(symbol):
            raise ContractError(f"invalid ticker in screener row: {symbol}")


def validate_symbol_payloads() -> None:
    paths = sorted((PUBLIC / "symbols").glob("*.json"))
    if not paths:
        raise ContractError("no symbol research payloads found")
    for path in paths:
        payload = _object(_read(path), str(path))
        _required(payload, path.name, "symbol", "as_of_date", "straddle_features", "earnings_history")
        symbol = _string(payload["symbol"], f"{path.name}.symbol")
        if symbol != path.stem or not SYMBOL_RE.fullmatch(symbol):
            raise ContractError(f"symbol payload/path mismatch: {path.name} -> {symbol}")
        _list(payload["straddle_features"], f"{path.name}.straddle_features")
        _list(payload["earnings_history"], f"{path.name}.earnings_history")


def validate_dashboard_evidence() -> None:
    path = PUBLIC / "evidence" / "forecast.json"
    payload = _object(_read(path), str(path))
    _required(payload, "forecast evidence", "schema", "receipt_id", "validated_at", "quality", "coverage", "controls", "artifact_bundles")
    if payload["schema"] != "quantiv.dashboard-evidence.v1":
        raise ContractError("forecast evidence schema discriminator changed")
    receipt = _string(payload["receipt_id"], "forecast evidence.receipt_id")
    if not SHA256_RE.fullmatch(receipt):
        raise ContractError("forecast evidence receipt_id must be SHA-256")
    quality = _object(payload["quality"], "forecast evidence.quality")
    _required(quality, "forecast evidence.quality", "status", "issue_count", "issue_codes")
    coverage = _object(payload["coverage"], "forecast evidence.coverage")
    _required(coverage, "forecast evidence.coverage", "rows", "symbols", "events", "horizons")


def validate_control_plane() -> None:
    path = PUBLIC / "control-plane.json"
    payload = _object(_read(path), str(path))
    _required(payload, "control plane", "schema", "generated_at", "status", "publication_eligible", "data", "model", "release", "exceptions")
    if payload["schema"] != "quantiv.control-plane.v2":
        raise ContractError("control-plane schema discriminator changed")
    if not isinstance(payload["publication_eligible"], bool):
        raise ContractError("control-plane publication_eligible must be boolean")
    _object(payload["data"], "control-plane.data")
    _object(payload["model"], "control-plane.model")
    _object(payload["release"], "control-plane.release")
    _list(payload["exceptions"], "control-plane.exceptions")


def validate_model_validation() -> None:
    path = PUBLIC / "evidence" / "model-validation.json"
    payload = _object(_read(path), str(path))
    _required(payload, "model validation", "schema", "generated_at", "model_source", "summary", "horizons", "validation_protocol", "current_evidence")
    if payload["schema"] != "quantiv.public-model-validation.v1":
        raise ContractError("model-validation schema discriminator changed")
    source = _object(payload["model_source"], "model-validation.model_source")
    _required(source, "model-validation.model_source", "kind", "bundle_id", "artifact_sha256")
    if source["kind"] not in {"signed_champion", "baked_fallback"}:
        raise ContractError("model-validation model source kind is unsupported")
    summary = _object(payload["summary"], "model-validation.summary")
    _required(summary, "model-validation.summary", "supported_horizons", "validation_row_observations", "weighted_model_mae", "weighted_straddle_mae", "weighted_relative_mae_improvement", "weighted_coverage")
    horizons = _list(payload["horizons"], "model-validation.horizons")
    expected = list(summary["supported_horizons"])
    actual = [row.get("horizon_days") for row in horizons if isinstance(row, dict)]
    if actual != expected:
        raise ContractError(f"model-validation horizon rows {actual} do not match summary {expected}")
    protocol = _object(payload["validation_protocol"], "model-validation.validation_protocol")
    if protocol.get("decision_scope") != "end_of_day_research" or protocol.get("live_trading_eligible") is not False:
        raise ContractError("model-validation decision scope must remain EOD research-only")


def validate_repo() -> list[str]:
    checks: list[tuple[str, Callable[[], None]]] = [
        ("schema documents", validate_schema_documents),
        ("screener", validate_screener),
        ("symbol payloads", validate_symbol_payloads),
        ("forecast evidence", validate_dashboard_evidence),
        ("control plane", validate_control_plane),
        ("model validation", validate_model_validation),
    ]
    passed: list[str] = []
    for name, check in checks:
        check()
        passed.append(name)
    return passed


def main() -> int:
    try:
        passed = validate_repo()
    except ContractError as exc:
        print(f"public contract validation failed: {exc}")
        return 1
    print("public contracts passed: " + ", ".join(passed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
