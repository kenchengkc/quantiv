"""Drift samples come from recorded vectors, never reconstructed history."""

import json

import numpy as np
import pandas as pd
import pytest

from ml.model_protocol import LEGACY_FEATURE_PROTOCOL, LEGACY_TARGET_PROTOCOL


NOW = pd.Timestamp("2026-10-06T01:00:00Z")


def _bundle(tmp_path, *, optionless=False):
    root = tmp_path / "bundle"
    root.mkdir()
    reference = {"feature": {"missing_rate": 0., "cuts": [.5], "probabilities": [.5, .5]},
                 "straddle_pct": {"missing_rate": 0., "cuts": [.08], "probabilities": [.5, .5]}}
    cohorts = {"strict_options": {"rows": 200, "feature_reference": reference}}
    if optionless:
        cohorts["optionless"] = {"rows": 200, "feature_reference": {
            **reference, "straddle_pct": {"missing_rate": 1., "cuts": [], "probabilities": []}}}
    (root / "metadata_T1.json").write_text(json.dumps({
        "feature_cols": ["feature", "straddle_pct"], "supported_cohorts": list(cohorts),
        "feature_reference": reference, "cohort_reference": cohorts,
    }))
    return root


def _rows(count, *, snapshot="2026-10-05", historical=False, cohort="strict_options", offset=0):
    baseline = .08 if cohort == "strict_options" else np.nan
    vectors = [json.dumps({"feature": i % 2, "straddle_pct": .08 if cohort == "strict_options" else None})
               for i in range(count)]
    rows = pd.DataFrame({
        "act_symbol": [f"S{i + offset}" for i in range(count)], "earnings_date": "2026-10-20",
        "snapshot_date": snapshot, "model_horizon": 1, "model_bundle_id": "bundle-a",
        "feature_protocol": LEGACY_FEATURE_PROTOCOL, "target_protocol": LEGACY_TARGET_PROTOCOL,
        "__cohort": cohort, "em_math_pct": baseline, "feature_vector": vectors,
        "scored_at": f"{snapshot}T21:15:00Z",
    })
    if historical:
        rows["bundle_id"] = rows["model_bundle_id"]
        rows["recorded_at"] = rows["scored_at"]
    return rows


def _assess(current, bundle, history=None):
    from ml.model_control import rolling_cohort_drift_report
    return rolling_cohort_drift_report(current, bundle, history=history,
                                       bundle_id="bundle-a", horizons=[1], as_of=NOW)


def test_signed_observed_history_supplements_sparse_current_cohort(tmp_path):
    report = _assess(_rows(20, offset=80), _bundle(tmp_path),
                     _rows(80, snapshot="2026-09-30", historical=True))
    assert report["status"] in {"passed", "warning"}
    assert report["rolling_evidence"]["historical_rows"] == 80
    assert report["horizons"]["1"]["rows"] == 100


def test_first_run_and_insufficient_observed_history_remain_held(tmp_path):
    bundle = _bundle(tmp_path)
    assert _assess(_rows(20), bundle)["status"] == "insufficient_data"
    assert _assess(_rows(20), bundle, _rows(79, snapshot="2026-09-30", historical=True))["status"] == "insufficient_data"


@pytest.mark.parametrize("mutation", ["bundle", "feature_protocol", "target_protocol", "horizon", "cohort", "snapshot", "recorded", "scored", "future", "missing_vector"])
def test_wrong_identity_stale_or_unrecorded_vectors_cannot_fill_the_minimum(tmp_path, mutation):
    history = _rows(80, snapshot="2026-09-30", historical=True)
    if mutation == "bundle":
        history["bundle_id"] = "other-bundle"
    elif mutation in {"feature_protocol", "target_protocol"}:
        history[mutation] = "other-protocol"
    elif mutation == "horizon":
        history["model_horizon"] = 7
    elif mutation == "cohort":
        history["__cohort"] = "optionless"
    elif mutation == "snapshot":
        history["snapshot_date"] = "2026-08-01"
    elif mutation == "recorded":
        history["recorded_at"] = "2026-08-01T21:15:00Z"
    elif mutation == "scored":
        history["scored_at"] = "2026-08-01T21:15:00Z"
    elif mutation == "future":
        history["recorded_at"] = "2026-10-07T21:15:00Z"
    elif mutation == "missing_vector":
        history = history.drop(columns="feature_vector")
    assert _assess(_rows(20, offset=80), _bundle(tmp_path), history)["status"] == "insufficient_data"


