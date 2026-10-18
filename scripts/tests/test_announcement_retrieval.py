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


@pytest.mark.parametrize('period', [
    'quarter ended', 'quarter ending', 'fiscal year ended', 'six months ended',
    'quarter ending on Wednesday,',
])
def test_fiscal_period_end_cannot_replace_announced_release_date(period):
    text = (
        f'Example schedules earnings. Example will release results for the {period} '
        'September 30, 2026, after the close of trading on October 19, 2026. '
        'A conference call follows on October 20, 2026 at 9:00 a.m. ET.'
    )
    assert calendar.extract_earnings_announcement(text, published_on=date(2026, 9, 22)) == {
        'date': '2026-10-19', 'timing': 'amc',
    }


def test_fiscal_period_date_alone_does_not_invent_a_release_date():
    text = ('Example schedules its earnings release for the quarter ending '
            'September 30, 2026. The announcement date will be provided later.')
    assert calendar.extract_earnings_announcement(text, published_on=date(2026, 9, 22)) is None


def test_before_market_shorthand_is_release_session():
    text = ('Example will release its third quarter 2026 earnings before market on '
            'November 2, 2026. Management will conduct a call at 11:00 a.m. ET.')
    assert calendar.extract_earnings_announcement(text, published_on=date(2026, 10, 13)) == {
        'date': '2026-11-02', 'timing': 'bmo',
    }


def test_before_market_close_is_not_a_before_open_release():
    text = 'Example will release earnings before market close on November 2, 2026.'
    assert calendar.extract_earnings_announcement(text, published_on=date(2026, 10, 13)) == {
        'date': '2026-11-02', 'timing': 'unknown',
    }


@pytest.mark.parametrize('title', [
    'Example to Host Third Quarter 2026 Earnings Call',
    'Example Will Host Third Quarter 2026 Earnings Call',
    'Example Plans Live Audio Webcast of Third Quarter 2026 Earnings Conference Call',
])
def test_host_or_planned_webcast_announcement_keeps_explicit_release_evidence(title):
    result = calendar._announcement_from_article(
        provider='finnhub_company_news', title=title,
        body=('Example will issue financial results on October 21, 2026 at 7:00 a.m. ET. '
              'The conference call follows at 9:00 a.m. ET.'),
        published_on=date(2026, 10, 1), official=False, url='https://example.com/earnings',
    )
    assert result is not None
    assert (result['date'], result['timing']) == ('2026-10-21', 'bmo')


@pytest.mark.parametrize('title', [
    'Example to Host Third Quarter 2026 Earnings Call',
    'Example Will Host Third Quarter 2026 Earnings Call',
    'Example Plans Live Audio Webcast of Third Quarter 2026 Earnings Conference Call',
])
@pytest.mark.parametrize('history', ['', 'Example reported its last quarterly results on July 21, 2026. '])
@pytest.mark.parametrize('replay', [
    '', ' A replay of the earnings conference call will be available on the investor website.',
    ' Example will post a replay of the webcast following the call.',
])
def test_host_announcement_without_release_statement_cannot_move_earnings_date(title, history, replay):
    assert calendar._announcement_from_article(
        provider='finnhub_company_news', title=title,
        body=history + 'The conference call will take place on October 21, 2026 at 7:00 a.m. ET.' + replay,
        published_on=date(2026, 10, 1), official=False, url='https://example.com/earnings',
    ) is None


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


def test_session_between_release_and_following_call_belongs_to_call():
    text = 'Example will release financial results on October 27, 2026 and, after market close, host a conference call to discuss the results.'
    assert calendar.extract_earnings_announcement(text, published_on=date(2026,10,1))['timing'] == 'unknown'


@pytest.mark.parametrize(('verb','phrase','timing'), [('announced','before market open','bmo'), ('reported','after market close','amc')])
def test_passive_release_actions(verb, phrase, timing):
    text = f'Example financial results will be {verb} on October 27, 2026 {phrase}.'
    assert calendar.extract_earnings_announcement(text, published_on=date(2026,10,1))['timing'] == timing


def test_next_day_morning_call_does_not_bound_release_session():
    text = 'Example will release financial results on October 27, 2026. Results will be available prior to the conference call on October 28 at 8:00 a.m. ET.'
    assert calendar.extract_earnings_announcement(text, published_on=date(2026,10,1))['timing'] == 'unknown'


