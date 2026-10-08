from datetime import date, datetime, timezone
import hashlib
import json

import pytest

import build_frontend_data as builder
from scripts.data_release import build_release


NOW = datetime(2026, 10, 8, 7, tzinfo=timezone.utc)
AS_OF = date(2026, 9, 30)


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _fallback(tmp_path):
    data = tmp_path / 'data'
    partition = data / 'parquet/options_chain/year=2026/month=09/2026-09-30.parquet'
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b'published options snapshot')
    build_release(data)
    report = {
        'schema': 'quantiv.data-reconciliation.v2',
        'quality': {'decision_safe': False, 'critical_exceptions': 1},
        'exceptions': [{'severity': 'critical', 'code': 'options_stale'}],
        'source_reconciliation': {'source_date': AS_OF.isoformat()},
        'quote_quality': {'source_date': AS_OF.isoformat()},
    }
    report_id = 'sha256:' + hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()
    report.update(manifest_id=report_id, generated_at='2026-10-08T06:45:00+00:00')
    status = {
        'schema': 'quantiv.options-snapshot-status.v1', 'state': 'fallback',
        'active_source_date': AS_OF.isoformat(),
        'fallback_manifest_id': report_id,
        'fallback_verified_at': '2026-10-08T06:47:00+00:00',
        'policy': {'refresh_scoring_allowed': True, 'scoring_allowed': False,
                   'strict_options_candidate_accepted': False, 'thresholds_changed': False,
                   'fallback_mode': 'last_published_snapshot'},
    }
    _write(data / 'validation/data_reconciliation.json', report)
    _write(data / 'validation/options_snapshot_status.json', status)
    return data, report, status


def _available(data, **kwargs):
    return builder.options_display_available(
        data, as_of_date=AS_OF, now=NOW, verify_fallback=True, **kwargs
    )


def test_verified_eight_day_fallback_allows_only_independent_display(tmp_path):
    data, _, _ = _fallback(tmp_path)
    before = (data / 'validation/options_snapshot_status.json').read_bytes()
    assert _available(data) is False
    assert (data / 'validation/options_snapshot_status.json').read_bytes() == before


def test_old_unverified_options_keep_existing_abort(tmp_path):
    with pytest.raises(RuntimeError, match='stale'):
        _available(tmp_path)


def test_fresh_unheld_local_options_keep_existing_behavior(tmp_path):
    assert builder.options_display_available(
        tmp_path, as_of_date=date(2026, 10, 7), now=NOW
    ) is True


@pytest.mark.parametrize('field,value', [
    ('active_source_date', '2026-10-06'),
    ('fallback_manifest_id', 'sha256:wrong'),
    ('fallback_verified_at', None),
    ('fallback_verified_at', '2026-10-08T06:44:00+00:00'),
])
def test_fallback_requires_bound_verified_evidence(tmp_path, field, value):
    data, _, status = _fallback(tmp_path)
    status[field] = value
    _write(data / 'validation/options_snapshot_status.json', status)
    with pytest.raises(RuntimeError):
        _available(data)


def test_fallback_rejects_tampered_reconciliation(tmp_path):
    data, report, _ = _fallback(tmp_path)
    report['exceptions'][0]['code'] = 'ohlcv_stale'
    _write(data / 'validation/data_reconciliation.json', report)
    with pytest.raises(RuntimeError):
        _available(data)


def test_fallback_rejects_bound_non_options_failure(tmp_path):
    data, report, status = _fallback(tmp_path)
    report['exceptions'][0]['code'] = 'ohlcv_stale'
    core = {key: value for key, value in report.items()
            if key not in {'manifest_id', 'generated_at'}}
    report['manifest_id'] = 'sha256:' + hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()
    status['fallback_manifest_id'] = report['manifest_id']
    _write(data / 'validation/data_reconciliation.json', report)
    _write(data / 'validation/options_snapshot_status.json', status)
    with pytest.raises(RuntimeError, match='non-options critical'):
        _available(data)


def test_fallback_rejects_modified_published_options(tmp_path):
    data, _, _ = _fallback(tmp_path)
    next((data / 'parquet/options_chain').rglob('*.parquet')).write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='release verification'):
        _available(data)


def test_old_saved_recovery_report_cannot_authorize_fallback(tmp_path):
    data, report, status = _fallback(tmp_path)
    report['generated_at'] = '2026-10-06T06:45:00+00:00'
    status['fallback_verified_at'] = '2026-10-06T06:47:00+00:00'
    _write(data / 'validation/data_reconciliation.json', report)
    _write(data / 'validation/options_snapshot_status.json', status)
    with pytest.raises(RuntimeError, match='current reconciliation'):
        _available(data)


def test_saved_recovery_report_can_be_older_than_recovery_start(tmp_path):
    data, _, _ = _fallback(tmp_path)
    assert _available(data) is False
    with pytest.raises(RuntimeError, match='predates'):
        _available(data, not_before='2026-10-08T06:50:00+00:00')


def test_fallback_does_not_allow_partial_surface_rebuild(tmp_path):
    data, _, _ = _fallback(tmp_path)
    with pytest.raises(ValueError, match='every publication surface'):
        _available(data, partial_rebuild=True)


