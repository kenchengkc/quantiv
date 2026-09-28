#!/usr/bin/env python3
"""Verify that a provider-free recovery is pinned to a publishable R2 data release."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from data_release import verify_release


class RecoveryVerificationError(RuntimeError):
    pass


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RecoveryVerificationError(f"required recovery evidence is missing: {path}")
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RecoveryVerificationError(f"invalid JSON recovery evidence: {path}") from exc
    if not isinstance(payload, dict):
        raise RecoveryVerificationError(f"recovery evidence must be an object: {path}")
    return payload


def verify_recovery(
    data_dir: Path,
    *,
    expected_release_id: str,
) -> dict[str, Any]:
    if not expected_release_id:
        raise RecoveryVerificationError(
            "provider-free recovery requires the exact promoted data release id"
        )

    verified = verify_release(data_dir)
    actual_release_id = str(verified["release_id"])
    if actual_release_id != expected_release_id:
        raise RecoveryVerificationError(
            f"recovery release mismatch: expected {expected_release_id}, "
            f"materialized {actual_release_id}"
        )

    pointer = _read_object(data_dir / "control" / "current_data_release.json")
    try:
        promoted_at = datetime.fromisoformat(str(pointer["promoted_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise RecoveryVerificationError("data-release pointer has invalid promoted_at") from exc
    if promoted_at.tzinfo is None or promoted_at.utcoffset() is None:
        raise RecoveryVerificationError("data-release promoted_at must be timezone-aware")

    reconciliation = _read_object(data_dir / "validation" / "data_reconciliation.json")
    options_status = _read_object(data_dir / "validation" / "options_snapshot_status.json")

    if options_status.get("schema") != "quantiv.options-snapshot-status.v1":
        raise RecoveryVerificationError("unsupported options snapshot status schema")
    state = options_status.get("state")
    if state not in {"accepted", "fallback"}:
        raise RecoveryVerificationError(
            f"options snapshot is not recoverable from saved data: state={state!r}"
        )

    policy = options_status.get("policy")
    if not isinstance(policy, dict) or policy.get("refresh_scoring_allowed") is not True:
        raise RecoveryVerificationError(
            "saved options snapshot does not permit independent scoring/publication"
        )
    if not options_status.get("active_source_date"):
        raise RecoveryVerificationError("saved options snapshot has no active source date")

    quality = reconciliation.get("quality")
    exceptions = reconciliation.get("exceptions")
    if not isinstance(quality, dict) or not isinstance(exceptions, list):
        raise RecoveryVerificationError("reconciliation evidence is incomplete")
    critical = [
        item
        for item in exceptions
        if isinstance(item, dict) and item.get("severity") == "critical"
    ]
    if quality.get("critical_exceptions") != len(critical):
        raise RecoveryVerificationError("reconciliation critical count is inconsistent")
    if quality.get("decision_safe") != (len(critical) == 0):
        raise RecoveryVerificationError("reconciliation decision contradicts exceptions")

    if state == "accepted":
        if quality.get("decision_safe") is not True or critical:
            raise RecoveryVerificationError(
                "accepted recovery snapshot must have zero critical reconciliation exceptions"
            )
        if options_status.get("critical_codes"):
            raise RecoveryVerificationError(
                "accepted recovery snapshot unexpectedly records critical option codes"
            )
        if options_status.get("candidate_manifest_id") != reconciliation.get("manifest_id"):
            raise RecoveryVerificationError(
                "accepted options status does not reference the restored reconciliation manifest"
            )
    else:
        if policy.get("scoring_allowed") is not False:
            raise RecoveryVerificationError(
                "fallback recovery must keep strict options-candidate scoring disabled"
            )
        if policy.get("fallback_mode") != "last_published_snapshot":
            raise RecoveryVerificationError("fallback recovery mode is not verified")

    return {
        "release_id": actual_release_id,
        "release_files": int(verified["files"]),
        "release_bytes": int(verified["bytes"]),
        "promoted_at": promoted_at.isoformat(),
        "options_state": str(state),
        "active_source_date": str(options_status["active_source_date"]),
        "reconciliation_manifest_id": str(reconciliation.get("manifest_id") or ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--expected-release-id", required=True)
    args = parser.parse_args()

    if os.getenv("PROVIDER_FREE_RECOVERY") != "1":
        raise SystemExit(
            "verify_refresh_recovery.py may only run with PROVIDER_FREE_RECOVERY=1"
        )

    try:
        result = verify_recovery(
            args.data_dir,
            expected_release_id=args.expected_release_id,
        )
    except (RecoveryVerificationError, RuntimeError) as exc:
        raise SystemExit(f"Provider-free recovery refused: {exc}") from exc

    print(
        "Provider-free recovery verified: "
        f"release={result['release_id']} · options={result['options_state']} "
        f"({result['active_source_date']}) · manifest={result['reconciliation_manifest_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
