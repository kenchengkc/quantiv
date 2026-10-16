"""Historical native quality permits honest sparse-current drift warnings."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from apps.ml.tests.test_historical_admission import _candidate  # noqa: E402
from ml.historical_admission import evaluate_historical_admission  # noqa: E402
from ml.model_bundle import ModelBundleError, create_historically_admitted_bundle  # noqa: E402
from ml.model_control import cohort_drift_report, rolling_cohort_drift_report  # noqa: E402
from ml.pipeline_validation import FORECAST_REQUIRED_COLUMNS  # noqa: E402

NOW = datetime(2026, 10, 15, 22, tzinfo=timezone.utc)


@pytest.fixture
def bundle_factory(tmp_path, monkeypatch):
    def create(*, reference_rows=250, missing_rate=0.0, with_reference=True):
        reference = {
            name: {"missing_rate": missing_rate if name == "feature" else 0.0,
                   "cuts": [.05], "probabilities": [.5, .5]}
            for name in ("feature", "straddle_pct", "hist_move_med_4q")
        }
        metadata = {"cohort_reference": {"strict_options": {
            "rows": reference_rows, "feature_reference": reference if with_reference else {},
        }}}
        source, reservation, model_receipt, private, public = _candidate(
            tmp_path, monkeypatch, metadata_overrides=metadata,
        )
        _, receipt = evaluate_historical_admission(
            source, reservation, model_receipt, tmp_path / "dev", tmp_path / "evidence",
            private_key=private, public_key=public, reservation_registry=tmp_path / "registry",
        )
        bundle, manifest = create_historically_admitted_bundle(
            source, tmp_path / "derived", admission_receipt=receipt,
            source_revision="sparse-monitor", private_key=private,
        )
        return source, bundle, manifest
    return create


def _current(bundle, count=5):
    rows = []
    for index in range(count):
        row = dict.fromkeys(FORECAST_REQUIRED_COLUMNS)
        vector = {"feature": .04 if index % 2 else .06, "straddle_pct": .08,
                  "hist_move_med_4q": .06, "timing_bmo": 1., "timing_amc": 0.,
                  "atm_iv": .5, "dte": 14., "em_iv_pct": .5 * (14. / 365.) ** .5}
        row.update({
            "act_symbol": f"LIVE{index}", "earnings_date": "2026-10-22",
            "snapshot_date": "2026-10-15", "model_horizon": 7,
            "model_bundle_id": bundle.name, "feature_protocol": "quantiv.earnings-causal.v2",
            "target_protocol": "quantiv.session-reaction.v2", "timing": "bmo",
            "spot_price": 100., "em_math_pct": .08, "em_ml_pct": .05, "em_ml_abs": 5.,
            "atm_iv": .5, "atm_strike": 100., "call_strike": 100., "put_strike": 100.,
            "call_bid": 3.9, "call_ask": 4.1, "call_mid": 4., "call_relative_spread": .05,
            "put_bid": 3.9, "put_ask": 4.1, "put_mid": 4., "put_relative_spread": .05,
            "straddle_bid": 7.8, "straddle_ask": 8.2, "straddle_mid": 8.,
            "straddle_relative_spread": .05, "quote_quality_status": "passed",
            "quote_timestamp_precision": "date", "market_data_mode": "end_of_day",
            "liquidity_tier": "liquid", "liquidity_tier_method": "quote_spread_proxy",
            "correction_factor": .625, "scored_at": NOW.isoformat(),
            "feature_vector": json.dumps(vector),
            **{f"p{q:02d}": q / 1000 for q in (10, 25, 50, 75, 90)},
        })
        rows.append(row)
    return pd.DataFrame(rows)


def _assess(rows, bundle):
    return rolling_cohort_drift_report(rows, bundle, bundle_id=bundle.name,
                                      horizons=[7], as_of=NOW)


@pytest.mark.parametrize("count", [1, 5, 99])
def test_signed_historical_quality_allows_sparse_current_warning(bundle_factory, count):
    _, bundle, manifest = bundle_factory()
    report = _assess(_current(bundle, count), bundle)
    assert report["status"] == "warning"
    cohort = report["horizons"]["7"]["cohorts"]["strict_options"]
    assert cohort["status"] == "warning"
    assert cohort["covariate_status"] == "insufficient_data"
    assert cohort["horizons"]["7"]["rows"] == count
    assert cohort["horizons"]["7"]["features"]["feature"]["status"] == "low_sample"
    evidence = cohort["historical_admission"]
    assert evidence["admission_id"] == manifest["historical_admission"]["receipt"]["admission_id"]
    assert evidence["admitted_cohorts"] == {"7": ["strict_options"]}
    assert evidence["sufficient_for"] == "native_model_quality_only"
    assert evidence["reference_rows"] == 250
    assert evidence["sampling_reason"] == "current_cohort_below_covariate_sample_floor"
    assert report["rolling_evidence"]["current_rows"] == count
    assert report["rolling_evidence"]["historical_rows"] == 0


@pytest.mark.parametrize("options", [{"reference_rows": 199}, {"with_reference": False}])
def test_sparse_historical_warning_requires_adequate_signed_reference(bundle_factory, options):
    _, bundle, _ = bundle_factory(**options)
    assert _assess(_current(bundle), bundle)["status"] == "insufficient_data"


def test_original_source_bundle_still_holds_ninety_nine_current_rows(bundle_factory):
    source, _, _ = bundle_factory()
    assert _assess(_current(source, 99), source)["status"] == "insufficient_data"


@pytest.mark.parametrize("missing_rate,want", [(0.0, "critical"), (.75, "warning")])
def test_historical_sparse_guard_distinguishes_observed_and_training_missing_inputs(
    bundle_factory, missing_rate, want,
):
    _, bundle, _ = bundle_factory(missing_rate=missing_rate)
    rows = _current(bundle)
    rows["feature_vector"] = rows["feature_vector"].map(
        lambda value: json.dumps({**json.loads(value), "feature": None})
    )
    report = _assess(rows, bundle)
    assert report["status"] == want
    cohort = report["current_snapshot"]["horizons"]["7"]["cohorts"]["strict_options"]
    assert cohort["corruption_status"] == ("critical" if want == "critical" else "passed")
    assert cohort["sparse_corruption_guard"]["method"] == "fully_missing_observed_reference"
    assert cohort["sparse_corruption_guard"]["features"] == (["feature"] if want == "critical" else [])


@pytest.mark.parametrize("mutation", ["stale", "protocol", "corrupt", "unadmitted"])
def test_sparse_historical_admission_preserves_current_contract_holds(bundle_factory, mutation):
    _, bundle, _ = bundle_factory()
    rows = _current(bundle)
    if mutation == "stale":
        rows["snapshot_date"] = "2026-10-13"
    elif mutation == "protocol":
        rows["feature_protocol"] = "quantiv.earnings-legacy.v1"
    elif mutation == "corrupt":
        rows["feature_vector"] = "{}"
    else:
        rows["em_math_pct"] = float("nan")
        rows["feature_vector"] = rows["feature_vector"].map(
            lambda value: json.dumps({**json.loads(value), "straddle_pct": None})
        )
    assert _assess(rows, bundle)["status"] in {"critical", "unsupported_cohort"}


def test_sparse_current_corruption_dominates_healthy_observed_history(bundle_factory):
    _, bundle, _ = bundle_factory()
    current = _current(bundle)
    current["feature_vector"] = current["feature_vector"].map(
        lambda value: json.dumps({**json.loads(value), "feature": None})
    )
    history = _current(bundle, 100).assign(
        bundle_id=bundle.name, snapshot_date="2026-10-14", __cohort="strict_options",
        scored_at="2026-10-14T22:00:00Z", recorded_at="2026-10-14T22:00:00Z",
    )
    report = rolling_cohort_drift_report(
        current, bundle, bundle_id=bundle.name, horizons=[7], history=history, as_of=NOW,
    )
    assert report["status"] == report["corruption_status"] == "critical"
    assert report["current_snapshot"]["corruption_status"] == "critical"
    assert report["rolling_evidence"]["historical_rows"] == 100


def test_sparse_warning_cannot_use_an_altered_historical_signature(bundle_factory):
    _, bundle, _ = bundle_factory()
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["historical_admission"]["receipt"]["admission_id"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ModelBundleError):
        _assess(_current(bundle), bundle)


@pytest.mark.parametrize("covered,want", [(True, 5), (False, 0)])
def test_historical_sparse_publication_still_requires_current_source_proof(
    bundle_factory, monkeypatch, covered, want,
):
    import event_forecast_eligibility as eligibility

    _, bundle, _ = bundle_factory()
    rows = eligibility.verify_candidate_identity(_current(bundle), bundle, bundle.name, [7])
    coverage = {symbol: ("2020-01-01", "2026-10-15") for symbol in rows["act_symbol"]}
    monkeypatch.setattr(eligibility, "_current_source_coverage", lambda _bundle: coverage if covered else {})
    selected, _, report = eligibility.select_publication_rows(
        rows, bundle_dir=bundle, now=NOW,
        assess_cohort=lambda current, _horizon: _assess(current, bundle),
    )
    assert len(selected) == want
    assert report["coverage"]["eligible_rows"] == want
    if covered:
        assert report["cohort_checks"]["7|strict_options"]["status"] == "warning"
    else:
        assert report["held_events"][0]["reasons"] == ["corporate_action_source_unavailable"]


def test_empty_historical_cohort_cannot_claim_a_sparse_warning(bundle_factory):
    _, bundle, _ = bundle_factory()
    assert cohort_drift_report(_current(bundle).iloc[:0], bundle, horizons=[7])["status"] == "insufficient_data"
