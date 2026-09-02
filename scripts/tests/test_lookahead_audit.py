import pandas as pd

from research.lookahead_audit import audit_lookahead


def test_audit_passes_when_all_features_are_available_before_decision() -> None:
    frame = pd.DataFrame(
        {
            "event_id": ["A", "A", "B"],
            "feature": ["spot", "iv", "spot"],
            "available_at": [
                "2026-09-01T20:00:00Z",
                "2026-09-01T19:59:00Z",
                "2026-09-02T20:00:00Z",
            ],
            "decision_at": [
                "2026-09-01T20:00:00Z",
                "2026-09-01T20:00:00Z",
                "2026-09-02T20:00:00Z",
            ],
        }
    )

    report = audit_lookahead(
        frame,
        decision_column="decision_at",
        available_column="available_at",
        feature_column="feature",
        id_column="event_id",
    )

    assert report["passed"] is True
    assert report["lookahead_violations"] == 0
    assert report["by_feature"]["spot"]["violations"] == 0


def test_audit_reports_future_information_and_missing_timestamps() -> None:
    frame = pd.DataFrame(
        {
            "event_id": ["A", "B", "C"],
            "feature": ["iv", "earnings", "spot"],
            "available_at": [
                "2026-09-01T20:01:30Z",
                None,
                "2026-09-03T19:00:00Z",
            ],
            "decision_at": [
                "2026-09-01T20:00:00Z",
                "2026-09-02T20:00:00Z",
                "2026-09-03T20:00:00Z",
            ],
        }
    )

    report = audit_lookahead(
        frame,
        decision_column="decision_at",
        available_column="available_at",
        feature_column="feature",
        id_column="event_id",
    )

    assert report["passed"] is False
    assert report["lookahead_violations"] == 1
    assert report["rows_with_missing_timestamps"] == 1
    assert report["violations"][0]["lead_seconds"] == 90.0
    assert report["violations"][0]["feature"] == "iv"
    assert report["violations"][0]["id"] == "A"