def test_builder_rebuilds_all_surfaces_from_verified_stale_fallback(tmp_path, monkeypatch):
    """Reproduce the failed build without ingestion, remote calls or a model run."""
    import sys
    import model_publication
    from frontend_data import display_forecast

    data, _, _ = _fallback(tmp_path)
    public = tmp_path / 'public'
    (public / 'symbols').mkdir(parents=True)
    upcoming = {
        'ticker': 'TEST', 'earnings_date': '2026-10-09', 'as_of_date': '2026-09-30',
        'timing': 'bmo', 'em_iv_pct': 0.15, 'em_straddle_pct': 0.12,
        'em_ml_pct': 0.20, 'display_forecast_method': 'ml',
        'display_forecast_pct': 0.20,
    }
    # A retained ticker outside the new calendar must not keep old decision fields.
    retained = {
        'symbol': 'RETAINED', 'as_of_date': AS_OF.isoformat(),
        'expected_move': {'earnings_date': '2026-10-12', 'iv_pct': 0.17,
                          'em_ml_pct': 0.19, 'em_method': 'ml_lightgbm'},
        'earnings_history': [],
    }
    _write(public / 'symbols/RETAINED.json', retained)

    class BuildDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 8)

    def earnings(conn, _path):
        conn.execute('CREATE TABLE earnings_events(ticker VARCHAR, earnings_dt DATE, timing VARCHAR)')
        conn.execute("INSERT INTO earnings_events VALUES ('TEST', '2026-10-09', 'bmo')")

    def views(conn, _data):
        conn.execute('CREATE TABLE v_options_chain(as_of_date DATE)')
        conn.execute("INSERT INTO v_options_chain VALUES ('2026-09-30')")

    def detail(_conn, ticker, _as_of, earn_dt, *_args):
        if ticker != 'TEST':
            return None
        return {'symbol': ticker, 'as_of_date': AS_OF.isoformat(),
                'expected_move': {'earnings_date': earn_dt.isoformat(), 'iv_pct': 0.15,
                                  'straddle_pct': 0.12, 'em_ml_pct': 0.20},
                'earnings_history': []}

    monkeypatch.setattr(builder, 'date', BuildDate)
    monkeypatch.setattr(builder, 'DATA_DIR', data)
    monkeypatch.setattr(builder, 'PUBLIC_DIR', public)
    monkeypatch.setattr(builder, 'write_to_public', lambda path, content:
                        _write_content(public / path, content))
    monkeypatch.setattr(builder, 'build_earnings_events_table', earnings)
    monkeypatch.setattr(builder, 'create_duckdb_views', views)
    monkeypatch.setattr(model_publication, 'verify_publication', lambda *_args, **_kwargs:
                        {'can_publish_ml': False})
    monkeypatch.setattr(builder, 'load_ml_forecasts', lambda **_kwargs: {})
    monkeypatch.setattr(builder, 'load_event_forecast_archive', lambda **_kwargs: {})
    monkeypatch.setattr(builder, 'load_provider_enrichments', lambda: {})
    monkeypatch.setattr(builder, 'collapse_duplicate_earnings', lambda *_args: (set(), []))
    monkeypatch.setattr(builder, 'load_published_calendar_events', lambda _path:
                        [('TEST', date(2026, 10, 9), 'bmo')])
    monkeypatch.setattr(builder, 'build_week_events', lambda _conn, _as_of, start, end, *_args, **_kw:
                        [dict(upcoming)] if start <= date(2026, 10, 9) <= end else [])
    monkeypatch.setattr(builder, 'build_symbol_detail', detail)
    monkeypatch.setattr(builder, 'enrich_reported_event_forecasts', lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(builder, 'enrich_realized_moves_from_ohlcv', lambda *_args: 0)
    for name in ('enrich_realized_moves_from_twelvedata', 'enrich_hist_move_avg_from_twelvedata',
                 'validate_twelvedata_against_ohlcv'):
        monkeypatch.setattr(builder, name, lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(display_forecast, '_ticker_historical_moves', lambda *_args, **_kwargs:
                        [0.04, 0.06, 0.05, 0.07])
    monkeypatch.setattr(sys, 'argv', ['build_frontend_data.py', '--verify-model-publication'])
    builder.main()

    for path in [public / 'weekly.json', public / 'screener.json',
                 *sorted((public / 'weeks').glob('*.json')),
                 *sorted((public / 'symbols').glob('*.json'))]:
        payload = json.loads(path.read_text())
        nodes = payload.get('events', []) + payload.get('earnings_history', [])
        if payload.get('expected_move'):
            nodes.append(payload['expected_move'])
        for node in nodes:
            assert node['display_forecast_method'] == 'historical'
            assert node['options_status'] == 'unavailable'
            assert node.get('em_iv_pct') is None
            assert node.get('iv_pct') is None
            assert node.get('em_ml_pct') is None
            assert node['ml_publication_status'] == 'held'
    assert json.loads((public / 'weekly.json').read_text())['events']
    assert json.loads((public / 'symbols/TEST.json').read_text())['expected_move']
    assert json.loads((public / 'symbols/RETAINED.json').read_text())['expected_move']


def _write_content(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
