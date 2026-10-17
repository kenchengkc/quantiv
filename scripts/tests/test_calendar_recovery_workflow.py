"""Calendar repair reuses the guarded serving path without repeating quote ingestion."""
from pathlib import Path

import yaml

from scripts.daily_refresh_claim import evaluate_claim

ROOT = Path(__file__).resolve().parents[2]


def test_calendar_recovery_does_not_claim_normal_daily_refresh():
    recovery = {'id': 10, 'created_at': '2026-10-17T04:30:00Z', 'event': 'workflow_dispatch',
                'display_title': 'Daily data refresh [calendar-only-recovery]'}
    normal = {'id': 20, 'created_at': '2026-10-17T06:00:00Z', 'event': 'workflow_dispatch',
              'display_title': 'Daily data refresh [normal]'}
    assert evaluate_claim(current_run=normal, workflow_runs=[recovery, normal])['owner_run_id'] == 20


def test_calendar_recovery_verifies_saved_release_before_repair_and_monitoring_before_publication():
    workflow = yaml.safe_load((ROOT / '.github/workflows/data-refresh.yml').read_text())
    trigger = workflow.get('on') or workflow[True]
    assert 'calendar-only-recovery' in trigger['workflow_dispatch']['inputs']['refresh_mode']['options']
    job = workflow['jobs']['recovery']
    assert "inputs.refresh_mode == 'calendar-only-recovery'" in job['if']
    assert job['concurrency']['group'] == 'daily-data-refresh-execution'
    steps = job['steps']
    commands = [s.get('run', '') for s in steps]
    def locate(text):
        return next(i for i, c in enumerate(commands) if text in c)
    assert locate('verify_refresh_recovery.py') < locate('reconcile_earnings_calendar.py') < locate('daily_score.py') < locate('model_publication.py monitor') < locate('build_frontend_data.py')
    repair = steps[locate('reconcile_earnings_calendar.py')]
    assert repair['if'] == "inputs.refresh_mode == 'calendar-only-recovery'"
    assert repair['env']['PROVIDER_FREE_RECOVERY'] == '0'
    assert '--earnings' in repair['run']
    assert '--ohlcv' not in repair['run'] and '--corporate-actions' not in repair['run']
    assert 'check_earnings_calendar_integrity.py --require-baseline' in repair['run']
    assert 'prepare_publication_inputs' in repair['run']
    for step in steps:
        if step is not repair:
            assert 'FINNHUB_API_KEY' not in step.get('env', {})
