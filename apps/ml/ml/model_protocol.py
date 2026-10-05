"""Version the semantics of model inputs and realized earnings targets.

Legacy signed bundles predate this contract. Absence of a protocol is an
explicit legacy interpretation, never permission to use repaired features.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

LEGACY_FEATURE_PROTOCOL = "quantiv.earnings-legacy.v1"
CAUSAL_FEATURE_PROTOCOL = "quantiv.earnings-causal.v2"
LEGACY_TARGET_PROTOCOL = "quantiv.generic-reaction.v1"
SESSION_TARGET_PROTOCOL = "quantiv.session-reaction.v2"
FEATURE_PROTOCOL_LEGACY = LEGACY_FEATURE_PROTOCOL
FEATURE_PROTOCOL_CAUSAL = CAUSAL_FEATURE_PROTOCOL
TARGET_PROTOCOL_CAUSAL = SESSION_TARGET_PROTOCOL


def feature_protocol(metadata: Mapping[str, Any]) -> str:
    value = metadata.get("feature_protocol", LEGACY_FEATURE_PROTOCOL)
    if value not in (LEGACY_FEATURE_PROTOCOL, CAUSAL_FEATURE_PROTOCOL):
        raise ValueError(f"unsupported feature protocol: {value!r}")
    return str(value)


resolve_feature_protocol = feature_protocol


def target_protocol(metadata: Mapping[str, Any]) -> str:
    expected = (
        SESSION_TARGET_PROTOCOL
        if feature_protocol(metadata) == CAUSAL_FEATURE_PROTOCOL
        else LEGACY_TARGET_PROTOCOL
    )
    value = metadata.get("target_protocol", expected)
    if value != expected:
        raise ValueError(f"incompatible target protocol: {value!r}; expected {expected}")
    return expected


def bundle_feature_protocol(
    models_dir: Path, *, horizons: Sequence[int] = (1, 2, 3, 7, 14, 21)
) -> str:
    protocols = set()
    for horizon in horizons:
        metadata = json.loads((models_dir / f"metadata_T{horizon}.json").read_text())
        protocols.add(feature_protocol(metadata))
        target_protocol(metadata)
    if len(protocols) != 1:
        raise ValueError(f"mixed feature protocols in bundle: {sorted(protocols)}")
    return protocols.pop()
