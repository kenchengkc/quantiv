import importlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts.daily_refresh_claim import evaluate_claim
from scripts.frontend_publication_gate import evaluate_publication

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 1, 17, 0, tzinfo=timezone.utc)


@pytest.fixture
def recovery():
    assert (ROOT / 'scripts/recover_options_snapshot.py').exists(), 'options-only recovery is missing'
    return importlib.import_module('scripts.recover_options_snapshot')


def test_recovery_fetches_only_missing_completed_sessions(recovery):
    assert recovery.recovery_dates(date(2026, 9, 28), date(2026, 9, 30), now=NOW) == [
        date(2026, 9, 29), date(2026, 9, 30),
    ]


@pytest.mark.parametrize('published,target', [
    ('2026-09-28', '2026-09-29'),  # stale target
    ('2026-09-28', '2026-10-01'),  # incomplete session
    ('2026-09-30', '2026-09-30'),  # already promoted
    ('2026-09-01', '2026-09-30'),  # unbounded historical ingestion
])
def test_recovery_rejects_stale_incomplete_duplicate_or_large_range(recovery, published, target):
    with pytest.raises(RuntimeError):
        recovery.recovery_dates(date.fromisoformat(published), date.fromisoformat(target), now=NOW)


def _candidate(root):
    validation = root / 'validation'
    validation.mkdir()
    report = {
        'schema': 'quantiv.data-reconciliation.v2',
        'generated_at': '2026-10-01T16:50:00+00:00',
        'manifest_id': 'sha256:fresh',
        'quality': {'decision_safe': True, 'critical_exceptions': 0},
        'exceptions': [],
        'source_reconciliation': {'source_date': '2026-09-30'},
        'quote_quality': {'source_date': '2026-09-30'},
    }
    status = {
        'schema': 'quantiv.options-snapshot-status.v1',
        'generated_at': '2026-10-01T16:51:00+00:00',
        'state': 'accepted',
        'active_source_date': '2026-09-30',
        'candidate_source_date': '2026-09-30',
        'candidate_manifest_id': 'sha256:fresh',
        'critical_codes': [],
        'policy': {'strict_options_candidate_accepted': True, 'scoring_allowed': True,
                   'refresh_scoring_allowed': True, 'fallback_mode': None, 'thresholds_changed': False},
    }
    return report, status


def _write(root, report, status):
    (root / 'validation/data_reconciliation.json').write_text(json.dumps(report))
    (root / 'validation/options_snapshot_status.json').write_text(json.dumps(status))


def test_gate_accepts_fresh_matching_decision_safe_evidence(recovery, tmp_path):
    report, status = _candidate(tmp_path)
    _write(tmp_path, report, status)
    result = recovery.require_accepted_candidate(tmp_path, date(2026, 9, 30),
                                               '2026-10-01T16:40:00+00:00', now=NOW)
    assert result['manifest_id'] == 'sha256:fresh'


@pytest.mark.parametrize('mutation', ['fallback', 'unsafe', 'wrong_manifest', 'stale_report',
                                    'held_policy', 'wrong_source', 'stale_status', 'next_session'])
def test_gate_refuses_holds_or_stale_mismatched_evidence(recovery, tmp_path, mutation):
    report, status = _candidate(tmp_path)
    clock = NOW
    if mutation == 'fallback':
        status['state'] = 'fallback'
    elif mutation == 'unsafe':
        report['quality']['decision_safe'] = False
        report['quality']['critical_exceptions'] = 1
        report['exceptions'] = [{'severity': 'critical', 'code': 'options_stale'}]
    elif mutation == 'wrong_manifest':
        status['candidate_manifest_id'] = 'sha256:old'
    elif mutation == 'stale_report':
        report['generated_at'] = '2026-10-01T06:30:00+00:00'
    elif mutation == 'held_policy':
        status['policy']['fallback_mode'] = 'last_published_snapshot'
    elif mutation == 'wrong_source':
        report['quote_quality']['source_date'] = '2026-09-28'
    elif mutation == 'stale_status':
        status['generated_at'] = '2026-10-01T06:30:00+00:00'
    elif mutation == 'next_session':
        clock = datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)
    _write(tmp_path, report, status)
    with pytest.raises(RuntimeError):
        recovery.require_accepted_candidate(tmp_path, date(2026, 9, 30),
                                            '2026-10-01T16:40:00+00:00', now=clock)


