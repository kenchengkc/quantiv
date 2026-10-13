from __future__ import annotations

from tools.build_control_plane_snapshot import build_snapshot
import pytest


def test_display_forecast_coverage_is_observable_but_does_not_change_eligibility():
    reconciliation = {
        "quality": {"status": "passed", "decision_safe": True},
        "event_coverage": {
            "coverage_pct": 0.73,
            "expected_events": 41,
            "covered_events": 30,
            "missing_events": 11,
            "status": "passed",
        },
        "exceptions": [
            {
                "code": "upcoming_events_without_option_chain",
                "severity": "warning",
                "summary": "11 events lack decision-eligible options",
                "count": 11,
            }
        ],
    }
    monitoring = {"status": "passed", "feature_drift": {"status": "passed"}}
    display = {
        "coverage_pct": 1.0,
        "published_upcoming_events": 41,
        "with_display_forecast": 41,
        "method_mix": {
            "ml": 30,
            "options_math": 4,
            "options_indicative": 3,
            "historical": 3,
            "historical_prior": 1,
        },
    }

    snapshot = build_snapshot(
        reconciliation,
        monitoring,
        {"champion_bundle_id": "champion"},
        {},
        generated_at="2026-09-16T15:00:00+00:00",
        display_forecast_status=display,
    )

    assert snapshot["publication_eligible"] is True
    assert snapshot["data"]["event_coverage_pct"] == 0.73
    assert snapshot["data"]["display_forecast_coverage_pct"] == 1.0
    assert snapshot["data"]["display_forecast_events"] == 41
    assert snapshot["data"]["display_forecast_method_mix"]["options_indicative"] == 3


@pytest.mark.parametrize(('monitor_status', 'expected_status'), [('failed', 'failed'), ('partial', 'advisory')])
def test_partial_serving_is_advisory_while_aggregate_drift_stays_critical(monitor_status, expected_status):
    snapshot = build_snapshot(
        {'quality': {'status': 'passed', 'decision_safe': True}},
        {'status': monitor_status, 'feature_drift': {'status': 'critical'},
         'publication_eligibility': {'schema': 'quantiv.event-forecast-eligibility.v1',
            'coverage': {'candidate_events': 3, 'eligible_events': 1, 'held_events': 2,
                         'candidate_rows': 3, 'eligible_rows': 1, 'held_rows': 2}}},
        {'champion_bundle_id': 'champion'}, {}, generated_at='2026-10-13T10:00:00Z',
        display_forecast_status={'ml_publication_status': 'partial',
            'ml_publication_coverage': {'candidate_events': 3, 'eligible_events': 1, 'held_events': 2,
                                      'candidate_rows': 3, 'eligible_rows': 1, 'held_rows': 2}})
    assert snapshot['publication_eligible'] is True
    assert snapshot['status'] == 'advisory'
    assert snapshot['model']['status'] == expected_status
    assert snapshot['model']['drift_status'] == 'critical'
    assert snapshot['model']['publication_coverage']['held_events'] == 2
    assert snapshot['model']['publication_policy'] == 'quantiv.event-forecast-eligibility.v1'
    assert any(item['code'] == 'ml_event_coverage_partial' and item['severity'] == 'warning'
               for item in snapshot['exceptions'])
    coverage_warning = next(item for item in snapshot['exceptions'] if item['code'] == 'ml_event_coverage_partial')
    assert 'scored earnings events' in coverage_warning['summary']
    assert 'Other upcoming calendar events remain held for ML' in coverage_warning['summary']


def test_all_events_can_serve_while_one_horizon_is_held():
    coverage = {'candidate_events': 2, 'eligible_events': 2, 'held_events': 0,
                'candidate_rows': 4, 'eligible_rows': 3, 'held_rows': 1}
    snapshot = build_snapshot(
        {'quality': {'status': 'passed', 'decision_safe': True}},
        {'status': 'partial', 'feature_drift': {'status': 'critical'},
         'publication_eligibility': {'schema': 'quantiv.event-forecast-eligibility.v1', 'coverage': coverage}},
        {'champion_bundle_id': 'champion'}, {}, generated_at='2026-10-13T10:00:00Z',
        display_forecast_status={'ml_publication_status': 'partial', 'ml_publication_coverage': coverage})
    assert snapshot['publication_eligible'] is True
    assert any(item['code'] == 'ml_forecast_coverage_partial' and item['severity'] == 'warning'
               for item in snapshot['exceptions'])


def test_eligible_cohorts_keep_aggregate_drift_as_an_advisory():
    coverage = {'candidate_events': 2, 'eligible_events': 2, 'held_events': 0,
                'candidate_rows': 2, 'eligible_rows': 2, 'held_rows': 0}
    snapshot = build_snapshot(
        {'quality': {'status': 'passed', 'decision_safe': True}},
        {'status': 'passed', 'feature_drift': {'status': 'critical'},
         'publication_eligibility': {'schema': 'quantiv.event-forecast-eligibility.v1', 'coverage': coverage}},
        {'champion_bundle_id': 'champion'}, {}, generated_at='2026-10-13T10:00:00Z',
        display_forecast_status={'ml_publication_status': 'eligible', 'ml_publication_coverage': coverage})
    assert snapshot['model']['status'] == 'advisory'
    assert snapshot['model']['drift_status'] == 'critical'
    assert snapshot['publication_eligible'] is True
