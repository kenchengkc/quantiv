"""Calendar retrieval must cover the requested window without silent truncation."""
from datetime import date

import pytest

import reconcile_earnings_calendar as calendar


def test_finnhub_splits_capped_windows_and_deduplicates(monkeypatch):
    calls = []
    first = {"symbol": "AAA", "date": "2026-10-21", "hour": "bmo"}
    last = {"symbol": "ZZZ", "date": "2026-10-24", "hour": "amc"}

    def request(url, *, params, **kwargs):
        calls.append((params["from"], params["to"]))
        if len(calls) == 1:
            rows = [first] * 1500
        elif params["from"] == "2026-10-21":
            rows = [first, first]
        else:
            rows = [last]
        return 200, {"earningsCalendar": rows}, None

    monkeypatch.setattr(calendar, "_request_json", request)
    monkeypatch.setattr(calendar.time, "sleep", lambda _: None)
    rows, meta = calendar.fetch_finnhub_calendar(date(2026, 10, 21), date(2026, 10, 24), "test")
    assert calls == [("2026-10-21", "2026-10-24"), ("2026-10-21", "2026-10-22"), ("2026-10-23", "2026-10-24")]
    assert [(r["symbol"], r["timing"]) for r in rows] == [("AAA", "bmo"), ("ZZZ", "amc")]
    assert meta["coverage_complete"] is True
    assert meta["requests"] == 3


@pytest.mark.parametrize("payload", [{"error": "rate limited"}, {}, [], {"earningsCalendar": None}])
def test_finnhub_invalid_payload_is_not_success(monkeypatch, payload):
    monkeypatch.setattr(calendar, "_request_json", lambda *a, **k: (200, payload, None))
    rows, meta = calendar.fetch_finnhub_calendar(date(2026, 10, 21), date(2026, 10, 21), "test")
    assert rows == []
    assert meta["status"] == "error"
    assert meta["coverage_complete"] is False


def test_finnhub_saturated_single_day_reports_incomplete(monkeypatch):
    monkeypatch.setattr(calendar, "_request_json", lambda *a, **k: (200, {"earningsCalendar": [
        {"symbol": "AAA", "date": "2026-10-21", "hour": "bmo"}
    ] * 1500}, None))
    rows, meta = calendar.fetch_finnhub_calendar(date(2026, 10, 21), date(2026, 10, 21), "test")
    assert len(rows) == 1
    assert meta["status"] == "partial"
    assert meta["coverage_complete"] is False
    assert meta["incomplete_windows"][0]["reason"] == "single_day_row_cap"


def test_finnhub_child_failure_does_not_claim_full_coverage(monkeypatch):
    def request(url, *, params, **kwargs):
        row = {"symbol": "AAA", "date": "2026-10-21", "hour": "bmo"}
        if params["from"] != "2026-10-21":
            return 503, None, "unavailable"
        return 200, {"earningsCalendar": [row] * (1500 if params["to"] == "2026-10-22" else 1)}, None
    monkeypatch.setattr(calendar, "_request_json", request)
    monkeypatch.setattr(calendar.time, "sleep", lambda _: None)
    rows, meta = calendar.fetch_finnhub_calendar(date(2026, 10, 21), date(2026, 10, 22), "test")
    assert len(rows) == 1
    assert meta["status"] == "partial"
    assert meta["coverage_complete"] is False
    assert meta["incomplete_windows"][0]["from"] == "2026-10-22"


def test_finnhub_bounds_requests_when_every_window_is_capped(monkeypatch):
    calls = []
    def request(url, *, params, **kwargs):
        calls.append(params)
        return 200, {"earningsCalendar": [{"symbol": "AAA", "date": params["from"], "hour": "bmo"}] * 1500}, None
    monkeypatch.setattr(calendar, "_request_json", request)
    monkeypatch.setattr(calendar.time, "sleep", lambda _: None)
    _, meta = calendar.fetch_finnhub_calendar(date(2026, 10, 21), date(2026, 12, 20), "test", max_requests=3)
    assert len(calls) == 3
    assert meta["coverage_complete"] is False
    assert any(w["reason"] == "request_budget_exhausted" for w in meta["incomplete_windows"])


def test_finnhub_calendar_budget_counts_actual_http_attempts(monkeypatch):
    calls = []
    class Response:
        status_code = 429
        ok = False
        text = "rate limited"
    def get(*args, **kwargs):
        calls.append(kwargs)
        return Response()
    monkeypatch.setattr(calendar.requests, "get", get)
    monkeypatch.setattr(calendar.time, "sleep", lambda _: None)
    _, meta = calendar.fetch_finnhub_calendar(date(2026, 10, 21), date(2026, 10, 22), "test", max_requests=1)
    assert len(calls) == meta["requests"] == 1
    assert meta["coverage_complete"] is False


def test_fmp_requests_future_window_and_reporting_times(monkeypatch):
    def request(url, *, params, **kwargs):
        assert params == {"apikey": "test", "from": "2026-10-21", "to": "2026-11-13", "includeReportTimes": "true"}
        return 200, [{"symbol": "AAA", "date": "2026-10-22", "time": "amc"},
                     {"symbol": "OLD", "date": "2026-10-20", "time": "bmo"}], None
    monkeypatch.setattr(calendar, "_request_json", request)
    rows, meta = calendar.fetch_fmp_calendar(date(2026, 10, 21), date(2026, 11, 13), "test")
    assert rows == [{"provider": "fmp_calendar", "symbol": "AAA", "date": "2026-10-22", "timing": "amc"}]
    assert meta["rows"] == 1
