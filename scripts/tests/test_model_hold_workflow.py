from pathlib import Path

import pytest

import yaml


@pytest.mark.parametrize('job', ['refresh', 'recovery'])
def test_daily_handoff_uses_exact_scored_artifact_and_holds_empty_batch(job):
    workflow = yaml.safe_load((Path(__file__).resolve().parents[2] / '.github/workflows/data-refresh.yml').read_text())
    steps = workflow['jobs'][job]['steps']
    score = next(step for step in steps if step['name'].startswith('Score upcoming'))
    validate = next(step for step in steps if 'validate' in step['name'].lower() and 'forecast' in step['name'].lower())
    monitor = next(step for step in steps if step.get('id') == 'model_publication')
    build = next(step for step in steps if step['name'].startswith('Build frontend JSON'))
    assert score.get('id') == 'scoring'
    assert '--github-output "$GITHUB_OUTPUT"' in score['run']
    assert "steps.scoring.outputs.has_forecasts == 'true'" in validate['if']
    path = '${{ steps.scoring.outputs.forecast_path }}'
    assert f'--forecast-path "{path}"' in validate['run']
    assert f'--forecast-path "{path}"' in monitor['run']
    assert build['env']['MODEL_PUBLICATION_FORECAST_PATH'] == path
    assert 'has_forecasts' not in monitor.get('if', '')


def test_event_freeze_empty_batch_still_records_a_signed_hold():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[2] / '.github/workflows/event-forecast-freeze.yml').read_text())
    steps = workflow['jobs']['freeze']['steps']
    score = next(step for step in steps if step['name'].startswith('Score a candidate'))
    validate = next(step for step in steps if step['name'].startswith('Validate candidate'))
    monitor = next(step for step in steps if step.get('id') == 'model-publication')
    assert score.get('id') == 'scoring'
    assert '--github-output "$GITHUB_OUTPUT"' in score['run']
    assert "steps.scoring.outputs.has_forecasts == 'true'" in validate['if']
    assert 'has_forecasts' not in monitor.get('if', '')

ROOT = Path(__file__).resolve().parents[2]


def test_refresh_and_recovery_hold_only_ml_writes():
    jobs = yaml.safe_load((ROOT / '.github/workflows/data-refresh.yml').read_text())['jobs']
    for job_name in ('refresh', 'recovery'):
        steps = jobs[job_name]['steps']
        score = next(s for s in steps if s['name'].startswith('Score upcoming'))
        monitor = next(s for s in steps if s['name'].startswith('Monitor champion'))
        assert score['run'].index('model_publication.py snapshot') < score['run'].index('daily_score.py')
        assert steps.index(score) < steps.index(monitor)
        assert monitor['id'] == 'model_publication'
        assert 'model_publication.py monitor' in monitor['run']
        assert 'continue-on-error' not in monitor
        for step in steps:
            name = step['name']
            if name.startswith(('Import recent forecasts', 'Import recovered forecasts',
                                'Push forecasts', 'Push recovered forecasts',
                                'Persist forecast ledger', 'Persist recovered forecast publication')):
                assert "steps.model_publication.outputs.can_publish_ml == 'true'" in step['if']
        frontend = next(s for s in steps if s['name'].startswith('Build frontend JSON'))
        assert '--verify-model-publication' in frontend['run']
        assert 'can_publish_ml' not in frontend.get('if', '')
        evidence = next(s for s in steps if s.get('with', {}).get('name') == f'{job_name}-model-monitoring')
        assert evidence['if'] == 'always()'


def test_model_hold_does_not_change_limits_or_normal_monitor_exit():
    monitor = (ROOT / 'scripts/model_control_plane_impl.py').read_text()
    assert 'return 0 if report["status"] == "passed" else 1' in monitor
    control = (ROOT / 'apps/ml/ml/model_control.py').read_text()
    assert 'critical_psi: float = 0.35' in control
    assert 'min_rows: int = 100' in control
