from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ml.model_artifact import sha256_file
from ml.model_bundle import create_signed_control_pointer, create_signed_monitor_receipt


@pytest.fixture
def signed_monitor(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    monkeypatch.setenv('MODEL_BUNDLE_SIGNING_KEY', private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode())
    monkeypatch.setenv('MODEL_BUNDLE_PUBLIC_KEY', private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    publication = importlib.import_module('scripts.model_publication')
    forecasts = tmp_path / 'forecasts'
    forecasts.mkdir()
    (forecasts / 'event_forecast_archive.parquet').write_bytes(b'previous published archive')
    publication.snapshot_published_forecasts(tmp_path)
    forecast = forecasts / 'forecasts_2026-10-02.parquet'
    forecast.write_bytes(b'new scored forecasts')
    monitoring = tmp_path / 'models/monitoring'
    monitoring.mkdir(parents=True)
    ledger = monitoring / 'prediction_ledger.parquet'
    ledger.write_bytes(b'ledger')
    control = tmp_path / 'models/control'
    control.mkdir()
    pointer = create_signed_control_pointer(bundle_id='a' * 64,
                                             previous_bundle_id=None, decision={'reason': 'test'})
    (control / 'champion.json').write_text(json.dumps(pointer))

    def write(status='critical', *, bundle_id='a' * 64):
        report = {
            'schema': 'quantiv.model-monitoring.v1',
            'status': 'failed' if status == 'critical' else 'passed',
            'monitored_at': '2026-10-02T06:39:29+00:00',
            'snapshot_date': '2026-10-01',
            'champion_bundle_id': bundle_id,
            'forecast_sha256': sha256_file(forecast),
            'published_forecasts_sha256': sha256_file(publication.backup_manifest(tmp_path)),
            'feature_drift': {'status': status, 'critical_features': 15, 'critical_limit': 5},
        }
        path = monitoring / 'latest_monitoring.json'
        path.write_text(json.dumps(report))
        receipt = create_signed_monitor_receipt(ledger_path=ledger, report_path=path,
                                                snapshot_date=report['snapshot_date'])
        (monitoring / 'latest_monitoring.receipt.json').write_text(json.dumps(receipt))
        return report

    write()
    return publication, tmp_path, write


def test_critical_drift_holds_ml_without_marking_monitor_passed(signed_monitor):
    publication, root, _ = signed_monitor
    policy = publication.verify_publication(root, not_before='2026-10-02T06:00:53Z')
    assert policy['state'] == 'held'
    assert policy['can_publish_ml'] is False
    assert policy['reason'] == 'feature_drift'
    assert json.loads((root / 'models/monitoring/latest_monitoring.json').read_text())['status'] == 'failed'


def test_passed_monitor_allows_ml(signed_monitor):
    publication, root, write = signed_monitor
    write('passed')
    assert publication.verify_publication(root)['can_publish_ml'] is True


@pytest.mark.parametrize('mutation', ['report', 'forecast', 'archive', 'champion', 'stale', 'other_failure', 'extra_archive'])
def test_publication_refuses_unverified_or_mismatched_evidence(signed_monitor, mutation):
    publication, root, write = signed_monitor
    if mutation == 'report':
        (root / 'models/monitoring/latest_monitoring.json').write_text('{}')
    elif mutation == 'forecast':
        (root / 'forecasts/forecasts_2026-10-02.parquet').write_bytes(b'changed')
    elif mutation == 'archive':
        (publication.backup_dir(root) / 'event_forecast_archive.parquet').write_bytes(b'changed')
    elif mutation == 'champion':
        write(bundle_id='b' * 64)
    elif mutation == 'extra_archive':
        (root / 'forecasts/event_forecast_archive.parquet').unlink()
        publication.snapshot_published_forecasts(root)
        write()
        (publication.backup_dir(root) / 'event_forecast_archive.parquet').write_bytes(b'unsigned archive')
    elif mutation == 'other_failure':
        report = write('unavailable')
        report['status'] = 'failed'
        path = root / 'models/monitoring/latest_monitoring.json'
        path.write_text(json.dumps(report))
        receipt = create_signed_monitor_receipt(
            ledger_path=root / 'models/monitoring/prediction_ledger.parquet', report_path=path,
            snapshot_date=report['snapshot_date'])
        (root / 'models/monitoring/latest_monitoring.receipt.json').write_text(json.dumps(receipt))
    with pytest.raises((ValueError, RuntimeError), match='digest|size|forecast|champion|predates|verified|inventory'):
        publication.verify_publication(root, not_before='2026-10-02T07:00:00Z' if mutation == 'stale' else None)


def test_held_loader_does_not_read_current_scored_ledger(monkeypatch):
    from tools.frontend_data import forecast_artifacts
    monkeypatch.setattr(forecast_artifacts, 'EVENT_PREDICTION_LEDGER_PATH', Path('/unavailable/ledger'))
    assert forecast_artifacts.load_ml_forecasts(publication_held=True) == {}


def test_held_archive_uses_pre_score_rows_not_newly_frozen_candidate(tmp_path, monkeypatch):
    import pandas as pd
    from tools.frontend_data import forecast_artifacts
    publication = importlib.import_module('scripts.model_publication')
    current = tmp_path / 'forecasts'
    current.mkdir()
    archive = current / 'event_forecast_archive.parquet'
    frame = pd.DataFrame([{'act_symbol': 'TEST', 'earnings_date': '2026-10-05',
                           'snapshot_date': '2026-09-30', 'forecast_id': 'published', 'em_ml_pct': .06}])
    frame.to_parquet(archive)
    publication.snapshot_published_forecasts(tmp_path)
    frame.loc[0, 'forecast_id'] = 'held-new-candidate'
    frame.loc[0, 'em_ml_pct'] = .99
    frame.to_parquet(archive)
    monkeypatch.setattr(forecast_artifacts, 'EVENT_FORECAST_ARCHIVE_PATH', archive)
    retained = forecast_artifacts.load_event_forecast_archive(forecasts_dir=publication.backup_dir(tmp_path))
    assert retained['TEST', '2026-10-05']['forecast_id'] == 'published'
    assert retained['TEST', '2026-10-05']['em_ml_pct'] == .06


def test_monitor_wrapper_outputs_hold_without_retrying_or_publishing_ml(signed_monitor, monkeypatch):
    publication, root, _ = signed_monitor
    import model_control_plane_impl
    calls = []
    def completed_monitor(args):
        calls.append(args)
        return 1
    monkeypatch.setattr(model_control_plane_impl, 'monitor', completed_monitor)
    output = root / 'github-output'
    monkeypatch.setattr('sys.argv', ['model_publication.py', 'monitor', '--data-dir', str(root),
                                    '--github-output', str(output)])
    assert publication.main() == 0
    assert len(calls) == 1
    assert output.read_text() == 'can_publish_ml=false\n'


def test_hold_removes_upcoming_ml_from_retained_symbol_files_but_preserves_history(tmp_path):
    from datetime import date
    from tools.frontend_data import forecast_artifacts
    symbols = tmp_path / 'symbols'
    symbols.mkdir()
    path = symbols / 'RETAINED.json'
    historical = {'date': '2026-09-23', 'em_ml_pct': .04, 'display_forecast_method': 'ml'}
    future_history = {'date': '2026-10-21', 'em_ml_pct': .99, 'p90': .99,
                      'display_forecast_method': 'ml', 'display_forecast_pct': .99,
                      'forecast_id': 'future-history-ml'}
    path.write_text(json.dumps({'symbol': 'RETAINED', 'as_of_date': '2026-09-28',
        'expected_move': {'earnings_date': '2026-10-21', 'em_ml_pct': .09, 'p10': .01,
            'display_forecast_pct': .09, 'display_forecast_method': 'ml',
            'iv_pct': .07, 'forecast_id': 'retained-ml'}, 'earnings_history': [historical, future_history]}))
    hold = getattr(forecast_artifacts, 'withhold_upcoming_ml', None)
    assert callable(hold), 'hold must cover retained public files as well as rebuilt events'
    hold(tmp_path, today=date(2026, 10, 2))
    result = json.loads(path.read_text())
    expected = result['expected_move']
    assert expected.get('em_ml_pct') is None
    assert expected.get('p10') is None and expected.get('forecast_id') is None
    assert expected['display_forecast_method'] == 'options_math'
    assert expected['display_forecast_pct'] == .07
    assert expected['display_forecast_as_of'] == '2026-09-28'
    assert result['earnings_history'][0] == historical
    assert result['earnings_history'][1].get('em_ml_pct') is None
    assert result['earnings_history'][1].get('p90') is None
    assert result['earnings_history'][1].get('forecast_id') is None
    assert result['earnings_history'][1].get('display_forecast_method') != 'ml'


def test_hold_calendar_and_symbol_use_same_historical_fallback(tmp_path):
    from datetime import date
    from tools.frontend_data import forecast_artifacts
    symbols = tmp_path / 'symbols'
    symbols.mkdir()
    history = [{'date': f'2026-0{i}-01', 'actual': move} for i, move in enumerate([.04, .06, .08, .10], 1)]
    symbol = {'symbol': 'TEST', 'as_of_date': '2026-09-30', 'earnings_history': history,
              'expected_move': {'earnings_date': '2026-10-21', 'em_ml_pct': .09,
                                'display_forecast_method': 'ml', 'display_forecast_pct': .09}}
    (symbols / 'TEST.json').write_text(json.dumps(symbol))
    event = {'ticker': 'TEST', 'earnings_date': '2026-10-21', 'as_of_date': '2026-09-30',
             'em_ml_pct': .09, 'display_forecast_method': 'ml', 'display_forecast_pct': .09,
             'hist_move_med_4q': .12}
    path = tmp_path / 'weekly.json'
    path.write_text(json.dumps({'events': [event]}))
    forecast_artifacts.withhold_upcoming_ml(tmp_path, today=date(2026, 10, 2))
    held = json.loads(path.read_text())['events'][0]
    assert held['display_forecast_method'] == 'historical'
    assert held['display_forecast_pct'] == pytest.approx(.07)
    assert held['hist_move_med_4q'] == pytest.approx(.07)
    assert held['display_forecast_as_of'] == '2026-09-30'
    assert held['options_status'] == 'unavailable'
    assert held['historical_event_count'] == 4
    assert held['em_method'] == 'historical'


def test_hold_refuses_aligning_forecasts_from_different_source_dates(tmp_path):
    from datetime import date
    from tools.frontend_data import forecast_artifacts
    symbols = tmp_path / 'symbols'
    symbols.mkdir()
    (symbols / 'TEST.json').write_text(json.dumps({'symbol': 'TEST', 'as_of_date': '2026-10-01',
        'expected_move': {'earnings_date': '2026-10-21', 'iv_pct': .07}, 'earnings_history': []}))
    (tmp_path / 'weekly.json').write_text(json.dumps({'events': [{'ticker': 'TEST',
        'earnings_date': '2026-10-21', 'as_of_date': '2026-09-20', 'em_ml_pct': .09,
        'display_forecast_method': 'ml', 'display_forecast_pct': .09}]}))
    with pytest.raises(RuntimeError, match='source dates'):
        forecast_artifacts.withhold_upcoming_ml(tmp_path, today=date(2026, 10, 2))
