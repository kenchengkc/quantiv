import json
from datetime import date

from tools.align_forecast_context import align_forecast_context


def test_exact_future_identity_uses_source_median_not_later_compact_history(tmp_path):
    (tmp_path / 'weeks').mkdir()
    (tmp_path / 'symbols').mkdir()
    events = [
        {'ticker': 'A', 'earnings_date': '2026-10-21', 'hist_move_med_4q': .04},
        {'ticker': 'B', 'earnings_date': '2026-10-21', 'hist_move_med_4q': None},
        {'ticker': 'C', 'earnings_date': '2026-10-21', 'hist_move_med_4q': .04},
        {'ticker': 'D', 'earnings_date': '2026-10-18', 'hist_move_med_4q': .04, 'eps_actual': 0},
    ]
    (tmp_path / 'weeks/2026-10-19.json').write_text(json.dumps({'metadata': {'as_of_date': '2026-10-15'}, 'events': events}))
    originals = {}
    for event in events:
        ticker = event['ticker']
        payload = {'symbol': ticker, 'as_of_date': '2026-10-15', 'expected_move': {
            'earnings_date': '2026-10-22' if ticker == 'C' else event['earnings_date'],
            'display_forecast_as_of': '2026-10-15', 'display_forecast_method': 'ml_unavailable'},
            'earnings_history': [{'date': '2026-10-16', 'actual': .9}]}
        originals[ticker] = payload
        (tmp_path / f'symbols/{ticker}.json').write_text(json.dumps(payload))
    assert align_forecast_context(tmp_path, today=date(2026, 10, 18)) == 2
    for ticker, expected in [('A', .04), ('B', None)]:
        result = json.loads((tmp_path / f'symbols/{ticker}.json').read_text())
        assert result['expected_move']['hist_move_med_4q'] == expected
        assert result['expected_move']['display_forecast_as_of'] == '2026-10-15'
    for ticker in ('C', 'D'):
        assert json.loads((tmp_path / f'symbols/{ticker}.json').read_text()) == originals[ticker]
    assert align_forecast_context(tmp_path, today=date(2026, 10, 18)) == 0


def test_same_day_unreported_context_requires_matching_pre_event_source(tmp_path):
    (tmp_path / 'weeks').mkdir()
    (tmp_path / 'symbols').mkdir()
    (tmp_path / 'weeks/2026-10-19.json').write_text(json.dumps({'events': [
        {'ticker': 'A', 'earnings_date': '2026-10-19', 'as_of_date': '2026-10-16', 'hist_move_med_4q': .04},
    ]}))
    path = tmp_path / 'symbols/A.json'
    payload = {'symbol': 'A', 'as_of_date': '2026-10-15',
               'expected_move': {'earnings_date': '2026-10-19'}}
    path.write_text(json.dumps(payload))
    assert align_forecast_context(tmp_path, today=date(2026, 10, 19)) == 0
    payload['as_of_date'] = '2026-10-16'
    path.write_text(json.dumps(payload))
    assert align_forecast_context(tmp_path, today=date(2026, 10, 19)) == 1
    assert json.loads(path.read_text())['expected_move']['hist_move_med_4q'] == .04
