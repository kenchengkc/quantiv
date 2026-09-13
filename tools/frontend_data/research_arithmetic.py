"""Canonical arithmetic for the published historical research payload.

Primitive public observations are intentionally quantized by the frontend data
builder. Derived fields must therefore be calculated from those published
primitives, not independently from higher-precision source values, otherwise a
round trip through JSON can violate the strict public-contract arithmetic checks.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def normalize_public_research_arithmetic(payload: dict[str, Any]) -> dict[str, Any]:
    """Make derived event arithmetic exact from the serialized public inputs.

    The source builder rounds primitive observations such as ``implied`` and the
    realized-window prices before publication. Re-derive every dependent field
    from those already-serialized primitives and then refresh ``universe_id`` so
    the content address covers the exact arithmetic researchers receive.
    """
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("historical research payload must contain an events array")

    for index, raw_event in enumerate(events):
        if not isinstance(raw_event, dict):
            raise ValueError(f"historical research event {index} must be an object")
        window = raw_event.get("realized_window")
        if not isinstance(window, dict):
            raise ValueError(f"historical research event {index} has no realized window")

        pre_price = _finite_number(
            window.get("pre_price"), f"historical research event {index} pre_price"
        )
        post_adjusted = _finite_number(
            window.get("post_price_adjusted"),
            f"historical research event {index} post_price_adjusted",
        )
        implied = _finite_number(
            raw_event.get("implied"), f"historical research event {index} implied"
        )
        if pre_price <= 0 or post_adjusted <= 0 or implied <= 0:
            raise ValueError(
                f"historical research event {index} has nonpositive public arithmetic inputs"
            )

        actual = post_adjusted / pre_price - 1.0
        realized_abs = abs(actual)
        raw_event["actual"] = actual
        raw_event["realized_abs"] = realized_abs
        raw_event["edge"] = realized_abs - implied
        raw_event["ratio"] = realized_abs / implied
        raw_event["outside_implied"] = realized_abs > implied

    identity = {
        key: value
        for key, value in payload.items()
        if key not in {"universe_id", "generated_at"}
    }
    payload["universe_id"] = _canonical_digest(identity)
    return payload
