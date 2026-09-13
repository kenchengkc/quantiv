from __future__ import annotations

import json
import math

import pytest

from frontend_data.research_arithmetic import normalize_public_research_arithmetic


def _payload() -> dict:
    return {
        "schema": "quantiv.historical-event-universe.v1",
        "generated_at": "2026-09-13T00:00:00Z",
        "universe_id": "sha256:" + "0" * 64,
        "source": {"kind": "analytical_duckdb", "completeness": "source_level"},
        "event_count": 1,
        "events": [
            {
                "ticker": "TEST",
                "date": "2026-01-02",
                "implied": 0.188063,
                # Deliberately inconsistent independently rounded values. These
                # reproduce the class of drift seen in the daily refresh.
                "actual": 0.123456,
                "realized_abs": 0.123456,
                "edge": -0.064606,
                "ratio": 0.656437,
                "outside_implied": False,
                "realized_window": {
                    "pre_price": 100.0,
                    "post_price_adjusted": 112.34564,
                },
            }
        ],
    }


def test_normalized_history_arithmetic_survives_json_round_trip() -> None:
    payload = normalize_public_research_arithmetic(_payload())
    event = payload["events"][0]

    # Exercise the same JSON round trip as the published artifact before doing
    # the validator's strict arithmetic recomputation.
    event = json.loads(json.dumps(event, allow_nan=False))
    actual = event["actual"]
    realized_abs = event["realized_abs"]
    implied = event["implied"]

    assert math.isclose(
        actual,
        event["realized_window"]["post_price_adjusted"]
        / event["realized_window"]["pre_price"]
        - 1.0,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )
    assert math.isclose(realized_abs, abs(actual), rel_tol=1e-12, abs_tol=1e-12)
    assert math.isclose(
        event["edge"], realized_abs - implied, rel_tol=1e-12, abs_tol=1e-12
    )
    assert math.isclose(
        event["ratio"], realized_abs / implied, rel_tol=1e-12, abs_tol=1e-12
    )
    assert event["outside_implied"] is (realized_abs > implied)
    assert payload["universe_id"] != "sha256:" + "0" * 64


def test_normalized_history_arithmetic_fails_closed_on_invalid_public_inputs() -> None:
    payload = _payload()
    payload["events"][0]["implied"] = 0.0

    with pytest.raises(ValueError, match="nonpositive public arithmetic inputs"):
        normalize_public_research_arithmetic(payload)
