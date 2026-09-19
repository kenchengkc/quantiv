"""Tests for extracting earnings dates/sessions from announcement text."""

from __future__ import annotations

from datetime import date

from benchmark_earnings_announcements import extract_earnings_announcement


def test_extracts_named_date_and_after_close_session() -> None:
    text = (
        "NIKE, Inc. will report first quarter fiscal 2027 results on October 1, 2026. "
        "The results will be released following the close of regular stock market trading."
    )
    assert extract_earnings_announcement(text, published_on=date(2026, 9, 10)) == {
        "date": "2026-10-01",
        "session": "amc",
    }


def test_extracts_before_market_time() -> None:
    text = (
        "FactSet schedules fourth quarter 2026 earnings call for September 30, 2026 "
        "at 9:00 a.m. ET. Financial results will be issued before the call."
    )
    assert extract_earnings_announcement(text, published_on=date(2026, 9, 1)) == {
        "date": "2026-09-30",
        "session": "bmo",
    }


def test_ignores_historical_result_date_when_no_future_announcement_language() -> None:
    text = "The company reported results for the quarter ended June 30, 2026."
    assert extract_earnings_announcement(text, published_on=date(2026, 9, 15)) is None


def test_resolves_month_day_without_year_to_future_date() -> None:
    text = "We will announce third-quarter results on September 29 after market close."
    assert extract_earnings_announcement(text, published_on=date(2026, 9, 10)) == {
        "date": "2026-09-29",
        "session": "amc",
    }
