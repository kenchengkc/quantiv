from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from ml.provider_signal_policy import (
    ProviderSignalPolicyError,
    blocked_model_features,
    load_provider_signal_policy,
    permitted_endpoint_ids,
    permitted_signals,
)


def test_committed_policy_covers_every_enrichment_endpoint() -> None:
    policy = load_provider_signal_policy()
    repo_root = Path(__file__).resolve().parents[3]
    source = ast.parse((repo_root / "scripts" / "sync_provider_enrichments.py").read_text())
    work_order = next(
        ast.literal_eval(node.value)
        for node in source.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "WORK_ORDER"
            for target in node.targets
        )
    )
    configured = {
        endpoint
        for entry in policy["signals"].values()
        for endpoint in entry["endpoint_ids"]
    }
    assert configured == set(work_order)
    assert permitted_endpoint_ids(policy) == set()
    assert permitted_signals(policy, "allow_publication") == set()
    assert permitted_signals(policy, "allow_ml") == set()


def test_unapproved_provider_feature_is_blocked() -> None:
    policy = load_provider_signal_policy()
    assert blocked_model_features(
        ["atm_iv", "put_call_volume_ratio", "short_days_to_cover"], policy
    ) == {
        "put_call_volume_ratio": "options_flow",
        "short_days_to_cover": "short_interest",
    }


def test_enabled_signal_requires_pinned_passing_evidence(tmp_path) -> None:
    policy = copy.deepcopy(load_provider_signal_policy())
    policy["signals"]["options_flow"]["allow_ml"] = True

    with pytest.raises(ProviderSignalPolicyError, match="no pinned paired-test evidence"):
        permitted_signals(policy, "allow_ml", repo_root=tmp_path)

    split_audit = [
        {
            "fold": str(fold),
            "train_end": f"2025-0{fold + 1}-01",
            "test_start": f"2025-0{fold + 1}-06",
            "test_end": f"2025-0{fold + 2}-05",
            "purge_days": 5,
            "events": 100,
            "rows": 100,
        }
        for fold in range(5)
    ]
    split_digest = hashlib.sha256(
        json.dumps(split_audit, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    report = {
        "schema": "quantiv.provider-paired-test.v1",
        "signal": "options_flow",
        "status": "passed",
        "source_sha256": "sha256:" + "c" * 64,
        "paired_keys_sha256": "sha256:" + "a" * 64,
        "split_audit_sha256": "sha256:" + split_digest,
        "split_audit": split_audit,
        "sample": {"events": 500, "walk_forward_folds": 5},
        "control": {"mae": 0.0500, "straddle_relative_mae": 0.94},
        "candidate": {"mae": 0.0480, "straddle_relative_mae": 0.91},
        "paired_error_delta": {"mean": -0.002, "t_stat": -3.0},
        "worst_slice_mae_regression_pct": 2.0,
        "incremental_monthly_cost_usd": 0.0,
    }
    evidence_path = tmp_path / "paired.json"
    evidence_path.write_text(json.dumps(report, sort_keys=True))
    digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    policy["signals"]["options_flow"]["evidence"] = {
        "path": "paired.json",
        "sha256": f"sha256:{digest}",
    }

    assert permitted_signals(policy, "allow_ml", repo_root=tmp_path) == {
        "options_flow"
    }
    assert blocked_model_features(
        ["put_call_volume_ratio"], policy, repo_root=tmp_path
    ) == {}
