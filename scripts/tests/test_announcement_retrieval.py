"""Capped news feeds must not hide authoritative future release announcements."""
from datetime import date, datetime, timezone

import pytest

import reconcile_earnings_calendar as calendar


def article(day, title='Market update', summary='', identity=1):
    return {'id': identity, 'datetime': int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()),
            'headline': title, 'summary': summary, 'url': f'https://example.com/{identity}'}


def test_truncated_feed_recovers_older_announcement_without_duplicate_votes(monkeypatch):
    recent = [article('2026-10-15', identity=i) for i in range(250)]
    release = article('2026-10-12', 'Meta to Announce Third Quarter 2026 Results',
                      'Meta will release financial results after market close on October 21, 2026.', 999)
    def transport(url, *, params, **kwargs):
        if params['to'] == '2026-10-17':
            return 200, recent, None
        return 200, [recent[0], release, release], None
    monkeypatch.setattr(calendar, '_request_json', transport)
    monkeypatch.setattr(calendar.time, 'sleep', lambda _: None)
    rows, meta = calendar.fetch_finnhub_announcements('META', today=date(2026, 10, 17), key='test', company_name='Meta Platforms')
    assert [(r['date'], r['timing']) for r in rows] == [('2026-10-21', 'amc')]
    assert meta['requests'] == 2


def test_capped_single_day_and_request_budget_are_reported_incomplete(monkeypatch):
    def transport(url, *, params, **kwargs):
        return 200, [article(params['to'], identity=i) for i in range(250)], None
    monkeypatch.setattr(calendar, '_request_json', transport)
    monkeypatch.setattr(calendar.time, 'sleep', lambda _: None)
    rows, meta = calendar.fetch_finnhub_announcements('META', today=date(2026, 10, 17), key='test', company_name='Meta Platforms', max_requests=3)
    assert rows == []
    assert meta['requests'] == 3
    assert meta['coverage_complete'] is False
    assert meta['truncated_days']


def test_backfill_error_preserves_found_evidence_and_reports_partial_coverage(monkeypatch):
    recent = [article('2026-10-15', identity=i) for i in range(250)]
    recent[0] = article('2026-10-15', 'Meta to Announce Third Quarter 2026 Results', 'Results on October 21, 2026.', 999)
    def transport(url, *, params, **kwargs):
        return (200, recent, None) if params['to'] == '2026-10-17' else (429, None, 'rate limited')
    monkeypatch.setattr(calendar, '_request_json', transport)
    monkeypatch.setattr(calendar.time, 'sleep', lambda _: None)
    rows, meta = calendar.fetch_finnhub_announcements('META', today=date(2026, 10, 17), key='test', company_name='Meta Platforms')
    assert len(rows) == 1
    assert meta['status'] == 'partial'
    assert meta['coverage_complete'] is False


@pytest.mark.parametrize(('phrase','timing'), [
    ('after the NYSE closes', 'amc'),
    ('prior to the opening of the Nasdaq Global Select Market', 'bmo'),
    ('before the opening of trading on the New York Stock Exchange', 'bmo'),
    ('after U.S. financial markets close', 'amc'),
    ('before U.S. financial markets open', 'bmo'),
])
def test_explicit_exchange_session_phrases(phrase, timing):
    result = calendar.extract_earnings_announcement(f'Example will release third quarter financial results on October 27, 2026 {phrase}.', published_on=date(2026, 10, 1))
    assert result == {'date': '2026-10-27', 'timing': timing}


def test_peer_issuer_ticker_in_body_cannot_supply_target_session():
    assert not calendar.finnhub_article_mentions_company({
        'headline': 'ARES CAPITAL CORPORATION SCHEDULES EARNINGS RELEASE',
        'summary': 'Ares Capital Corporation (NASDAQ: ARCC) will report earnings October 27, 2026 prior to the opening of the Nasdaq Global Select Market.',
    }, 'ARES', 'Ares Management')


@pytest.mark.parametrize('detail', [
    'The conference call will be held after market close.',
    'Financial results will be issued before the conference call at 5:00 p.m. ET.',
    'A webcast will be held at 8:00 a.m. ET.',
])
def test_call_or_webcast_cannot_supply_release_session(detail):
    result = calendar.extract_earnings_announcement('Example will release financial results on October 27, 2026. ' + detail, published_on=date(2026, 10, 1))
    assert result == {'date': '2026-10-27', 'timing': 'unknown'}


def test_generic_company_word_is_not_issuer_identity():
    assert not calendar.headline_mentions_company('American Electric Power to Report Third-Quarter Earnings', 'AXP', 'American Express Company')
    assert calendar.headline_mentions_company('Example (MSFT) to Report Earnings', 'MSFT', None)


def test_total_news_budget_reserves_one_request_per_symbol(monkeypatch):
    budgets = []
    def fetch(symbol, **kwargs):
        budgets.append(kwargs['max_requests'])
        return [], {'status': 'ok', 'requests': kwargs['max_requests']}
    monkeypatch.setattr(calendar, 'fetch_finnhub_announcements', fetch)
    calendar._collect_announcements(['AAA', 'BBB', 'CCC'], today=date(2026,10,17),
        finnhub_key='test', twelvedata_key=None, alphavantage_key=None,
        finnhub_delay=0, twelvedata_delay=0, alphavantage_delay=0,
        alpha_news_max=0, twelvedata_max_symbols=0, finnhub_max_requests=5)
    assert budgets == [3, 1, 1]


@pytest.mark.parametrize('detail', [
    'Management will host a conference call to discuss the earnings release after market close.',
    'Its quarterly earnings release will be discussed in an after market close webcast.',
])
def test_discussion_of_release_is_not_a_release_action(detail):
    result = calendar.extract_earnings_announcement('Example will release financial results on October 27, 2026. ' + detail, published_on=date(2026, 10, 1))
    assert result['timing'] == 'unknown'


def test_session_before_release_verb_is_supported():
    result = calendar.extract_earnings_announcement('Before market open on October 27, 2026, Example will release third quarter financial results.', published_on=date(2026, 10, 1))
    assert result == {'date': '2026-10-27', 'timing': 'bmo'}


def test_request_budget_counts_physical_rate_limited_calls(monkeypatch):
    calls = []
    class Limited:
        status_code = 429
        ok = False
        text = 'rate limited'
    def get(*args, **kwargs):
        calls.append(args)
        return Limited()
    monkeypatch.setattr(calendar.requests, 'get', get)
    monkeypatch.setattr(calendar.time, 'sleep', lambda _: None)
    _, meta = calendar.fetch_finnhub_announcements('META', today=date(2026,10,17), key='test', max_requests=1)
    assert len(calls) == meta['requests'] == 1
