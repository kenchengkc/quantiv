"""Retain explicit announcement sessions and spend escalation calls on gaps."""

from datetime import date

import pytest

import reconcile_earnings_calendar as calendar


def announcement(day, timing="unknown", *, published="2026-10-19", official=False):
    return {
        "provider": "twelvedata_press_release" if official else "finnhub_company_news",
        "date": day,
        "timing": timing,
        "published_on": published,
        "direct": True,
        "official": official,
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_same_day_commentary_cannot_erase_explicit_issuer_release_session(reverse):
    rows = [
        {**announcement("2026-11-09"), "title":
         "Doug Bergeron Comments on Ethan Allen’s Decision to Report First Quarter Results After 2026 Annual Meeting"},
        {**announcement("2026-11-09", "amc"), "title":
         "Ethan Allen Announces Release Date for its Fiscal 2027 First Quarter Results"},
    ]
    chosen, reason = calendar._announcement_decision(
        rows[::-1] if reverse else rows, as_of=date(2026, 10, 21),
    )
    assert (chosen["date"], chosen["timing"], reason) == (
        "2026-11-09", "amc", "direct_announcement",
    )
    assert chosen["published_on"] == "2026-10-19"
    assert chosen["title"].startswith("Ethan Allen Announces")


@pytest.mark.parametrize("official", [False, True])
def test_contemporaneous_conflicting_release_sessions_stay_unknown(official):
    chosen, _ = calendar._announcement_decision([
        announcement("2026-11-09", "amc", official=official),
        announcement("2026-11-09", "bmo", official=official),
    ], as_of=date(2026, 10, 21))
    assert chosen["timing"] == "unknown"


def test_explicit_session_conflict_cannot_be_refilled_from_confirmed_baseline():
    decision = calendar.choose_canonical_event(
        current={"date": "2026-11-09", "timing": "unknown"},
        baseline={"date": "2026-11-09", "timing": "amc", "confidence": "confirmed"},
        structured_votes=[],
        announcements=[announcement("2026-11-09", "amc"), announcement("2026-11-09", "bmo")],
        as_of=date(2026, 10, 21),
    )
    assert decision["timing"] == "unknown"
    assert decision["timing_sources"] == []


@pytest.mark.parametrize("old_date, old_publication", [
    ("2026-11-09", "2026-10-18"),
    ("2026-11-04", "2026-10-19"),
])
def test_unknown_revision_does_not_borrow_an_older_or_different_event_session(
    old_date, old_publication,
):
    chosen, _ = calendar._announcement_decision([
        announcement("2026-11-09"),
        announcement(old_date, "amc", published=old_publication),
    ], as_of=date(2026, 10, 21))
    assert (chosen["date"], chosen["timing"]) == ("2026-11-09", "unknown")


@pytest.mark.parametrize("candidate_dates, expected_press, expected_alpha", [
    (None, ["NEAR", "NEXT"], ["NEXT"]),
    ({"NO_EVIDENCE": "2026-10-21"}, ["NO_EVIDENCE", "NEAR"], ["NO_EVIDENCE"]),
])
def test_escalation_targets_nearest_unresolved_announcement_within_budgets(
    monkeypatch, candidate_dates, expected_press, expected_alpha,
):
    news = {
        "RESOLVED": [announcement("2026-10-22", "amc")],
        "NO_EVIDENCE": [],
        "FAR": [announcement("2026-11-20")],
        "NEAR": [announcement("2026-10-22")],
        "NEXT": [announcement("2026-10-23")],
    }
    calls = {"finnhub": [], "twelvedata": [], "alphavantage": []}

    def finnhub(symbol, **kwargs):
        calls["finnhub"].append(symbol)
        return news[symbol], {"status": "ok", "requests": 1}

    def twelvedata(symbol, **kwargs):
        calls["twelvedata"].append(symbol)
        rows = [announcement("2026-10-22", "bmo", official=True)] if symbol == "NEAR" else []
        return rows, {"status": "ok"}

    def alpha(symbol, **kwargs):
        calls["alphavantage"].append(symbol)
        return [], {"status": "ok"}

    monkeypatch.setattr(calendar, "fetch_finnhub_announcements", finnhub)
    monkeypatch.setattr(calendar, "fetch_twelvedata_announcements", twelvedata)
    monkeypatch.setattr(calendar, "fetch_alphavantage_announcements", alpha)
    evidence, status = calendar._collect_announcements(
        list(news), today=date(2026, 10, 21), finnhub_key=None,
        twelvedata_key=None, alphavantage_key=None, finnhub_delay=0,
        twelvedata_delay=0, alphavantage_delay=0, alpha_news_max=1,
        twelvedata_max_symbols=2, finnhub_max_requests=5,
        **({"candidate_dates": candidate_dates} if candidate_dates else {}),
    )
    assert calls["finnhub"] == list(news)
    assert calls["twelvedata"] == expected_press
    assert calls["alphavantage"] == expected_alpha
    assert set(status["twelvedata_press_releases"]) == set(expected_press)
    chosen, _ = calendar._announcement_decision(evidence["NEAR"], as_of=date(2026, 10, 21))
    assert chosen["timing"] == "bmo"


def test_announcement_for_another_event_does_not_cancel_escalation(monkeypatch):
    monkeypatch.setattr(calendar, "fetch_finnhub_announcements", lambda *args, **kwargs: (
        [announcement("2026-11-20", "amc")], {"status": "ok", "requests": 1},
    ))
    monkeypatch.setattr(calendar, "fetch_twelvedata_announcements", lambda *args, **kwargs: (
        [announcement("2026-10-22", "bmo", official=True)], {"status": "ok"},
    ))
    evidence, status = calendar._collect_announcements(
        ["EXAMPLE"], today=date(2026, 10, 21), finnhub_key=None,
        twelvedata_key=None, alphavantage_key=None, finnhub_delay=0,
        twelvedata_delay=0, alphavantage_delay=0, alpha_news_max=0,
        twelvedata_max_symbols=1, finnhub_max_requests=1,
        candidate_dates={"EXAMPLE": "2026-10-22"},
    )
    chosen, _ = calendar._announcement_decision(evidence["EXAMPLE"], as_of=date(2026, 10, 21))
    assert (chosen["date"], chosen["timing"]) == ("2026-10-22", "bmo")
    assert list(status["twelvedata_press_releases"]) == ["EXAMPLE"]
