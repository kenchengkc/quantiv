"""Fail-closed policy for experimental vendor signals.

Existing research data may be collected manually, but scheduled collection,
dashboard publication, and ML use remain blocked until a pinned paired-test
report proves incremental value without adding recurring infrastructure cost.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY_PATH = REPO_ROOT / "config" / "provider_signal_policy.json"
POLICY_SCHEMA = "quantiv.provider-signal-policy.v1"
EVIDENCE_SCHEMA = "quantiv.provider-paired-test.v1"


class ProviderSignalPolicyError(ValueError):
    """Raised when the policy or its pinned evidence is invalid."""


def _object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ProviderSignalPolicyError(f"{path} must contain a JSON object")
    return payload


def load_provider_signal_policy(path: Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    policy = _object(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise ProviderSignalPolicyError(f"{path} has an unsupported policy schema")
    if policy.get("default") != "blocked":
        raise ProviderSignalPolicyError("provider policy must fail closed by default")
    if not isinstance(policy.get("signals"), dict) or not policy["signals"]:
        raise ProviderSignalPolicyError("provider policy must define signals")
    return policy


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def validate_paired_evidence(
    signal: str,
    entry: dict[str, Any],
    requirements: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
) -> list[str]:
    evidence_ref = entry.get("evidence")
    if not isinstance(evidence_ref, dict):
        return ["no pinned paired-test evidence"]
    relative_path = evidence_ref.get("path")
    expected_hash = str(evidence_ref.get("sha256") or "").removeprefix("sha256:")
    if not isinstance(relative_path, str) or not relative_path:
        return ["evidence path is missing"]
    evidence_path = (repo_root / relative_path).resolve()
    try:
        evidence_path.relative_to(repo_root.resolve())
    except ValueError:
        return ["evidence path escapes the repository"]
    if not evidence_path.is_file():
        return [f"evidence file does not exist: {relative_path}"]
    if len(expected_hash) != 64 or _sha256(evidence_path) != expected_hash:
        return ["evidence SHA-256 does not match the pinned digest"]

    try:
        report = _object(evidence_path)
    except (OSError, json.JSONDecodeError, ProviderSignalPolicyError) as exc:
        return [f"evidence is unreadable: {exc}"]
    return validate_paired_report(signal, report, requirements)


def validate_paired_report(
    signal: str,
    report: dict[str, Any],
    requirements: dict[str, Any],
) -> list[str]:
    """Validate report metrics independently of repository pinning."""
    errors: list[str] = []
    if report.get("schema") != EVIDENCE_SCHEMA:
        errors.append("unsupported paired-test evidence schema")
    if report.get("signal") != signal:
        errors.append("paired-test report names a different signal")
    if report.get("status") != "passed":
        errors.append("paired-test status is not passed")
    for receipt_field in ("source_sha256", "paired_keys_sha256", "split_audit_sha256"):
        receipt = str(report.get(receipt_field) or "")
        if not receipt.startswith("sha256:") or len(receipt.removeprefix("sha256:")) != 64:
            errors.append(f"{receipt_field} is not a SHA-256 receipt")

    sample = report.get("sample") if isinstance(report.get("sample"), dict) else {}
    events = sample.get("events")
    folds = sample.get("walk_forward_folds")
    if not _finite_number(events) or events < requirements.get("minimum_events", 250):
        errors.append("paired test has insufficient events")
    if not _finite_number(folds) or folds < requirements.get("minimum_walk_forward_folds", 3):
        errors.append("paired test has insufficient walk-forward folds")
    split_audit = report.get("split_audit")
    if not isinstance(split_audit, list) or not split_audit:
        errors.append("paired test is missing its chronological split audit")
    elif report.get("split_audit_sha256") != _canonical_sha256(split_audit):
        errors.append("paired test split audit does not match its receipt")
    elif _finite_number(folds) and len(split_audit) != int(folds):
        errors.append("paired test split audit fold count does not match its sample")

    control = report.get("control") if isinstance(report.get("control"), dict) else {}
    candidate = report.get("candidate") if isinstance(report.get("candidate"), dict) else {}
    control_mae = control.get("mae")
    candidate_mae = candidate.get("mae")
    if not _finite_number(control_mae) or not _finite_number(candidate_mae) or control_mae <= 0:
        errors.append("paired test must include positive control and candidate MAE")
    else:
        improvement = (control_mae - candidate_mae) / control_mae * 100
        if improvement < requirements.get("minimum_mae_improvement_pct", 0.5):
            errors.append("candidate MAE improvement is below policy minimum")
    paired_delta = (
        report.get("paired_error_delta")
        if isinstance(report.get("paired_error_delta"), dict)
        else {}
    )
    paired_t = paired_delta.get("t_stat")
    minimum_t = abs(float(requirements.get("minimum_paired_t_stat_abs", 2.0)))
    if not _finite_number(paired_t) or float(paired_t) > -minimum_t:
        errors.append("candidate paired error improvement is not statistically significant")
    control_relative = control.get("straddle_relative_mae")
    candidate_relative = candidate.get("straddle_relative_mae")
    if (
        not _finite_number(control_relative)
        or not _finite_number(candidate_relative)
        or candidate_relative > control_relative
        or candidate_relative > requirements.get("maximum_straddle_relative_mae", 1.0)
    ):
        errors.append("candidate does not preserve or improve the straddle baseline result")

    worst_slice = report.get("worst_slice_mae_regression_pct")
    if not _finite_number(worst_slice) or worst_slice > requirements.get(
        "maximum_worst_slice_regression_pct", 5.0
    ):
        errors.append("candidate exceeds the allowed worst-slice MAE regression")
    monthly_cost = report.get("incremental_monthly_cost_usd")
    if not _finite_number(monthly_cost) or monthly_cost > requirements.get(
        "maximum_incremental_monthly_cost_usd", 0.0
    ):
        errors.append("candidate exceeds the allowed incremental monthly cost")
    return errors


def validate_policy(policy: dict[str, Any], *, repo_root: Path = REPO_ROOT) -> list[str]:
    errors: list[str] = []
    requirements = policy.get("requirements") or {}
    endpoint_owners: dict[str, str] = {}
    for signal, raw_entry in policy["signals"].items():
        if not isinstance(raw_entry, dict):
            errors.append(f"{signal}: policy entry must be an object")
            continue
        for endpoint in raw_entry.get("endpoint_ids") or []:
            if endpoint in endpoint_owners:
                errors.append(
                    f"{signal}: endpoint {endpoint} already belongs to {endpoint_owners[endpoint]}"
                )
            endpoint_owners[str(endpoint)] = signal
        enabled = any(
            raw_entry.get(capability) is True
            for capability in ("allow_collection", "allow_publication", "allow_ml")
        )
        if enabled:
            errors.extend(
                f"{signal}: {message}"
                for message in validate_paired_evidence(
                    signal, raw_entry, requirements, repo_root=repo_root
                )
            )
    return errors


def permitted_signals(
    policy: dict[str, Any], capability: str, *, repo_root: Path = REPO_ROOT
) -> set[str]:
    if capability not in {"allow_collection", "allow_publication", "allow_ml"}:
        raise ProviderSignalPolicyError(f"unsupported capability: {capability}")
    policy_errors = validate_policy(policy, repo_root=repo_root)
    if policy_errors:
        raise ProviderSignalPolicyError("; ".join(policy_errors))
    return {
        signal
        for signal, entry in policy["signals"].items()
        if isinstance(entry, dict) and entry.get(capability) is True
    }


def permitted_endpoint_ids(policy: dict[str, Any], *, repo_root: Path = REPO_ROOT) -> set[str]:
    allowed = permitted_signals(policy, "allow_collection", repo_root=repo_root)
    return {
        str(endpoint)
        for signal in allowed
        for endpoint in policy["signals"][signal].get("endpoint_ids") or []
    }


def blocked_model_features(
    feature_columns: Iterable[str],
    policy: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, str]:
    allowed = permitted_signals(policy, "allow_ml", repo_root=repo_root)
    blocked: dict[str, str] = {}
    for signal, entry in policy["signals"].items():
        if signal in allowed or not isinstance(entry, dict):
            continue
        prefixes = tuple(str(prefix) for prefix in entry.get("feature_prefixes") or [])
        for feature in feature_columns:
            if prefixes and str(feature).startswith(prefixes):
                blocked[str(feature)] = signal
    return blocked


__all__ = [
    "DEFAULT_POLICY_PATH",
    "ProviderSignalPolicyError",
    "blocked_model_features",
    "load_provider_signal_policy",
    "permitted_endpoint_ids",
    "permitted_signals",
    "validate_paired_evidence",
    "validate_paired_report",
    "validate_policy",
]
