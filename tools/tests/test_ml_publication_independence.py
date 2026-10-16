"""Regression coverage for publishing ML independently of strict options math."""

from __future__ import annotations

from datetime import date

import pytest

import frontend_data.payloads as payloads


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=None):
        return _Rows(self._rows)


def _forecast(snapshot_date: date) -> dict:
    return {
        "em_ml_pct": 0.085007,
        "em_ml_abs": 4.347697,
        "correction_factor": 0.540955,
        "model_horizon": 14,
        "snapshot_date": snapshot_date,
        "p10": 0.011632,
        "p25": 0.034987,
        "p50": 0.080091,
        "p75": 0.136412,
        "p90": 0.183762,
    }


def _published_row():
    return (
        "CBRL",
        date(2026, 9, 23),
        "before_market_open",
        "Q3",
        None,
        None,
        None,
        None,
        None,
    )


def test_matching_ml_survives_when_strict_options_math_is_unavailable(monkeypatch):
    monkeypatch.setattr(payloads, "compute_em_math", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        payloads,
        "screener_extras",
        lambda *_args, **_kwargs: {
            "iv_rank": None,
            "hist_move_avg_4q": None,
            "iv_crush_pct": None,
        },
    )

    event_key = ("CBRL", "2026-09-23")
    events = payloads.build_week_events(
        _Conn([_published_row()]),
        as_of_date=date(2026, 9, 14),
        week_start=date(2026, 9, 21),
        week_end=date(2026, 9, 25),
        ml_lookup={event_key: _forecast(date(2026, 9, 9))},
        provider_lookup={},
        require_ml=True,
        canonical={event_key},
        published={event_key: "bmo"},
    )

    assert len(events) == 1
    event = events[0]
    assert event["em_method"] == "ml_lightgbm"
    assert event["em_ml_pct"] == 0.085007
    assert event["model_horizon"] == 14
    assert event["ml_snapshot_date"] == "2026-09-09"
    assert event["em_straddle_pct"] is None
    assert event["em_iv_pct"] is None


def test_ml_for_different_earnings_date_is_not_reused(monkeypatch):
    monkeypatch.setattr(payloads, "compute_em_math", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        payloads,
        "screener_extras",
        lambda *_args, **_kwargs: {
            "iv_rank": None,
            "hist_move_avg_4q": None,
            "iv_crush_pct": None,
        },
    )

    event_key = ("CBRL", "2026-09-23")
    events = payloads.build_week_events(
        _Conn([_published_row()]),
        as_of_date=date(2026, 9, 14),
        week_start=date(2026, 9, 21),
        week_end=date(2026, 9, 25),
        ml_lookup={("CBRL", "2026-09-22"): _forecast(date(2026, 9, 8))},
        provider_lookup={},
        require_ml=True,
        canonical={event_key},
        published={event_key: "bmo"},
    )

    assert len(events) == 1
    event = events[0]
    assert event["em_method"] is None
    assert "em_ml_pct" not in event
    assert "model_horizon" not in event
    assert "ml_snapshot_date" not in event


@pytest.mark.parametrize("matching_ml", [True, False])
def test_new_optionless_event_requires_exact_ml_but_no_current_options(monkeypatch, matching_ml):
    event_day = date(2099, 9, 23)
    event_key = ("NEW", event_day.isoformat())
    row = ("NEW", event_day, "amc", "Q3", None, None, None, None, None)
    monkeypatch.setattr(payloads, "compute_em_math", lambda *_a, **_kw: pytest.fail("held upcoming options must not be queried"))
    monkeypatch.setattr(payloads, "screener_extras", lambda *_a, **_kw: {})
    forecast_key = event_key if matching_ml else ("NEW", "2099-09-22")
    events = payloads.build_week_events(
        _Conn([row]), as_of_date=date(2099, 9, 9),
        week_start=event_day, week_end=event_day,
        ml_lookup={forecast_key: _forecast(date(2099, 9, 9))},
        require_ml=False, canonical={event_key}, published={}, options_available=False,
    )
    assert len(events) == int(matching_ml)
    if matching_ml:
        assert events[0]["em_ml_pct"] == 0.085007
        assert events[0]["model_horizon"] == 14
        assert events[0]["ml_snapshot_date"] == "2099-09-09"
        assert events[0]["em_straddle_pct"] is None


def test_retained_calendar_event_does_not_query_held_current_options(monkeypatch):
    event_day = date(2099, 9, 23)
    key = ("RETAINED", event_day.isoformat())
    monkeypatch.setattr(payloads, "compute_em_math", lambda *_a, **_kw: pytest.fail("held upcoming options must not be queried"))
    monkeypatch.setattr(payloads, "screener_extras", lambda *_a, **_kw: {})
    events = payloads.build_week_events(
        _Conn([]), as_of_date=date(2099, 9, 9), week_start=event_day, week_end=event_day,
        ml_lookup={key: _forecast(date(2099, 9, 9))}, published={key: "amc"}, options_available=False,
    )
    assert len(events) == 1 and events[0]["em_ml_pct"] == 0.085007
