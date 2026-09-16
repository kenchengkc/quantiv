from __future__ import annotations

from tools.build_control_plane_snapshot import build_snapshot


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
