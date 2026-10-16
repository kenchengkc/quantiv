
import pandas as pd
import pytest

from ml.model_control import prospective_snapshot_mask


@pytest.mark.parametrize("now,allowed,rejected", [
    ("2026-10-16T04:00:00+00:00", "2026-10-14", "2026-10-13"),
    ("2026-09-08T12:00:00+00:00", "2026-09-03", "2026-09-02"),
    ("2026-11-27T18:00:00+00:00", "2026-11-25", "2026-11-24"),
])
def test_grace_counts_completed_sessions_including_holidays_and_early_closes(now, allowed, rejected):
    frame = pd.DataFrame({"recorded_at": [now] * 2, "scored_at": [now] * 2,
                          "snapshot_date": [allowed, rejected]})
    assert prospective_snapshot_mask(frame).tolist() == [True, False]


def test_grace_never_allows_future_weekend_or_rescored_old_snapshots():
    frame = pd.DataFrame({"recorded_at": ["2026-10-19T12:00:00Z"] * 3,
                          "scored_at": ["2026-10-19T12:00:00Z"] * 3,
                          "snapshot_date": ["2026-10-19", "2026-10-18", "2026-10-14"]})
    assert not prospective_snapshot_mask(frame).any()


def test_grace_must_hold_at_both_scoring_and_recording_times():
    frame = pd.DataFrame({"recorded_at": ["2026-10-16T21:00:00Z"],
                          "scored_at": ["2026-10-15T21:00:00Z"],
                          "snapshot_date": ["2026-10-14"]})
    assert not prospective_snapshot_mask(frame).any()
