#!/usr/bin/env python3
"""Recover missing options sessions using only public DoltHub source data.

This command never promotes R2 data. The workflow promotes only after this
command accepts fresh reconciliation; fallback or rejected data is a failure.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from market_sessions import is_us_market_session, latest_completed_us_market_session
from options_snapshot_resilience import _validated_manifest, finalize_snapshot
from verify_refresh_recovery import verify_recovery
import sync_dolthub as dolt

ROOT = Path(__file__).resolve().parent.parent


def recovery_dates(published: date, target: date, *, now: datetime | None = None) -> list[date]:
    if target != latest_completed_us_market_session(now):
        raise RuntimeError('options recovery target must be the latest completed US market session')
    if target <= published:
        raise RuntimeError('options recovery target is already published; refusing duplicate ingestion')
    days = []
    cursor = published + timedelta(days=1)
    while cursor <= target:
        if is_us_market_session(cursor):
            days.append(cursor)
        if len(days) > 5:
            raise RuntimeError('options recovery is limited to five missing market sessions')
        cursor += timedelta(days=1)
    return days


def require_accepted_candidate(data_dir: Path, target: date, not_before: str,
                               *, now: datetime | None = None) -> dict:
    if target != latest_completed_us_market_session(now):
        raise RuntimeError('options recovery target became stale before promotion')
    manifest = _validated_manifest(data_dir / 'validation/data_reconciliation.json', not_before)
    status = json.loads((data_dir / 'validation/options_snapshot_status.json').read_text())
    generated = datetime.fromisoformat(status['generated_at'])
    started = datetime.fromisoformat(not_before)
    policy = status.get('policy') or {}
    if (
        generated.tzinfo is None or generated < started
        or status.get('schema') != 'quantiv.options-snapshot-status.v1'
        or status.get('state') != 'accepted'
        or status.get('active_source_date') != target.isoformat()
        or status.get('candidate_source_date') != target.isoformat()
        or status.get('candidate_manifest_id') != manifest.get('manifest_id')
        or status.get('critical_codes')
        or policy.get('strict_options_candidate_accepted') is not True
        or policy.get('scoring_allowed') is not True
        or policy.get('refresh_scoring_allowed') is not True
        or policy.get('fallback_mode') is not None
        or policy.get('thresholds_changed') is not False
        or manifest['quality']['decision_safe'] is not True
        or any((manifest.get(section) or {}).get('source_date') != target.isoformat()
               for section in ('source_reconciliation', 'quote_quality'))
    ):
        raise RuntimeError('options recovery remains held: fresh accepted decision-safe evidence required')
    return manifest


def restricted_query(sql: str, api_url: str = dolt.OPTIONS_API, retries: int = 3) -> list[dict]:
    allowed = {dolt.OPTIONS_API: {'option_chain'}, dolt.STOCKS_API: {'split', 'dividend'}}
    tables = re.findall(r'\bFROM\s+`?([a-z_]+)`?', sql, flags=re.IGNORECASE)
    if api_url not in allowed or not tables or any(table.lower() not in allowed[api_url] for table in tables):
        raise RuntimeError('options recovery may query only option_chain, split, and dividend on DoltHub')
    return _source_query(sql, api_url, retries)


_source_query = dolt.query


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-date', required=True, type=date.fromisoformat)
    parser.add_argument('--expected-release-id', required=True)
    parser.add_argument('--expected-manifest-id', required=True)
    args = parser.parse_args()
    data_dir = dolt.data_dir()
    started = datetime.now(timezone.utc).isoformat()
    original = verify_recovery(data_dir, expected_release_id=args.expected_release_id,
                               expected_manifest_id=args.expected_manifest_id)
    days = recovery_dates(date.fromisoformat(original['active_source_date']), args.target_date)
    # Keep the source boundary narrow even if an ingestion helper later grows.
    dolt.query = restricted_query
    if dolt.latest_dolthub_date() < args.target_date:
        raise RuntimeError('DoltHub has not published the requested options session')
    print(f'Options-only recovery: {days[0]} → {days[-1]} ({len(days)} sessions)', flush=True)
    dolt.sync_dates(days, dolt.parquet_root(), skip_existing=True)
    for day in days:
        partition = dolt.parquet_root() / f'year={day.year}/month={day.month:02d}/{day}.parquet'
        if not partition.is_file():
            raise RuntimeError(f'missing options partition after sync: {day}')
    dolt.sync_corporate_actions(end_date_str=args.target_date.isoformat())
    env = {**os.environ, 'DATA_DIR': str(data_dir)}
    subprocess.run([sys.executable, 'scripts/setup_duckdb_from_parquet.py'], cwd=ROOT, env=env, check=True)
    # Non-strict writes the full rejection evidence so finalize_snapshot can
    # quarantine a bad candidate. The strict acceptance gate below is mandatory.
    subprocess.run([sys.executable, 'scripts/build_data_reconciliation.py',
                    '--max-lag-days', '5', '--days-ahead', '21',
                    '--report', str(data_dir / 'validation/data_reconciliation.json')],
                   cwd=ROOT, env=env, check=True)
    finalize_snapshot(manifest_path=data_dir / 'validation/data_reconciliation.json',
                      data_dir=data_dir, not_before=started)
    report = require_accepted_candidate(data_dir, args.target_date, started)
    meta = dolt.load_meta()
    meta.update(last_sync_date=args.target_date.isoformat(), last_sync_time=started,
                last_options_candidate_status='accepted', mode='options-only-recovery')
    dolt.save_meta(meta)
    print(f"Accepted options recovery: {args.target_date} · {report['manifest_id']}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
