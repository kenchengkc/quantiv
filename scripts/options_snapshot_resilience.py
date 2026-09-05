#!/usr/bin/env python3
"""Finalize a nightly options candidate without weakening quote-quality gates.

The daily refresh evaluates newly synced local options partitions before R2
promotion. If reconciliation fails only on options freshness, event coverage,
or quote quality, every options partition newer than the currently published R2
data release is quarantined and the last published options snapshot remains
active. Healthy non-options datasets can then continue through publication.

Unrelated critical reconciliation failures remain fatal. This is a blast-radius
control, not a threshold override.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SOFT_FALLBACK_CRITICAL_CODES = frozenset(
    {
        "event_quote_coverage_below_limit",
        "option_quote_quality_below_limit",
        "options_stale",
    }
)


@dataclass(frozen=True)
class FinalizationResult:
    state: str
    can_score: bool
    active_source_date: str | None
    candidate_source_date: str | None
    critical_codes: tuple[str, ...]
    quarantined_source_dates: tuple[str, ...] = ()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _parse_partition_date(value: str) -> str | None:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return value


def _option_partitions(data_dir: Path) -> dict[str, Path]:
    root = data_dir / "parquet" / "options_chain"
    result: dict[str, Path] = {}
    if not root.exists():
        return result
    for path in root.glob("year=*/month=*/*.parquet"):
        source_date = _parse_partition_date(path.stem)
        if source_date:
            result[source_date] = path
    return result


def _published_options_date(data_dir: Path) -> str | None:
    """Return the newest options date in the atomically published R2 release."""
    pointer_path = data_dir / "control" / "current_data_release.json"
    pointer = _read_json(pointer_path)
    manifest_value = pointer.get("manifest")
    if not manifest_value:
        return None

    manifest = _read_json(data_dir / str(manifest_value))
    dates: list[str] = []
    for item in manifest.get("files") or []:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("path") or "")
        if not relative.startswith("parquet/options_chain/") or not relative.endswith(
            ".parquet"
        ):
            continue
        source_date = _parse_partition_date(Path(relative).stem)
        if source_date:
            dates.append(source_date)
    return max(dates) if dates else None


def _manifest_source_date(manifest: dict[str, Any]) -> str | None:
    source = manifest.get("source_reconciliation") or {}
    quote = manifest.get("quote_quality") or {}
    value = source.get("source_date") or quote.get("source_date")
    return str(value) if value else None


def _critical_codes(manifest: dict[str, Any]) -> tuple[str, ...]:
    codes = {
        str(item.get("code"))
        for item in (manifest.get("exceptions") or [])
        if isinstance(item, dict)
        and item.get("severity") == "critical"
        and item.get("code")
    }
    return tuple(sorted(codes))


def _write_github_outputs(path: Path | None, result: FinalizationResult) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(f"options_state={result.state}\n")
        handle.write(f"can_score={'true' if result.can_score else 'false'}\n")
        handle.write(f"active_source_date={result.active_source_date or ''}\n")
        handle.write(f"candidate_source_date={result.candidate_source_date or ''}\n")


def _update_sync_metadata(
    data_dir: Path,
    active_date: str,
    candidate_date: str | None,
) -> None:
    path = data_dir / "sync_metadata.json"
    payload = _read_json(path)
    payload["last_sync_date"] = active_date
    payload["last_options_candidate_date"] = candidate_date
    payload["last_options_candidate_status"] = "rejected"
    payload["last_options_candidate_finalized_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    _atomic_json(path, payload)


def _status_payload(
    manifest: dict[str, Any],
    *,
    state: str,
    sync_outcome: str,
    active_date: str | None,
    candidate_date: str | None,
    critical_codes: tuple[str, ...],
    quarantined_source_dates: tuple[str, ...] = (),
    quarantine_dir: Path | None = None,
) -> dict[str, Any]:
    return {
        "schema": "quantiv.options-snapshot-status.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "sync_outcome": sync_outcome,
        "active_source_date": active_date,
        "candidate_source_date": candidate_date,
        "candidate_manifest_id": manifest.get("manifest_id"),
        "critical_codes": list(critical_codes),
        "quarantined_source_dates": list(quarantined_source_dates),
        "candidate_quarantine": str(quarantine_dir) if quarantine_dir else None,
        "policy": {
            "thresholds_changed": False,
            "scoring_allowed": state == "accepted",
            "fallback_mode": "last_published_snapshot" if state == "fallback" else None,
        },
    }


def _remove_empty_partition_dirs(path: Path) -> None:
    for parent in (path.parent, path.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            pass


def _quarantine_unpublished_partitions(
    *,
    data_dir: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    published_date: str,
    candidate_date: str | None,
) -> tuple[Path | None, tuple[str, ...]]:
    partitions = _option_partitions(data_dir)
    unpublished_dates = tuple(sorted(date for date in partitions if date > published_date))
    if not unpublished_dates:
        return None, ()

    safe_manifest_id = str(manifest.get("manifest_id") or "unidentified").replace(
        ":", "-"
    )
    quarantine_dir = (
        data_dir
        / "quarantine"
        / "options_candidates"
        / (candidate_date or unpublished_dates[-1])
        / safe_manifest_id
    )
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_path, quarantine_dir / "reconciliation.json")

    for source_date in unpublished_dates:
        partition = partitions[source_date]
        shutil.copy2(partition, quarantine_dir / f"options-{source_date}.parquet")

        ingestion_path = (
            data_dir / "control" / "ingestion" / "options" / f"{source_date}.json"
        )
        if ingestion_path.exists():
            shutil.copy2(
                ingestion_path,
                quarantine_dir / f"ingestion-{source_date}.json",
            )
            ingestion_path.unlink()

        partition.unlink()
        _remove_empty_partition_dirs(partition)

    return quarantine_dir, unpublished_dates


def finalize_snapshot(
    *,
    manifest_path: Path,
    data_dir: Path,
    sync_outcome: str = "success",
    github_output: Path | None = None,
) -> FinalizationResult:
    manifest = _read_json(manifest_path)
    if not manifest:
        raise RuntimeError(f"reconciliation manifest unavailable: {manifest_path}")

    critical_codes = _critical_codes(manifest)
    partitions = _option_partitions(data_dir)
    latest_local = max(partitions) if partitions else None
    candidate_date = _manifest_source_date(manifest) or latest_local
    published_date = _published_options_date(data_dir)
    decision_safe = bool((manifest.get("quality") or {}).get("decision_safe"))
    status_path = data_dir / "validation" / "options_snapshot_status.json"

    if decision_safe:
        active_date = candidate_date or latest_local
        result = FinalizationResult(
            state="accepted",
            can_score=True,
            active_source_date=active_date,
            candidate_source_date=candidate_date,
            critical_codes=critical_codes,
        )
        _atomic_json(
            status_path,
            _status_payload(
                manifest,
                state=result.state,
                sync_outcome=sync_outcome,
                active_date=result.active_source_date,
                candidate_date=result.candidate_source_date,
                critical_codes=critical_codes,
            ),
        )
        _write_github_outputs(github_output, result)
        print(f"Options candidate accepted: active source date {active_date}")
        return result

    unexpected = sorted(set(critical_codes) - SOFT_FALLBACK_CRITICAL_CODES)
    if unexpected:
        result = FinalizationResult(
            state="blocked",
            can_score=False,
            active_source_date=published_date,
            candidate_source_date=candidate_date,
            critical_codes=critical_codes,
        )
        _atomic_json(
            status_path,
            _status_payload(
                manifest,
                state=result.state,
                sync_outcome=sync_outcome,
                active_date=result.active_source_date,
                candidate_date=result.candidate_source_date,
                critical_codes=critical_codes,
            ),
        )
        _write_github_outputs(github_output, result)
        raise RuntimeError(
            "reconciliation contains non-options critical failures; refusing fallback: "
            + ", ".join(unexpected)
        )

    if published_date is None:
        raise RuntimeError(
            "options candidate failed reconciliation and no published data-release "
            "options snapshot exists for fallback"
        )
    if published_date not in partitions:
        raise RuntimeError(
            f"published options snapshot {published_date} is missing locally; refusing fallback"
        )

    quarantine_dir, quarantined_dates = _quarantine_unpublished_partitions(
        data_dir=data_dir,
        manifest_path=manifest_path,
        manifest=manifest,
        published_date=published_date,
        candidate_date=candidate_date,
    )

    remaining = _option_partitions(data_dir)
    if max(remaining, default=None) != published_date:
        raise RuntimeError(
            "options fallback did not restore the published snapshot as the newest local partition"
        )

    _update_sync_metadata(data_dir, published_date, candidate_date)
    result = FinalizationResult(
        state="fallback",
        can_score=False,
        active_source_date=published_date,
        candidate_source_date=candidate_date,
        critical_codes=critical_codes,
        quarantined_source_dates=quarantined_dates,
    )
    _atomic_json(
        status_path,
        _status_payload(
            manifest,
            state=result.state,
            sync_outcome=sync_outcome,
            active_date=published_date,
            candidate_date=candidate_date,
            critical_codes=critical_codes,
            quarantined_source_dates=quarantined_dates,
            quarantine_dir=quarantine_dir,
        ),
    )
    _write_github_outputs(github_output, result)
    print(
        "Options candidate rejected; retaining published fallback "
        f"{published_date}. Quarantined {len(quarantined_dates)} unpublished "
        "options partition(s). Scoring/publication of new options research is disabled."
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/validation/data_reconciliation.json"),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.getenv("DATA_DIR", "data")),
    )
    parser.add_argument(
        "--sync-outcome",
        default="success",
        help="GitHub step outcome for the latest options sync (success/failure).",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        default=None,
        help="Append options_state/can_score outputs for the calling Actions step.",
    )
    args = parser.parse_args()
    finalize_snapshot(
        manifest_path=args.manifest,
        data_dir=args.data_dir,
        sync_outcome=args.sync_outcome,
        github_output=args.github_output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