def test_release_clock_before_call_in_same_sentence():
    text = (
        'Acadian Asset Management Inc. will announce its financial and operating results '
        'for the third quarter ended September 30, 2026, on Thursday, October 29, 2026. '
        'The Company will announce its results through a press release and related slide '
        'presentation at 7:30 a.m. Eastern Time and will hold a conference call and '
        'simultaneous webcast to discuss the results at 11:00 a.m. Eastern Time.'
    )
    assert calendar.extract_earnings_announcement(text, published_on=date(2026, 10, 14)) == {
        'date': '2026-10-29', 'timing': 'bmo',
    }


@pytest.mark.parametrize('conjunction', ['and will', 'and the company will', 'while management will'])
def test_separate_release_and_call_clock_clauses(conjunction):
    text = (
        'Example will host a conference call at 8:00 a.m. ET '
        f'{conjunction} release earnings on October 29, 2026 at 4:05 p.m. ET.'
    )
    assert calendar.extract_earnings_announcement(text, published_on=date(2026, 10, 14)) == {
        'date': '2026-10-29', 'timing': 'amc',
    }


@pytest.mark.parametrize('detail', [
    'and will hold a conference call at 8:00 a.m. ET',
    'and related slides for its conference call at 8:00 a.m. ET',
    'and will host a webcast at 4:05 p.m. ET',
])
def test_call_clock_after_untimed_release_stays_unknown(detail):
    text = f'Example will release results on October 29, 2026 {detail}.'
    assert calendar.extract_earnings_announcement(text, published_on=date(2026, 10, 14)) == {
        'date': '2026-10-29', 'timing': 'unknown',
    }


@pytest.mark.parametrize('call', ['will host a conference call', 'management will host a conference call'])
def test_conjunction_separates_release_from_later_call(call):
    text = f'Example will release financial results on October 27, 2026 and, after market close, {call}.'
    assert calendar.extract_earnings_announcement(text, published_on=date(2026,10,1))['timing'] == 'unknown'


@pytest.mark.parametrize('detail', [
    'The conference call and release materials will be discussed at 8:00 a.m. ET.',
    'Management will discuss the conference call and release schedule at 8:00 a.m. ET.',
    'Management will host a conference call and will release a replay at 4:05 p.m. ET.',
    'Management will host a conference call at 11:00 a.m. ET and will release a transcript at 4:05 p.m. ET.',
    'Management will discuss the earnings release at 8:00 a.m. ET and will host a conference call at 11:00 a.m. ET.',
])
def test_release_noun_phrase_cannot_discard_discussion_context(detail):
    text = f'Example will release results on October 29, 2026. {detail}'
    assert calendar.extract_earnings_announcement(text, published_on=date(2026, 10, 14)) == {
        'date': '2026-10-29', 'timing': 'unknown',
    }


@pytest.mark.parametrize(('symbol','name','headline'), [
    ('T','AT&T','AT&T to Release Third-Quarter 2026 Earnings'),
    ('IBM','International Business Machines','IBM to Announce Third-Quarter 2026 Results'),
    ('MMM','3M','3M Announces Upcoming Quarterly Earnings Call'),
    ('GE','General Electric','GE Aerospace to Announce Third Quarter 2026 Results'),
])
def test_short_or_branded_issuer_names_are_not_discarded(symbol, name, headline):
    assert calendar.headline_mentions_company(headline, symbol, name)


def test_incidental_ticker_word_does_not_identify_issuer():
    assert not calendar.headline_mentions_company('Apple to announce AI results', 'AI', 'C3.ai')
    assert not calendar.headline_mentions_company('AI firms to announce quarterly results', 'AI', 'C3.ai')
    assert not calendar.headline_mentions_company('ALL companies to announce quarterly results', 'ALL', 'Allstate')
    assert not calendar.headline_mentions_company('GEAR to announce results', 'GE', 'General Electric')


@pytest.mark.parametrize('brand', ['GE HealthCare', 'GE Vernova'])
def test_ge_peer_brands_cannot_supply_aerospace_release(brand):
    article = {'headline': f'{brand} to Announce Third Quarter 2026 Results',
               'summary': 'Financial results will be released before market open on October 27, 2026.'}
    assert not calendar.finnhub_article_mentions_company(article, 'GE', 'General Electric')
