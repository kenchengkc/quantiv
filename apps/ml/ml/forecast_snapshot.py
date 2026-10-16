"""Stable identity for a published scoring snapshot across Parquet and JSONB."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping


def _number(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("snapshot numbers cannot be booleans")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("snapshot numbers must be finite")
    encoded = format(number, "f")
    if number == 0:
        return "0"
    return encoded.rstrip("0").rstrip(".") if "." in encoded else encoded


def forecast_snapshot_key(row: Mapping[str, Any]) -> str:
    return (f"{str(row['act_symbol']).strip().upper()}|"
            f"{date.fromisoformat(str(row['earnings_date'])[:10]).isoformat()}|{int(row['model_horizon'])}")


def forecast_snapshot_id(row: Mapping[str, Any]) -> str:
    vector = row["feature_vector"]
    if isinstance(vector, str):
        vector = json.loads(vector, parse_float=Decimal, parse_int=Decimal)
    if not isinstance(vector, Mapping) or any(not isinstance(key, str) for key in vector):
        raise ValueError("snapshot feature vector must be an object")
    scored = row["scored_at"]
    if not isinstance(scored, datetime):
        scored = datetime.fromisoformat(str(scored).replace("Z", "+00:00"))
    if scored.tzinfo is None:
        raise ValueError("snapshot scoring timestamp must include a timezone")
    horizon = int(row["model_horizon"])
    if horizon <= 0 or str(horizon) != _number(row["model_horizon"]):
        raise ValueError("snapshot horizon must be a positive integer")
    payload = {
        "symbol": str(row["act_symbol"]).strip().upper(),
        "earnings_date": date.fromisoformat(str(row["earnings_date"])[:10]).isoformat(),
        "snapshot_date": date.fromisoformat(str(row["snapshot_date"])[:10]).isoformat(),
        "horizon": horizon, "bundle_id": str(row["model_bundle_id"]),
        "scored_at": scored.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        "spot": _number(row["spot_price"]),
        "features": {key: None if value is None else _number(value) for key, value in vector.items()},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
