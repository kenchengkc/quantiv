import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest


def fixture(tmp_path, n=200):
    candidate, champion = tmp_path / "candidate", tmp_path / "champion"
    for path in (candidate, champion):
        path.mkdir()
    metadata = {
        "horizon": 4,
        "feature_protocol": "quantiv.earnings-causal.v2",
        "target_protocol": "quantiv.session-reaction.v2",
        "supported_cohorts": ["optionless"],
        "selection_exposure": {"through_date": "2026-06-01"},
    }
    (candidate / "metadata_T4.json").write_text(json.dumps(metadata))
    actual = np.linspace(0.0001, 0.1, n)
    rows = pd.DataFrame(
        {
            "act_symbol": [f"S{i}" for i in range(n)],
            "earnings_date": pd.Timestamp("2026-07-06"),
            "snapshot_date": pd.Timestamp("2026-07-02"),
            "model_horizon": 4,
            "bundle_id": "candidate",
            "timing": "bmo",
            "recorded_at": "2026-07-02T20:00:01Z",
            "scored_at": "2026-07-02T20:00:00Z",
            "feature_protocol": metadata["feature_protocol"],
            "target_protocol": metadata["target_protocol"],
            "prediction": actual,
            "em_math_pct": np.nan,
            "hist_move_med_4q": 0.08,
            **{f"p{q:02d}": q / 1000 for q in (10, 25, 50, 75, 90)},
        }
    )
    labels = pd.DataFrame(
        {
            "__symbol": rows.act_symbol,
            "__earnings_date": rows.earnings_date,
            "__label_available_at": pd.Timestamp("2026-07-06"),
            "target": actual,
        }
    )
    contract = {
        "schema": "quantiv.prospective-admission.v1",
        "candidate_bundle_id": "candidate",
        "champion_bundle_id": "champion",
        "started_at": "2026-07-01T20:00:00Z",
        "comparators": {"4": {"optionless": "historical_median"}},
        "minimum_rows": 200,
    }
    return candidate, champion, rows, labels, contract


def evaluate(tmp_path, n=200, mutate=None):
    from ml.prospective_admission import evaluate_admission

    candidate, champion, rows, labels, contract = fixture(tmp_path, n)
    if mutate:
        mutate(rows, labels, contract)
    return evaluate_admission(
        rows,
        labels,
        candidate,
        champion,
        contract,
        now=datetime(2026, 7, 8, tzinfo=timezone.utc),
    )


def test_new_optionless_head_can_qualify_against_predeclared_baseline(tmp_path):
    report = evaluate(tmp_path)
    assert report["status"] == "passed"
    assert report["horizons"]["4"]["cohorts"]["optionless"]["rows"] == 200


def test_declared_comparators_use_both_authenticated_capability_maps(tmp_path, monkeypatch):
    from ml import prospective_admission as admission
    candidate, champion, _, _, _ = fixture(tmp_path)
    metadata = {"supported_cohorts": ["strict_options", "optionless"]}
    for directory in (candidate, champion):
        (directory / "metadata_T4.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(admission, "verify_bundle_dir", lambda directory: {
        "bundle_id": directory.name, "horizons": [4],
        "serving_capabilities": {"4": ["strict_options"] if directory == candidate else ["optionless"]},
    })
    contract = admission.declare_admission(candidate, champion, started_at="2026-07-01T20:00:00Z")
    assert contract["comparators"] == {"4": {"strict_options": "straddle"}}


def test_evaluation_contract_covers_admitted_cohorts_without_overclaim(tmp_path, monkeypatch):
    from ml import prospective_admission as admission
    candidate, champion, rows, labels, contract = fixture(tmp_path)
    metadata_path = candidate / "metadata_T4.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["supported_cohorts"] = ["strict_options", "optionless"]
    metadata_path.write_text(json.dumps(metadata))
    (candidate / "manifest.json").write_text("{}")
    monkeypatch.setattr(admission, "verify_bundle_dir", lambda directory: {
        "bundle_id": "candidate", "horizons": [4], "serving_capabilities": {"4": ["optionless"]},
    })
    report = admission.evaluate_admission(rows, labels, candidate, champion, contract,
        now=datetime(2026, 7, 8, tzinfo=timezone.utc))
    assert report["status"] == "passed"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows, labels, contract: contract.update(
            started_at="2026-07-03T00:00:00Z"
        ),
        lambda rows, labels, contract: rows.__setitem__(
            "recorded_at", "2026-07-01T21:00:00Z"
        ),
        lambda rows, labels, contract: rows.__setitem__("hist_move_med_4q", np.nan),
    ],
)
def test_expansion_requires_prospective_snapshot_and_matched_baseline(tmp_path, mutate):
    assert evaluate(tmp_path, mutate=mutate)["status"] == "insufficient_data"


def test_expansion_keeps_200_independent_events_per_cohort(tmp_path):
    assert evaluate(tmp_path, n=199)["status"] == "insufficient_data"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows, labels, contract: rows.__setitem__("prediction", 0.09),
        lambda rows, labels, contract: rows.__setitem__("p90", 0.5),
    ],
)
def test_expansion_rejects_regression_and_bad_calibration(tmp_path, mutate):
    assert evaluate(tmp_path, mutate=mutate)["status"] == "failed"
