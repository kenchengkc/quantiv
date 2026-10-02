import importlib
import json
import re
from datetime import date

import pyarrow.parquet as pq
import pytest


@pytest.fixture
def source(tmp_path, monkeypatch):
    recovery = importlib.import_module('scripts.recover_options_snapshot')
    dolt = recovery.dolt
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setattr(dolt.time, 'sleep', lambda _: None)

    def query(sql, api_url=dolt.OPTIONS_API, retries=3):
        assert api_url == dolt.OPTIONS_API
        assert 'FROM volatility_history' in sql
        if 'ORDER BY date' in sql:
            return [{'date': '2026-10-01'}]
        day = re.search(r"date = '([^']+)'", sql).group(1)
        rows = []
        if day != '2026-09-29':
            row = {field.name: None for field in dolt.VOLHIST_SCHEMA}
            row.update(date=day, act_symbol='TEST', hv_current=.2, hv_year_high=.3,
                       hv_year_low=.1, iv_current=.3, iv_week_ago=.25,
                       iv_year_high=.4, iv_year_low=.2)
            rows = [row]
        if 'COUNT(*)' in sql:
            return [{'rows_count': len(rows)}]
        return rows

    monkeypatch.setattr(recovery, '_source_query', query)
    monkeypatch.setattr(dolt, 'query', query)
    monkeypatch.setattr(dolt, 'load_meta', lambda: {'last_volhist_date': '2026-09-27'})
    return recovery


def test_volatility_sync_honors_explicit_session_bounds(source, tmp_path):
    source.dolt.sync_volhist('2026-09-30', '2026-09-30')
    files = list(tmp_path.glob('parquet/volatility_history/year=*/month=*/*.parquet'))
    assert [p.name for p in files] == ['2026-09-30.parquet']
    assert pq.read_table(files[0]).column('date').to_pylist() == [date(2026, 9, 30)]


def test_options_recovery_includes_complete_volatility_history(source, tmp_path):
    recover = getattr(source, 'recover_volatility_history', None)
    assert callable(recover), 'options recovery must restore its volatility-history inputs'
    result = recover(tmp_path, [date(2026, 9, 29), date(2026, 9, 30)])
    assert result['source_date'] == '2026-09-30'
    assert result['expected_rows'] == result['received_rows'] == 1
    assert result['status'] == 'passed'
    assert (tmp_path / result['partition']).is_file()


def test_volatility_recovery_rejects_missing_latest_session(source, tmp_path, monkeypatch):
    recover = getattr(source, 'recover_volatility_history', None)
    assert callable(recover), 'options recovery must restore its volatility-history inputs'
    monkeypatch.setattr(source.dolt, 'sync_volhist', lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match='latest volatility-history partition'):
        recover(tmp_path, [date(2026, 9, 30)])


def test_volatility_recovery_rejects_truncated_partition(source, tmp_path, monkeypatch):
    recover = getattr(source, 'recover_volatility_history', None)
    assert callable(recover), 'options recovery must restore its volatility-history inputs'
    source.dolt.sync_volhist('2026-09-30', '2026-09-30')
    original = source._source_query

    def revised(sql, api_url, retries):
        return [{'rows_count': 2}] if 'COUNT(*)' in sql else original(sql, api_url, retries)

    monkeypatch.setattr(source, '_source_query', revised)
    with pytest.raises(RuntimeError, match='row count'):
        recover(tmp_path, [date(2026, 9, 30)])


@pytest.mark.parametrize('mutation', ['missing_file', 'digest', 'missing_receipt', 'wrong_date'])
def test_promotion_reverifies_volatility_history_without_source_calls(source, tmp_path, monkeypatch, mutation):
    vol = source.recover_volatility_history(tmp_path, [date(2026, 9, 30)])
    report = {'manifest_id': 'sha256:accepted'}
    monkeypatch.setattr(source, 'require_accepted_candidate', lambda *a, **kw: report)
    receipt = {'schema': 'quantiv.options-recovery.v1', 'target_date': '2026-09-30',
               'started_at': '2026-10-01T17:00:00Z', 'manifest_id': report['manifest_id'],
               'volatility_history': vol}
    if mutation == 'missing_file':
        (tmp_path / vol['partition']).unlink()
    elif mutation == 'digest':
        vol['sha256'] = '0' * 64
    elif mutation == 'missing_receipt':
        receipt.pop('volatility_history')
    elif mutation == 'wrong_date':
        vol['source_date'] = '2026-09-28'
    validation = tmp_path / 'validation'
    validation.mkdir()
    (validation / 'options_recovery.json').write_text(json.dumps(receipt))
    monkeypatch.setattr(source, '_source_query', lambda *a, **kw: pytest.fail('promotion must not query sources'))
    with pytest.raises(RuntimeError, match='volatility-history'):
        source.verify_promotion(tmp_path)
