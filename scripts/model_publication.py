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
from ml.model_bundle import verify_control_pointer, verify_monitor_receipt  # noqa: E402
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
    if report.get('schema') != 'quantiv.model-monitoring.v1' or not (held or eligible):
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
    return {'state': 'held' if held else 'eligible', 'can_publish_ml': eligible,
            'reason': 'feature_drift' if held else None,
            'monitored_at': report['monitored_at'], 'snapshot_date': report['snapshot_date'],
            'champion_bundle_id': report['champion_bundle_id']}


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
    if result != (0 if policy['can_publish_ml'] else 1):
        raise RuntimeError('monitoring exit status contradicts publication evidence')
    if args.github_output:
        with args.github_output.open('a') as output:
            output.write(f"can_publish_ml={str(policy['can_publish_ml']).lower()}\n")
    print(json.dumps(policy, sort_keys=True))
    if not policy['can_publish_ml']:
        print('::warning::ML publication held by signed model evidence; publishing independent data only.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
