"""Full issuer announcements close summary gaps without changing admission rules."""

from datetime import date

import reconcile_earnings_calendar as calendar


def test_full_release_resolves_missing_or_truncated_news_before_paid_escalation(monkeypatch):
    calls = []
    monkeypatch.setattr(calendar, "fetch_finnhub_announcements", lambda symbol, **kw: (
        [], {"status": "ok", "requests": 1},
    ))

    def full_release(symbol, **kwargs):
        calls.append(symbol)
        return [{"provider": "financialcontent_press_release", "date": "2026-11-05",
                 "timing": "amc", "published_on": "2026-10-14", "direct": True,
                 "official": False, "url": "https://markets.financialcontent.com/stocks/article/example"}], {"status": "ok"}

    monkeypatch.setattr(calendar, "fetch_public_announcements", full_release)
    monkeypatch.setattr(calendar, "fetch_twelvedata_announcements", lambda *a, **kw: (
        calls.append("paid") or [], {"status": "ok"},
    ))
    evidence, status = calendar._collect_announcements(
        ["EXAMPLE"], today=date(2026, 10, 25), finnhub_key=None,
        twelvedata_key=None, alphavantage_key=None, finnhub_delay=0,
        twelvedata_delay=0, alphavantage_delay=0, alpha_news_max=0,
        public_max_symbols=1, public_delay=0,
        candidate_dates={"EXAMPLE": "2026-11-05"},
    )
    assert calls == ["EXAMPLE"]
    assert evidence["EXAMPLE"][0]["timing"] == "amc"
    assert status["public_press_releases"]["EXAMPLE"]["status"] == "ok"


def test_full_release_confirmation_survives_the_next_run_baseline():
    baseline = calendar._row_vote({"date": "2026-11-05", "timing": "amc",
                                   "source": "dolt+financialcontent_press_release"}, "baseline")
    decision = calendar.choose_canonical_event(
        current={"date": "2026-11-05", "timing": "unknown"}, baseline=baseline,
        structured_votes=[], announcements=[], as_of=date(2026, 10, 25),
    )
    assert (decision["date"], decision["timing"]) == ("2026-11-05", "amc")


def test_public_source_rate_limit_stops_requests_but_keeps_other_sources(monkeypatch):
    calls = []
    monkeypatch.setattr(calendar, "fetch_finnhub_announcements", lambda *a, **kw: (
        [], {"status": "ok", "requests": 1},
    ))
    monkeypatch.setattr(calendar, "fetch_public_announcements", lambda symbol, **kw: (
        calls.append(symbol) or [], {"status": "rate_limited", "http_status": 429},
    ))
    monkeypatch.setattr(calendar, "fetch_twelvedata_announcements", lambda symbol, **kw: (
        calls.append(f"paid:{symbol}") or [], {"status": "ok"},
    ))
    calendar._collect_announcements(
        ["AAA", "BBB"], today=date(2026, 10, 25), finnhub_key=None,
        twelvedata_key=None, alphavantage_key=None, finnhub_delay=0,
        twelvedata_delay=0, alphavantage_delay=0, alpha_news_max=0,
        public_max_symbols=2, public_delay=0, twelvedata_max_symbols=1,
    )
    assert calls == ["AAA", "paid:AAA"]
