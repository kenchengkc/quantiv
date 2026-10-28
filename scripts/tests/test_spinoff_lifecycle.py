from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts import apply_ticker_lifecycle as lifecycle
from scripts import delisted


def test_msgs_transition_preserves_historical_identity_and_separate_companies():
    assert delisted.ticker_renames().get('MSGS') == 'MSGK'
    assert delisted.canonical_earnings_ticker('MSGS', '2026-10-26') == 'MSGS'
    assert delisted.canonical_earnings_ticker('MSGS', '2026-10-27') == 'MSGK'
    assert delisted.canonical_action_ticker('MSGS') == 'MSGS'
    assert delisted.canonical_action_ticker('BK') == 'BNY'
    assert delisted.canonical_earnings_ticker('BK', '2025-01-01') == 'BNY'
    for symbol in ('MSGK', 'MSGR', 'MSGE'):
        assert delisted.canonical_earnings_ticker(symbol, '2026-10-28') == symbol
        assert not delisted.is_retired(symbol)
    assert not delisted.is_delisted('MSGS')


def test_spinoff_earnings_history_survives_in_csv_and_parquet(tmp_path):
    rows = [
        {'act_symbol': 'MSGS', 'date': '2026-08-12', 'eps_actual': 0.42},
        {'act_symbol': 'MSGE', 'date': '2026-08-12', 'eps_actual': 9.9},
        {'act_symbol': 'MSGR', 'date': '2026-10-28', 'eps_actual': 0.1},
        {'act_symbol': 'MSGS', 'date': '2026-10-30', 'eps_actual': 0.2},
        {'act_symbol': 'MSGK', 'date': '2026-10-30', 'eps_actual': 0.3},
    ]
    frame = pd.DataFrame(rows)
    frame.to_csv(tmp_path / 'earnings_calendar.csv', index=False)
    frame.to_parquet(tmp_path / 'earnings_calendar.parquet', index=False)
    lifecycle.apply_artifacts(tmp_path)
    expected = pd.DataFrame([rows[1], rows[0], rows[2], rows[4]])
    pd.testing.assert_frame_equal(pd.read_csv(tmp_path / 'earnings_calendar.csv'), expected)
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / 'earnings_calendar.parquet'), expected)


def test_spinoff_requires_a_valid_event_date():
    with pytest.raises(ValueError):
        delisted.canonical_earnings_ticker('MSGS', 'unknown')


@pytest.mark.parametrize('provider', ['dolthub', 'finnhub'])
def test_provider_overlay_cannot_rewrite_pre_spin_earnings(tmp_path, monkeypatch, provider):
    rows = [
        {'act_symbol': 'MSGS', 'date': '2026-08-12', 'timing': 'bmo'},
        {'act_symbol': 'MSGS', 'date': '2026-10-30', 'timing': 'bmo'},
        {'act_symbol': 'MSGE', 'date': '2026-08-12', 'timing': 'bmo'},
        {'act_symbol': 'MSGR', 'date': '2026-10-30', 'timing': 'bmo'},
    ]
    if provider == 'finnhub':
        from scripts.sync_finnhub_earnings import normalize_existing
        result = normalize_existing(pd.DataFrame(rows))
    else:
        from scripts import sync_dolthub
        source = [{'act_symbol': row['act_symbol'], 'date': row['date'],
                   'when': 'Before market open'} for row in rows]
        monkeypatch.setattr(sync_dolthub, 'query', lambda *args, **kwargs: source)
        monkeypatch.setattr(sync_dolthub, 'data_dir', lambda: tmp_path)
        monkeypatch.setattr(sync_dolthub, 'SYMBOLS_DIR', tmp_path / 'symbols')
        sync_dolthub.sync_earnings()
        result = pd.read_csv(tmp_path / 'earnings_calendar.csv')
    assert {(symbol, str(day)) for symbol, day in result[['act_symbol', 'date']].itertuples(index=False)} == {
        ('MSGS', '2026-08-12'), ('MSGK', '2026-10-30'),
        ('MSGE', '2026-08-12'), ('MSGR', '2026-10-30'),
    }


@pytest.mark.parametrize('override', [
    {'renamed_on': 'unknown'}, {'history_policy': None},
    {'price_history_continuous': True}, {'new': 'MSGS'},
])
def test_bad_discontinuity_metadata_fails_closed(tmp_path, monkeypatch, override):
    spec = {'new': 'MSGK', 'renamed_on': '2026-10-27',
            'history_policy': 'preserve_pre_transition', 'price_history_continuous': False}
    spec.update(override)
    path = tmp_path / 'renames.json'
    path.write_text(json.dumps({'renames': {'MSGS': spec}}))
    monkeypatch.setattr(delisted, 'RENAMES_PATH', path)
    delisted.ticker_history_cutovers.cache_clear()
    try:
        with pytest.raises(ValueError):
            delisted.ticker_history_cutovers()
    finally:
        delisted.ticker_history_cutovers.cache_clear()


def test_provider_can_drop_old_symbol_without_erasing_its_earnings(tmp_path, monkeypatch):
    from scripts import sync_dolthub

    prior = {'act_symbol': 'MSGS', 'date': '2026-08-12', 'timing': 'bmo', 'eps_actual': .42}
    pd.DataFrame([prior]).to_csv(tmp_path / 'earnings_calendar.csv', index=False)
    monkeypatch.setattr(sync_dolthub, 'query', lambda *args, **kwargs: [
        {'act_symbol': 'MSGK', 'date': '2026-10-30', 'when': 'Before market open'},
    ])
    monkeypatch.setattr(sync_dolthub, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(sync_dolthub, 'SYMBOLS_DIR', tmp_path / 'no_old_forecast_pages')
    sync_dolthub.sync_earnings()
    result = pd.read_csv(tmp_path / 'earnings_calendar.csv').set_index('act_symbol')
    assert result.loc['MSGS', 'eps_actual'] == .42
    assert result.loc['MSGS', 'date'] == '2026-08-12'
    assert 'MSGK' in result.index


def test_action_sync_cannot_collapse_parent_and_successor_rows(monkeypatch):
    from datetime import date
    from scripts import sync_dolthub

    monkeypatch.setattr(sync_dolthub, 'API_DELAY', 0)
    monkeypatch.setattr(sync_dolthub, 'query', lambda *args, **kwargs: [
        {'act_symbol': symbol, 'ex_date': '2026-10-28', 'amount': .2}
        for symbol in ('MSGK', 'MSGS')
    ])
    result, _ = sync_dolthub._fetch_action_table(
        'dividend', ['amount'], ['MSGK', 'MSGS'], date(2019, 1, 1), date(2026, 10, 28),
    )
    assert result.act_symbol.tolist() == ['MSGK', 'MSGS']
