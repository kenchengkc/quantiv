import pandas as pd
import pytest

from ml.training_split import chronological_train_val_split


def test_split_sorts_by_date_keeps_targets_aligned_and_purges_calendar_days():
    frame = pd.DataFrame(
        {
            "__earnings_date": [
                "2026-01-10",
                "2026-01-01",
                "2026-01-08",
                "2026-01-11",
                "2026-01-09",
                "2026-01-02",
            ],
            "__symbol": ["F", "A", "D", "G", "E", "B"],
            "feature": [10, 1, 8, 11, 9, 2],
            "target": [100, 10, 80, 110, 90, 20],
        }
    )

    train, validation, metadata = chronological_train_val_split(
        frame, train_frac=0.5, purge_days=2
    )

    assert train["feature"].tolist() == [1, 2]
    assert train["target"].tolist() == [10, 20]
    assert validation["feature"].tolist() == [9, 10, 11]
    assert validation["target"].tolist() == [90, 100, 110]
    assert metadata["rows_purged"] == 1
    assert metadata["train_end"] == "2026-01-02"
    assert metadata["validation_start"] == "2026-01-09"


def test_split_keeps_boundary_date_together_in_validation():
    frame = pd.DataFrame(
        {
            "__earnings_date": [
                "2026-01-01",
                "2026-01-02",
                "2026-01-03",
                "2026-01-03",
                "2026-01-04",
            ],
            "target": range(5),
        }
    )

    train, validation, _ = chronological_train_val_split(
        frame, train_frac=0.6, purge_days=0
    )

    assert train["__earnings_date"].dt.date.astype(str).tolist() == [
        "2026-01-01",
        "2026-01-02",
    ]
    assert validation["__earnings_date"].dt.date.astype(str).tolist() == [
        "2026-01-03",
        "2026-01-03",
        "2026-01-04",
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"train_frac": 1.0}, "train_frac"),
        ({"purge_days": -1}, "purge_days"),
        ({"date_col": "missing"}, "must include"),
    ],
)
def test_split_rejects_invalid_configuration(kwargs, message):
    frame = pd.DataFrame(
        {"__earnings_date": ["2026-01-01", "2026-01-02"], "target": [1, 2]}
    )
    with pytest.raises(ValueError, match=message):
        chronological_train_val_split(frame, **kwargs)


@pytest.mark.parametrize("horizon", [7, 14, 21])
def test_split_requires_labels_available_before_first_validation_snapshot(horizon):
    events = pd.date_range("2025-09-01", periods=60)
    frame = pd.DataFrame({
        "__earnings_date": events,
        "__snapshot_date": events - pd.Timedelta(days=horizon),
        "__label_available_at": events + pd.Timedelta(days=1),
        "target": range(60),
    })
    train, validation, metadata = chronological_train_val_split(
        frame, train_frac=.75, horizon_days=horizon,
    )
    first_snapshot = pd.to_datetime(validation["__snapshot_date"]).min()
    assert (pd.to_datetime(train["__label_available_at"]) < first_snapshot).all()
    assert metadata["validation_snapshot_start"] == first_snapshot.date().isoformat()
    assert metadata["availability_basis"] == "observed_label_availability"


def test_legacy_split_accounts_for_forecast_horizon_and_label_lag():
    frame = pd.DataFrame({
        "__earnings_date": pd.date_range("2025-09-01", periods=60),
        "target": range(60),
    })
    train, _, metadata = chronological_train_val_split(
        frame, horizon_days=21, label_lag_days=5,
    )
    assert train["__earnings_date"].max() == pd.Timestamp("2025-09-19")
    assert metadata["availability_basis"] == "conservative_legacy_horizon_and_label_lag"


def test_causal_split_rejects_partial_availability_evidence():
    frame = pd.DataFrame({
        "__earnings_date": pd.date_range("2025-09-01", periods=10),
        "__snapshot_date": pd.date_range("2025-08-31", periods=10),
        "target": range(10),
    })
    with pytest.raises(ValueError, match="both"):
        chronological_train_val_split(frame, horizon_days=1)
