from __future__ import annotations

import copy
from datetime import date
import json

import duckdb
import pytest

from frontend_data.display_forecast import DisplayForecastError
from frontend_data.display_payloads import (
    build_display_forecast_status, enrich_upcoming_event,
    suppress_held_options, validate_upcoming_display_forecasts,
)
from frontend_data.forecast_artifacts import withhold_upcoming_ml

TODAY = date(2026, 10, 15)


def _event(**fields):
    return {
        "ticker": "TEST", "earnings_date": "2026-10-21", "timing": "bmo",
        "hist_move_med_4q": 0.08, "hist_move_avg_4q": 0.09,
        "em_iv_pct": 0.12, "em_straddle_pct": 0.10,
        "display_forecast_pct": 0.08, "display_forecast_method": "historical",
        **fields,
    }


def test_upcoming_without_ml_uses_iv_and_retains_historical_context():
    event = _event()
    with duckdb.connect() as conn:
        enrich_upcoming_event(conn, event, as_of_date=TODAY, today=TODAY)
    assert event["display_forecast_pct"] == 0.12
    assert event["display_forecast_method"] == "options_math"
    assert event["ml_status"] == "unavailable_inputs"
    assert event["hist_move_med_4q"] == 0.08
    assert event["hist_move_avg_4q"] == 0.09
    validate_upcoming_display_forecasts(
        [("TEST", date(2026, 10, 21), "bmo")],
        {date(2026, 10, 19): {"events": [event]}}, today=TODAY,
    )


def test_upcoming_held_ml_cannot_reappear_from_raw_or_canonical_values():
    event = _event(em_ml_pct=0.07, display_forecast_method="ml",
                   display_forecast_pct=0.07, ml_publication_status="held")
    with duckdb.connect() as conn:
        enrich_upcoming_event(conn, event, as_of_date=TODAY, today=TODAY)
    assert event["display_forecast_method"] == "options_math"
    assert event["display_forecast_pct"] == 0.12


@pytest.mark.parametrize("method", ["historical", "historical_prior", "options_math", "options_indicative"])
def test_upcoming_validation_rejects_fallbacks_without_provenance(method):
    event = _event(display_forecast_method=method)
    with pytest.raises(DisplayForecastError, match="missing display_forecast_as_of"):
        validate_upcoming_display_forecasts(
            [("TEST", date(2026, 10, 21), "bmo")],
            {date(2026, 10, 19): {"events": [event]}}, today=TODAY,
        )


def test_ml_hold_removes_fallback_on_all_surfaces_and_preserves_reported_history(tmp_path):
    upcoming = _event(em_ml_pct=0.07, forecast_id="unapproved")
    reported = _event(earnings_date="2026-10-14", eps_actual=0, forecast_id="reported")
    frozen = copy.deepcopy(reported)
    payload = {"events": [upcoming, reported]}
    for relative in ("weekly.json", "screener.json", "weeks/2026-10-19.json"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    symbol_path = tmp_path / "symbols/TEST.json"
    symbol_path.parent.mkdir()
    symbol_path.write_text(json.dumps({
        "symbol": "TEST", "expected_move": upcoming,
        "earnings_history": [reported],
    }))
    withhold_upcoming_ml(tmp_path, today=TODAY, eligible_forecast_ids=set())
    for relative in ("weekly.json", "screener.json", "weeks/2026-10-19.json"):
        events = json.loads((tmp_path / relative).read_text())["events"]
        assert events[0]["display_forecast_method"] == "ml_unavailable"
        assert events[0]["display_forecast_pct"] is None
        assert events[0]["hist_move_med_4q"] == 0.08
        assert events[1] == frozen
    symbol = json.loads(symbol_path.read_text())
    assert symbol["expected_move"]["display_forecast_method"] == "ml_unavailable"
    assert symbol["earnings_history"] == [frozen]


def test_held_options_projection_does_not_require_history_to_publish_unavailable_ml(tmp_path):
    path = tmp_path / "weekly.json"
    path.write_text(json.dumps({"events": [_event()]}))
    with duckdb.connect() as conn:
        suppress_held_options(tmp_path, conn=conn, as_of_date=TODAY, today=TODAY)
    event = json.loads(path.read_text())["events"][0]
    assert event["display_forecast_method"] == "ml_unavailable"
    assert event["display_forecast_pct"] is None
    assert event["em_iv_pct"] is None


def test_display_coverage_counts_only_ml_and_keeps_missing_events_in_denominator():
    unavailable = _event(display_forecast_method="ml_unavailable", display_forecast_pct=None)
    available = _event(ticker="ML", display_forecast_method="ml", display_forecast_pct=0.07)
    status = build_display_forecast_status(
        [("TEST", date(2026, 10, 21), "bmo"), ("ML", date(2026, 10, 21), "bmo"),
         ("MISSING", date(2026, 10, 21), "bmo")],
        {date(2026, 10, 19): {"events": [unavailable, available]}},
        today=TODAY, generated_at="2026-10-15T12:00:00Z",
    )
    assert status["published_upcoming_events"] == 3
    assert status["with_display_forecast"] == 1
    assert status["coverage_pct"] == pytest.approx(1 / 3)
    assert status["method_mix"]["ml_unavailable"] == 1


def test_nonmarket_events_remain_explicitly_excluded_from_full_window_denominator():
    status = build_display_forecast_status(
        [("WEEKEND", date(2026, 10, 17), "bmo")],
        {date(2026, 10, 12): {"window": {"start": "2026-10-12", "end": "2026-10-16"}, "events": []}},
        today=TODAY, generated_at="2026-10-15T12:00:00Z",
    )
    assert status["published_upcoming_events"] == 1
    assert status["unavailable_events"] == 1
    assert status["coverage_pct"] == 0
    assert status["exclusions"] == [{"event_key": "WEEKEND|2026-10-17", "reason": "non_market_session_event"}]


def test_eligible_retained_ml_replaces_a_stale_historical_headline(tmp_path):
    path = tmp_path / "weekly.json"
    path.write_text(json.dumps({"events": [_event(em_ml_pct=0.07, forecast_id="approved")]}))
    withhold_upcoming_ml(tmp_path, today=TODAY, eligible_forecast_ids={"approved"})
    event = json.loads(path.read_text())["events"][0]
    assert event["display_forecast_method"] == "ml"
    assert event["display_forecast_pct"] == 0.07
    assert event["ml_status"] == "available"
    assert event["fallback_reason"] is None


def test_eligible_identity_without_a_finite_ml_value_remains_unavailable(tmp_path):
    path = tmp_path / "weekly.json"
    path.write_text(json.dumps({"events": [_event(forecast_id="approved")]}))
    withhold_upcoming_ml(tmp_path, today=TODAY, eligible_forecast_ids={"approved"})
    event = json.loads(path.read_text())["events"][0]
    assert event["display_forecast_method"] == "ml_unavailable"
    assert event["display_forecast_pct"] is None
    assert event["ml_publication_status"] == "held"
