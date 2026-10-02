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
import hashlib

import pyarrow.parquet as pq

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


def verify_synced_sessions(data_dir: Path, days: list[date]) -> list[str]:
    """Keep the latest session mandatory; retain proved upstream historical gaps."""
    empty_sessions = []
    for day in days:
        partition = data_dir / f'parquet/options_chain/year={day.year}/month={day.month:02d}/{day}.parquet'
        if partition.is_file() and partition.stat().st_size > 0:
            continue
        if day == days[-1]:
            raise RuntimeError(f'missing latest options partition after sync: {day}')
        try:
            receipt = json.loads((data_dir / f'control/ingestion/options/{day}.json').read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'missing options partition without source-empty evidence: {day}') from exc
        buckets = receipt.get('pagination') or []
        if (
            receipt.get('schema') != 'quantiv.options-ingestion.v1'
            or receipt.get('status') != 'passed'
            or receipt.get('source') != 'dolthub/post-no-preference/options/option_chain'
            or receipt.get('source_date') != day.isoformat()
            or receipt.get('expected_rows') != 0 or receipt.get('received_rows') != 0
            or receipt.get('partition') is not None
            or receipt.get('replay_equivalence') != 'verified'
            or receipt.get('duplicate_primary_keys') != 0
            or receipt.get('expected_method') != 'exhaustive_keyset_pagination'
            or len(buckets) != len(dolt.SYMBOL_BUCKETS)
            or {tuple(bucket.get('symbol_range') or []) for bucket in buckets} != set(dolt.SYMBOL_BUCKETS)
            or any(bucket.get('exhausted') is not True or bucket.get('rows') != 0
                   or not isinstance(bucket.get('pages'), int) or bucket['pages'] < 1
                   for bucket in buckets)
        ):
            raise RuntimeError(f'missing options partition has unverified source-empty evidence: {day}')
        print(f'Upstream source-empty intermediate session: {day}; preserving its zero-row receipt', flush=True)
        empty_sessions.append(day.isoformat())
    return empty_sessions


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
    allowed = {dolt.OPTIONS_API: {'option_chain', 'volatility_history'}, dolt.STOCKS_API: {'split', 'dividend'}}
    tables = re.findall(r'\bFROM\s+`?([a-z_]+)`?', sql, flags=re.IGNORECASE)
    # These ingestion helpers emit one-table SELECTs. Reject unsupported SQL
    # structures instead of trying to implement a general SQL parser here.
    source_clause = re.split(r'\bFROM\b', sql, flags=re.IGNORECASE)[-1]
    source_clause = re.split(r'\b(?:WHERE|ORDER|GROUP|LIMIT)\b', source_clause, flags=re.IGNORECASE)[0]
    if (api_url not in allowed or len(tables) != 1
        or re.search(r'\b(?:JOIN|UNION|INSERT|UPDATE|DELETE|DROP)\b', sql, flags=re.IGNORECASE)
        or ',' in source_clause
        or tables[0].lower() not in allowed.get(api_url, set())):
        raise RuntimeError('options recovery may query only option_chain, volatility_history, split, and dividend on DoltHub')
    return _source_query(sql, api_url, retries)


_source_query = dolt.query


def _volatility_partition(data_dir: Path, target: date):
    partition = data_dir / f'parquet/volatility_history/year={target.year}/month={target.month:02d}/{target}.parquet'
    if not partition.is_file():
        raise RuntimeError(f'missing latest volatility-history partition after sync: {target}')
    parquet = pq.ParquetFile(partition)
    if not parquet.schema_arrow.remove_metadata().equals(dolt.VOLHIST_SCHEMA.remove_metadata()):
        raise RuntimeError('latest volatility-history partition has invalid schema')
    frame = parquet.read().to_pandas()
    if (frame.empty
        or frame['date'].isna().any() or set(frame['date']) != {target}
        or frame['act_symbol'].isna().any() or frame['act_symbol'].astype(str).str.strip().eq('').any()
        or frame.duplicated(['date', 'act_symbol']).any()):
        raise RuntimeError('latest volatility-history partition has invalid schema or primary keys')
    return partition, frame


def recover_volatility_history(data_dir: Path, days: list[date]) -> dict:
    """Recover matching public options-derived features, with a complete latest partition."""
    dolt.sync_volhist(start_date_str=days[0].isoformat(), end_date_str=days[-1].isoformat())
    target = days[-1]
    partition, frame = _volatility_partition(data_dir, target)
    rows = restricted_query(f"SELECT COUNT(*) AS rows_count FROM volatility_history WHERE date = '{target}'")
    expected = int(rows[0]['rows_count'])
    if len(frame) != expected:
        raise RuntimeError(f'volatility-history source row count mismatch: expected {expected}, received {len(frame)}')
    return {'status': 'passed', 'source_date': target.isoformat(), 'expected_rows': expected,
            'received_rows': len(frame), 'partition': partition.relative_to(data_dir).as_posix(),
            'sha256': hashlib.sha256(partition.read_bytes()).hexdigest()}


def verify_promotion(data_dir: Path) -> dict:
    receipt = json.loads((data_dir / 'validation/options_recovery.json').read_text())
    if receipt.get('schema') != 'quantiv.options-recovery.v1':
        raise RuntimeError('options recovery acceptance receipt is unavailable')
    report = require_accepted_candidate(data_dir, date.fromisoformat(receipt['target_date']),
                                       receipt['started_at'])
    if report.get('manifest_id') != receipt.get('manifest_id'):
        raise RuntimeError('options recovery acceptance receipt references a different candidate')
    target = date.fromisoformat(receipt['target_date'])
    vol = receipt.get('volatility_history') or {}
    expected_path = f'parquet/volatility_history/year={target.year}/month={target.month:02d}/{target}.parquet'
    if (vol.get('status') != 'passed' or vol.get('source_date') != target.isoformat()
        or vol.get('partition') != expected_path
        or type(vol.get('expected_rows')) is not int or vol['expected_rows'] <= 0
        or vol.get('received_rows') != vol['expected_rows']):
        raise RuntimeError('options recovery requires a complete latest volatility-history receipt')
    partition, frame = _volatility_partition(data_dir, target)
    if (len(frame) != vol['received_rows']
        or hashlib.sha256(partition.read_bytes()).hexdigest() != vol.get('sha256')):
        raise RuntimeError('latest volatility-history partition does not match its accepted receipt')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--target-date', type=date.fromisoformat)
    parser.add_argument('--expected-release-id', default='')
    parser.add_argument('--expected-manifest-id', default='')
    args = parser.parse_args()
    data_dir = dolt.data_dir()
    if args.verify_only:
        verify_promotion(data_dir)
        print('Options recovery acceptance reverified immediately before promotion')
        return 0
    if args.target_date is None:
        parser.error('--target-date is required for options recovery')
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
    empty_sessions = verify_synced_sessions(data_dir, days)
    dolt.sync_corporate_actions(end_date_str=args.target_date.isoformat())
    volatility_history = recover_volatility_history(data_dir, days)
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
    receipt = {'schema': 'quantiv.options-recovery.v1', 'started_at': started,
               'target_date': args.target_date.isoformat(), 'manifest_id': report['manifest_id'],
               'source_empty_sessions': empty_sessions, 'volatility_history': volatility_history}
    (data_dir / 'validation/options_recovery.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f"Accepted options recovery: {args.target_date} · {report['manifest_id']}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
