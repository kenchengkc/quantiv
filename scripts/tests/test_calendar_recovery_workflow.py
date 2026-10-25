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
    assert locate('verify_refresh_recovery.py') < locate('provider_market_hours.py --admit') < locate('reconcile_earnings_calendar.py') < locate('daily_score.py') < locate('model_publication.py monitor') < locate('build_frontend_data.py')
    repair = steps[locate('reconcile_earnings_calendar.py')]
    assert repair['if'] == "inputs.refresh_mode == 'calendar-only-recovery'"
    assert repair['env']['PROVIDER_FREE_RECOVERY'] == '0'
    assert repair['shell'] == 'python3 scripts/provider_market_hours.py --run-shell {0}'
    # Lookup cursors may survive downstream failure, but provider repair must
    # never promote prices, options, forecasts or model state through this path.
    state_uploads = [line.strip() for line in repair['run'].splitlines() if 'r2_push.sh' in line]
    assert state_uploads == ['bash scripts/r2_push.sh --runtime-state-only']
    assert 'r2_push_calendar_reference.sh' in repair['run']
    assert '--earnings' in repair['run']
    assert '--ohlcv' not in repair['run'] and '--corporate-actions' not in repair['run']
    assert 'check_earnings_calendar_integrity.py --require-baseline' in repair['run']
    assert 'prepare_publication_inputs' in repair['run']
    for step in steps:
        if step is not repair:
            assert 'FINNHUB_API_KEY' not in step.get('env', {})


def test_targeted_recovery_passes_validated_tickers_as_distinct_arguments(tmp_path):
    import json
    import os
    import subprocess
    import sys
    workflow = yaml.safe_load((ROOT / '.github/workflows/data-refresh.yml').read_text())
    repair = next(s for s in workflow['jobs']['recovery']['steps'] if 'reconcile_earnings_calendar.py' in s.get('run',''))
    probe = tmp_path / 'python'
    probe.write_text(f'#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\nif any("reconcile_earnings_calendar.py" in arg for arg in sys.argv[1:]):\n Path(os.environ["ARG_LOG"]).write_text(json.dumps([arg for arg in sys.argv[1:] if arg != "-u"]))\n sys.exit(42)\n')
    probe.chmod(0o755)
    log = tmp_path / 'arguments.json'
    env = {**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}', 'ARG_LOG': str(log)}
    for symbols in ['', 'T IBM GE MMM', 'T --days-ahead 365', 'T; false']:
        log.unlink(missing_ok=True)
        result = subprocess.run(['bash','-c',repair['run']], env={**env,'CALENDAR_SYMBOLS':symbols}, cwd=tmp_path, capture_output=True, text=True)
        if symbols in ['', 'T IBM GE MMM']:
            assert result.returncode == 42, result.stderr
            args = json.loads(log.read_text())
            assert args[:5] == ['scripts/reconcile_earnings_calendar.py', '--days-ahead', '60', '--announcement-days-ahead', '35']
            assert args[5:] == (['--symbols','T','IBM','GE','MMM'] if symbols else [])
        else:
            assert result.returncode == 2
            assert not log.exists()
