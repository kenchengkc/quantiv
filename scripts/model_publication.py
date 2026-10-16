#!/usr/bin/env python3
"""Publish independent data while a verified model drift failure holds ML."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'apps/ml'))

from ml.model_artifact import sha256_file  # noqa: E402
from ml.model_bundle import verify_bundle_dir, verify_control_pointer, verify_monitor_receipt  # noqa: E402
from ml.pipeline_validation import latest_forecast_path  # noqa: E402

ARCHIVE_FILES = ('event_forecast_archive.parquet', 'event_prediction_publications.parquet')


def backup_dir(data_dir: Path) -> Path:
    return data_dir / '.published_forecasts'


def backup_manifest(data_dir: Path) -> Path:
    return backup_dir(data_dir) / 'manifest.json'


def snapshot_published_forecasts(data_dir: Path) -> None:
    """Capture the durable pre-score archive; never substitute newly scored rows."""
    destination = backup_dir(data_dir)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    files = []
    for name in ARCHIVE_FILES:
        source = data_dir / 'forecasts' / name
        if source.is_file():
            shutil.copy2(source, destination / name)
            files.append({'name': name, 'sha256': sha256_file(destination / name)})
    backup_manifest(data_dir).write_text(json.dumps({'files': files}, sort_keys=True) + '\n')


def verify_publication(data_dir: Path, *, not_before: str | None = None, forecast_path: Path | None = None) -> dict:
    monitoring = data_dir / 'models/monitoring'
    report_path = monitoring / 'latest_monitoring.json'
    report = json.loads(report_path.read_text())
    receipt = json.loads((monitoring / 'latest_monitoring.receipt.json').read_text())
    verify_monitor_receipt(receipt, ledger_path=monitoring / 'prediction_ledger.parquet',
                          report_path=report_path)
    pointer = verify_control_pointer(json.loads((data_dir / 'models/control/champion.json').read_text()))
    if report.get('champion_bundle_id') != pointer['champion_bundle_id']:
        raise RuntimeError('monitoring champion does not match the active signed pointer')
    data_pointer = data_dir / 'control/current_data_release.json'
    release_id = json.loads(data_pointer.read_text()).get('release_id') if data_pointer.is_file() else None
    if report.get('current_data_release_id') != release_id:
        raise RuntimeError('monitoring data release does not match the current source release')
    configured = os.getenv('MODEL_PUBLICATION_FORECAST_PATH')
    forecast = forecast_path or (Path(configured) if configured else latest_forecast_path(data_dir / 'forecasts'))
    if forecast is not None:
        try:
            forecast.resolve().relative_to(data_dir.resolve())
        except ValueError as exc:
            raise RuntimeError('monitored forecast path escapes data directory') from exc
    if forecast is None or report.get('forecast_sha256') != sha256_file(forecast):
        raise RuntimeError('monitored forecast digest does not match the current scored snapshot')
    monitored_at = datetime.fromisoformat(report['monitored_at'].replace('Z', '+00:00'))
    if monitored_at.tzinfo is None:
        raise RuntimeError('monitoring timestamp must be timezone-aware')
    if not_before and monitored_at < datetime.fromisoformat(not_before.replace('Z', '+00:00')):
        raise RuntimeError('monitoring report predates this refresh')
    drift = (report.get('feature_drift') or {}).get('status')
    held = report.get('status') == 'failed' and drift in {'critical', 'insufficient_data', 'unsupported_cohort'}
    eligible = report.get('status') == 'passed' and drift in {'passed', 'warning'}
    selection = report.get('publication_eligibility')
    if selection is not None:
        policy = _verify_event_eligibility(data_dir, forecast, report, selection, pointer)
        held = policy['state'] in {'held', 'partial'}
        eligible = policy['can_publish_ml']
    if report.get('schema') != 'quantiv.model-monitoring.v1' or (selection is None and not (held or eligible)):
        raise RuntimeError('monitoring failure is not a verified feature-drift publication hold')
    if held:
        manifest = backup_manifest(data_dir)
        if not manifest.is_file() or report.get('published_forecasts_sha256') != sha256_file(manifest):
            raise RuntimeError('published forecast archive backup digest does not match monitoring')
        files = json.loads(manifest.read_text())['files']
        names = [item['name'] for item in files]
        if len(set(names)) != len(names) or set(names) - set(ARCHIVE_FILES):
            raise RuntimeError('invalid published forecast archive backup')
        if {path.name for path in backup_dir(data_dir).iterdir()} != {'manifest.json', *names}:
            raise RuntimeError('published forecast archive backup inventory mismatch')
        for item in files:
            path = backup_dir(data_dir) / item['name']
            if not path.is_file() or sha256_file(path) != item['sha256']:
                raise RuntimeError('published forecast archive backup digest mismatch')
    if selection is not None:
        return {**policy, 'monitored_at': report['monitored_at'], 'snapshot_date': report['snapshot_date'],
                'champion_bundle_id': report['champion_bundle_id']}
    return {'state': 'held' if held else 'eligible', 'can_publish_ml': eligible,
            'reason': 'feature_drift' if held else None,
            'monitored_at': report['monitored_at'], 'snapshot_date': report['snapshot_date'],
            'champion_bundle_id': report['champion_bundle_id']}


def _verify_event_eligibility(data_dir: Path, candidate_path: Path, report: dict,
                              selection: dict, pointer: dict) -> dict:
    import pandas as pd
    try:
        from event_forecast_eligibility import SCHEMA, event_keys, verify_candidate_identity
    except ModuleNotFoundError:
        from scripts.event_forecast_eligibility import SCHEMA, event_keys, verify_candidate_identity

    if not isinstance(selection, dict) or selection.get('schema') != SCHEMA:
        raise RuntimeError('invalid event forecast eligibility schema')
    if selection.get('candidate_sha256') != report.get('forecast_sha256'):
        raise RuntimeError('event eligibility candidate digest differs from monitoring')
    relative = selection.get('eligible_forecast_path')
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise RuntimeError('eligible forecast path must be relative to data directory')
    eligible_path = data_dir / relative
    try:
        eligible_path.resolve().relative_to((data_dir / 'forecasts/eligible').resolve())
    except ValueError as exc:
        raise RuntimeError('eligible forecast path escapes the publication directory') from exc
    if not eligible_path.is_file() or selection.get('eligible_forecast_sha256') != sha256_file(eligible_path):
        raise RuntimeError('eligible forecast digest differs from signed selection')
    champion_id = pointer['champion_bundle_id']
    bundle = data_dir / 'models/bundles' / champion_id
    manifest = verify_bundle_dir(bundle)
    candidate = verify_candidate_identity(pd.read_parquet(candidate_path), bundle, champion_id, manifest['horizons'])
    selected = pd.read_parquet(eligible_path)
    verify_candidate_identity(selected, bundle, champion_id, manifest['horizons'],
                              require_admitted_cohorts=True)
    from ml.forecast_snapshot import forecast_snapshot_id, forecast_snapshot_key
    if selection.get('eligible_snapshot_ids') != sorted(forecast_snapshot_id(row) for row in selected.to_dict('records')):
        raise RuntimeError('eligible snapshot bindings differ from signed monitoring')
    if selection.get('eligible_snapshot_bindings') != {forecast_snapshot_key(row): forecast_snapshot_id(row)
                                                      for row in selected.to_dict('records')}:
        raise RuntimeError('eligible event snapshot bindings differ from signed monitoring')
    if set(selected.columns) != set(candidate.columns):
        raise RuntimeError('eligible forecast schema differs from candidate')
    ids = sorted(selected['forecast_id'].astype(str).tolist())
    events = sorted(set(event_keys(selected)))
    if len(set(ids)) != len(ids) or selection.get('eligible_forecast_ids') != ids or selection.get('eligible_event_keys') != events:
        raise RuntimeError('eligible forecast identities differ from signed selection')
    if selection.get('eligible_forecast_rows') != len(selected):
        raise RuntimeError('eligible forecast row count differs from signed selection')
    if not set(ids).issubset(set(candidate['forecast_id'].astype(str))):
        raise RuntimeError('eligible forecasts are not candidate observations')
    expected = candidate.loc[candidate['forecast_id'].isin(ids)].sort_values('forecast_id').reset_index(drop=True)
    actual = selected[list(candidate.columns)].sort_values('forecast_id').reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(expected, actual, check_dtype=False, check_exact=True)
    except AssertionError as exc:
        raise RuntimeError('eligible forecasts differ from exact candidate observations') from exc
    all_events = set(event_keys(candidate))
    held_keys = sorted(all_events - set(events))
    coverage = {'candidate_rows': len(candidate), 'eligible_rows': len(selected),
                'held_rows': len(candidate) - len(selected), 'candidate_events': len(all_events),
                'eligible_events': len(events), 'held_events': len(held_keys)}
    if selection.get('coverage') != coverage or selection.get('held_event_keys') != held_keys:
        raise RuntimeError('event forecast coverage differs from exact candidate selection')
    held_events = selection.get('held_events')
    if not isinstance(held_events, list) or [item.get('event_key') for item in held_events] != held_keys or any(
        not isinstance(item.get('reasons'), list) or not item['reasons'] for item in held_events
    ):
        raise RuntimeError('event forecast held reasons are incomplete')
    expected_status = 'passed' if len(selected) == len(candidate) and len(selected) else 'partial' if len(selected) else 'failed'
    if report.get('status') != expected_status:
        raise RuntimeError('monitoring state contradicts event forecast selection')
    state = {'passed': 'eligible', 'partial': 'partial', 'failed': 'held'}[expected_status]
    return {**selection, 'eligible_forecast_path': str(eligible_path.resolve()), 'state': state,
            'can_publish_ml': bool(len(selected)), 'has_forecasts': bool(len(selected)),
            'reason': None if state == 'eligible' else 'event_forecast_eligibility'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['snapshot', 'monitor'])
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--github-output', type=Path)
    parser.add_argument('--forecast-path', type=Path)
    parser.add_argument('--days-ahead', type=int, default=21)
    args = parser.parse_args()
    if args.command == 'snapshot':
        snapshot_published_forecasts(args.data_dir)
        return 0
    from model_control_plane_impl import monitor
    # Exceptions, signature failures and malformed evidence remain fatal. Only
    # a completed, signed drift assessment can select the data-only path.
    result = monitor(argparse.Namespace(models_root=args.data_dir / 'models',
                                       forecast_dir=args.data_dir / 'forecasts', forecast_path=args.forecast_path,
                                       days_ahead=args.days_ahead))
    policy = verify_publication(args.data_dir, not_before=os.getenv('REFRESH_STARTED_AT'), forecast_path=args.forecast_path)
    if result != (0 if policy['state'] == 'eligible' else 1):
        raise RuntimeError('monitoring exit status contradicts publication evidence')
    if args.github_output:
        with args.github_output.open('a') as output:
            output.write(f"can_publish_ml={str(policy['can_publish_ml']).lower()}\n")
            if 'eligible_forecast_path' in policy:
                output.write(f"eligible_forecast_path={policy['eligible_forecast_path']}\n")
                output.write(f"has_forecasts={str(policy['has_forecasts']).lower()}\n")
                for key, value in policy['coverage'].items():
                    output.write(f"{key}={value}\n")
    print(json.dumps(policy, sort_keys=True))
    if policy['state'] == 'partial':
        coverage = policy['coverage']
        print(f"::warning::ML coverage is partial: {coverage['eligible_events']} eligible events; {coverage['held_events']} held events.")
    elif not policy['can_publish_ml']:
        print('::warning::ML publication held by signed model evidence; publishing independent data only.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
