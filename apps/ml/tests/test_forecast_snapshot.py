from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ml.forecast_snapshot import forecast_snapshot_id


def _row():
    return {"act_symbol": "A", "earnings_date": "2026-10-21", "snapshot_date": "2026-10-14",
            "model_horizon": 7, "model_bundle_id": "a" * 64, "scored_at": "2026-10-15T12:00:00Z",
            "spot_price": 100., "feature_vector": '{"x":1.0,"missing":null,"zero":-0.0}'}


def test_snapshot_identity_survives_jsonb_numeric_and_timestamp_normalization():
    original = _row()
    stored = {**original, "act_symbol": "a", "spot_price": Decimal("100.00"),
              "scored_at": datetime(2026, 10, 15, 12, tzinfo=timezone.utc),
              "feature_vector": {"zero": 0, "missing": None, "x": Decimal("1.000")}}
    assert forecast_snapshot_id(original) == forecast_snapshot_id(stored)


@pytest.mark.parametrize("field,value", [("feature_vector", '{"x":2.0}'), ("model_horizon", 8),
    ("scored_at", "2026-10-15T12:00:01Z"), ("spot_price", 101.), ("model_bundle_id", "b" * 64)])
def test_snapshot_identity_rejects_same_session_replacement_of_any_scoring_input(field, value):
    original = _row()
    assert forecast_snapshot_id(original) != forecast_snapshot_id({**original, field: value})