def test_options_recovery_does_not_claim_normal_daily_ingestion():
    recovery_run = {'id': 10, 'created_at': '2026-10-01T04:30:00Z',
                    'event': 'workflow_dispatch', 'display_title': 'Daily data refresh [options-only-recovery]'}
    normal = {'id': 20, 'created_at': '2026-10-01T06:00:00Z',
              'event': 'workflow_dispatch', 'display_title': 'Daily data refresh [normal]'}
    result = evaluate_claim(current_run=normal, workflow_runs=[recovery_run, normal])
    assert result['claimed'] is True
    assert result['owner_run_id'] == 20


def test_options_data_only_run_does_not_publish_stale_frontend():
    result = evaluate_publication(event_name='workflow_run',
                                 workflow_run={'name': 'Daily data refresh', 'conclusion': 'success',
                                               'head_branch': 'main', 'event': 'workflow_dispatch'},
                                 jobs=[{'name': 'options_recovery', 'conclusion': 'success'},
                                       {'name': 'refresh', 'conclusion': 'skipped'},
                                       {'name': 'recovery', 'conclusion': 'skipped'}])
    assert result['should_publish'] is False


def test_options_promotion_never_writes_models_forecasts_calendar_or_runtime(tmp_path):
    data = tmp_path / 'data'
    partition = data / 'parquet/options_chain/year=2026/month=09/2026-09-30.parquet'
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b'content-addressed-test-partition')
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    log = tmp_path / 'rclone.log'
    rclone = binaries / 'rclone'
    rclone.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n')
    rclone.chmod(0o755)
    env = {**os.environ, 'PATH': f'{binaries}:{os.environ["PATH"]}',
           'DATA_DIR': str(data), 'PYTHON_BIN': sys.executable, 'CALL_LOG': str(log)}
    result = subprocess.run(['bash', 'scripts/r2_push.sh', '--options-recovery'],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert 'r2:quantiv-data/control/current_data_release.json' in calls
    for forbidden in ['/models', '/forecasts', '/calendar-reference', '/runtime-state', 'earnings_calendar']:
        assert forbidden not in calls
    assert calls.splitlines()[-1].endswith('r2:quantiv-data/control/current_data_release.json')


def test_workflow_options_recovery_is_explicit_and_has_no_quote_provider_credentials():
    workflow = yaml.safe_load((ROOT / '.github/workflows/data-refresh.yml').read_text())
    trigger = workflow.get('on') or workflow[True]
    assert 'options-only-recovery' in trigger['workflow_dispatch']['inputs']['refresh_mode']['options']
    job = workflow['jobs']['options_recovery']
    assert "inputs.refresh_mode == 'options-only-recovery'" in job['if']
    assert job['concurrency']['group'] == 'daily-data-refresh-execution'
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    assert 'recover_options_snapshot.py' in commands
    assert '--options-recovery' in commands
    assert '--expected-release-id' in commands and '--expected-manifest-id' in commands
    forbidden_keys = {'FINNHUB_API_KEY', 'FMP_API_KEY', 'TWELVEDATA_API_KEY', 'POLYGON_API_KEY'}
    for step in job['steps']:
        assert forbidden_keys.isdisjoint(step.get('env', {}))


@pytest.mark.parametrize('endpoint,sql', [
    ('https://finnhub.io/api/v1/quote', 'SELECT date FROM option_chain'),
    ('https://www.dolthub.com/api/v1alpha1/post-no-preference/stocks/master',
     'SELECT date FROM ohlcv'),
    ('https://www.dolthub.com/api/v1alpha1/post-no-preference/earnings/master',
     'SELECT date FROM earnings_calendar'),
])
def test_recovery_rejects_non_options_source_queries_before_network(recovery, endpoint, sql):
    with pytest.raises(RuntimeError, match='may query only'):
        recovery.restricted_query(sql, endpoint)
