import json

import pytest

from ml.model_protocol import (
    CAUSAL_FEATURE_PROTOCOL,
    LEGACY_FEATURE_PROTOCOL,
    SESSION_TARGET_PROTOCOL,
    bundle_feature_protocol,
    feature_protocol,
    target_protocol,
)


def test_legacy_is_explicit_and_unknown_protocol_fails_closed():
    assert feature_protocol({}) == LEGACY_FEATURE_PROTOCOL
    assert feature_protocol({"feature_protocol": CAUSAL_FEATURE_PROTOCOL}) == CAUSAL_FEATURE_PROTOCOL
    assert target_protocol({"feature_protocol": CAUSAL_FEATURE_PROTOCOL}) == SESSION_TARGET_PROTOCOL
    with pytest.raises(ValueError, match="unsupported feature protocol"):
        feature_protocol({"feature_protocol": "unknown"})
    with pytest.raises(ValueError, match="incompatible target protocol"):
        target_protocol({"feature_protocol": CAUSAL_FEATURE_PROTOCOL, "target_protocol": "old"})


def test_bundle_rejects_mixed_feature_semantics(tmp_path):
    for horizon, protocol in ((1, LEGACY_FEATURE_PROTOCOL), (2, CAUSAL_FEATURE_PROTOCOL)):
        (tmp_path / f"metadata_T{horizon}.json").write_text(json.dumps({"feature_protocol": protocol}))
    with pytest.raises(ValueError, match="mixed feature protocols"):
        bundle_feature_protocol(tmp_path, horizons=(1, 2))


def test_bundle_reads_each_horizon_and_requires_metadata(tmp_path):
    (tmp_path / "metadata_T1.json").write_text(json.dumps({"feature_protocol": CAUSAL_FEATURE_PROTOCOL}))
    assert bundle_feature_protocol(tmp_path, horizons=(1,)) == CAUSAL_FEATURE_PROTOCOL
    with pytest.raises(FileNotFoundError):
        bundle_feature_protocol(tmp_path, horizons=(1, 2))
