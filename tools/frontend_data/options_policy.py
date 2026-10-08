"""Admit independent frontend publication without admitting held options."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from data_release import verify_release
from options_snapshot_resilience import (
    SOFT_FALLBACK_CRITICAL_CODES,
    _critical_codes,
    _option_partitions,
    _published_options_date,
    _read_json,
    _require_source_date,
    _validated_manifest,
)


def options_display_available(
    data_dir: Path,
    *,
    as_of_date: date,
    now: datetime | None = None,
    verify_fallback: bool = False,
    not_before: str | None = None,
    partial_rebuild: bool = False,
) -> bool:
    """Return false only for a verified fallback; unknown stale input aborts.

    The release and restored reconciliation are checked read-only. A fallback
    authorizes independent display estimates, never option decision eligibility.
    Recovery may reuse recent saved evidence rather than invent a new report.
    """
    current = now or datetime.now(timezone.utc)
    status = _read_json(data_dir / 'validation/options_snapshot_status.json')
    if verify_fallback and status.get('state') == 'fallback':
        policy = status.get('policy') or {}
        if (
            status.get('schema') != 'quantiv.options-snapshot-status.v1'
            or policy.get('refresh_scoring_allowed') is not True
            or policy.get('scoring_allowed') is not False
            or policy.get('strict_options_candidate_accepted') is not False
            or policy.get('thresholds_changed') is not False
            or policy.get('fallback_mode') != 'last_published_snapshot'
        ):
            raise RuntimeError('invalid options fallback publication policy')
        report = _validated_manifest(
            data_dir / 'validation/data_reconciliation.json', not_before
        )
        core = {key: value for key, value in report.items()
                if key not in {'manifest_id', 'generated_at'}}
        digest = 'sha256:' + hashlib.sha256(json.dumps(
            core, sort_keys=True, separators=(',', ':'), default=str
        ).encode()).hexdigest()
        if digest != report.get('manifest_id') or digest != status.get('fallback_manifest_id'):
            raise RuntimeError('fallback does not match the active reconciliation manifest')
        try:
            generated = datetime.fromisoformat(str(report.get('generated_at')).replace('Z', '+00:00'))
            verified = datetime.fromisoformat(str(status.get('fallback_verified_at')).replace('Z', '+00:00'))
            if generated.tzinfo is None or verified.tzinfo is None:
                raise ValueError('timestamps require a timezone')
        except ValueError as exc:
            raise RuntimeError('fallback verification timestamp is invalid') from exc
        if generated > verified or verified > current:
            raise RuntimeError('fallback verification timestamp is inconsistent')
        if (current - generated).total_seconds() > 36 * 3600:
            raise RuntimeError('fallback requires current reconciliation evidence')
        active_date = as_of_date.isoformat()
        if (
            status.get('active_source_date') != active_date
            or _published_options_date(data_dir) != active_date
            or max(_option_partitions(data_dir), default=None) != active_date
        ):
            raise RuntimeError('fallback no longer matches the active published options')
        _require_source_date(report, active_date)
        if set(_critical_codes(report)) - SOFT_FALLBACK_CRITICAL_CODES:
            raise RuntimeError('fallback contains non-options critical failures')
        verify_release(data_dir)
        if partial_rebuild:
            raise ValueError('an options hold requires rebuilding every publication surface')
        return False

    age_days = (current.date() - as_of_date).days
    stale_threshold = int(os.getenv('STALE_OPTIONS_MAX_DAYS', '7'))
    if age_days > stale_threshold and not os.getenv('ALLOW_STALE_OPTIONS'):
        raise RuntimeError(
            f'Options chain is {age_days} days stale (as_of={as_of_date}, '
            f'threshold={stale_threshold}d); verified fallback evidence is required'
        )
    return True