def test_repeated_observation_identity_does_not_inflate_sample_size(tmp_path):
    history = _rows(40, snapshot="2026-09-30", historical=True)
    history = pd.concat([history, history], ignore_index=True)
    report = _assess(_rows(20, offset=80), _bundle(tmp_path), history)
    assert report["status"] == "insufficient_data"
    assert report["horizons"]["1"]["rows"] == 60


def test_history_cannot_mask_current_feature_corruption(tmp_path):
    current = _rows(20, offset=200)
    current["feature_vector"] = json.dumps({"feature": None, "straddle_pct": .08})
    report = _assess(current, _bundle(tmp_path), _rows(200, snapshot="2026-09-30", historical=True))
    assert report["status"] == "critical"
    assert report["current_snapshot"]["corruption_status"] == "critical"


def test_history_cannot_mask_unsupported_current_cohort(tmp_path):
    report = _assess(_rows(5, cohort="optionless"), _bundle(tmp_path),
                     _rows(200, snapshot="2026-09-30", historical=True))
    assert report["status"] == "unsupported_cohort"


def test_history_cannot_replace_a_stale_current_snapshot(tmp_path):
    report = _assess(_rows(20, snapshot="2026-09-30"), _bundle(tmp_path),
                     _rows(200, snapshot="2026-09-30", historical=True))
    assert report["status"] == "critical"
    assert report["rolling_evidence"]["current_snapshot_fresh"] is False


def test_each_current_serving_cohort_needs_its_own_rolling_sample(tmp_path):
    current = pd.concat([_rows(20, offset=80), _rows(5, cohort="optionless", offset=200)], ignore_index=True)
    history = _rows(80, snapshot="2026-09-30", historical=True)
    assert _assess(current, _bundle(tmp_path, optionless=True), history)["status"] == "insufficient_data"


@pytest.mark.parametrize("mutation", ["log_spot", "timing", "malformed"])
def test_causal_current_hard_contracts_are_independent_of_the_rolling_sample(tmp_path, mutation):
    from ml.model_protocol import CAUSAL_FEATURE_PROTOCOL, SESSION_TARGET_PROTOCOL

    bundle = _bundle(tmp_path)
    path = bundle / "metadata_T1.json"
    metadata = json.loads(path.read_text())
    metadata.update(feature_protocol=CAUSAL_FEATURE_PROTOCOL, target_protocol=SESSION_TARGET_PROTOCOL,
                    feature_cols=["feature", "straddle_pct", "log_spot", "timing_bmo", "timing_amc"])
    path.write_text(json.dumps(metadata))
    current, history = _rows(1, offset=200), _rows(200, snapshot="2026-09-30", historical=True)
    for rows in (current, history):
        rows["feature_protocol"] = CAUSAL_FEATURE_PROTOCOL
        rows["target_protocol"] = SESSION_TARGET_PROTOCOL
        rows["timing"] = "bmo"
        rows["feature_vector"] = json.dumps({"feature": .5, "straddle_pct": .08,
                                             "log_spot": 4.6, "timing_bmo": 1., "timing_amc": 0.})
    if mutation == "log_spot":
        vector = json.loads(current.loc[0, "feature_vector"])
        vector["log_spot"] = None
        current["feature_vector"] = json.dumps(vector)
    elif mutation == "timing":
        current["timing"] = "amc"
    else:
        current["feature_vector"] = "{"
    assert _assess(current, bundle, history)["status"] == "critical"
