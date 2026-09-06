"""Deterministic control-plane manifests for the local data pipeline."""

from __future__ import annotations

import hashlib
import json
from typing import Any


RECONCILIATION_SCHEMA = "quantiv.data-reconciliation.v2"


def _canonical_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _exception(
    code: str,
    severity: str,
    summary: str,
    *,
    count: int | None = None,
    sample: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "code": code,
            "severity": severity,
            "summary": summary,
            "count": count,
            "sample": sample,
        }.items()
        if value is not None
    }


def _rate_exceeds(payload: dict[str, Any], rate_key: str, limit_key: str) -> bool:
    rate = payload.get(rate_key)
    limit = payload.get(limit_key)
    if rate is None or limit is None:
        return False
    try:
        return float(rate) > float(limit)
    except (TypeError, ValueError):
        return False


def _scope_quote_quality(
    quote_quality: dict[str, Any] | None,
    event_coverage: dict[str, Any],
) -> dict[str, Any]:
    """Separate chain diagnostics from the earnings decisions we publish.

    Contract, same-strike-pair, and all-symbol/expiration rejection rates remain
    visible diagnostics over the immutable source chain. Publication-facing
    decision groups, however, are upcoming in-universe earnings events because
    that is the unit consumed by daily_score.py. This changes no numerical
    threshold and never hides a source/freshness failure.
    """
    quote = dict(quote_quality or {"status": "not_enforced"})
    raw_status = str(quote.get("status") or "not_enforced")

    chain_fields = (
        "decision_groups",
        "eligible_decision_groups",
        "rejected_decision_groups",
        "decision_group_rejection_rate",
        "eligible_symbol_expirations",
    )
    for field in chain_fields:
        if field in quote:
            quote[f"chain_{field}"] = quote[field]
    if "top_decision_group_rejection_reasons" in quote:
        quote["chain_top_decision_group_rejection_reasons"] = quote[
            "top_decision_group_rejection_reasons"
        ]

    quote.setdefault("contract_rejection_rate_scope", "all_contracts_diagnostic")
    quote.setdefault(
        "pair_rejection_rate_scope", "all_same_strike_pairs_diagnostic"
    )
    quote["chain_decision_group_rejection_rate_scope"] = (
        "all_symbol_expirations_diagnostic"
    )

    expected = max(int(event_coverage.get("expected_events") or 0), 0)
    covered = max(int(event_coverage.get("covered_events") or 0), 0)
    covered = min(covered, expected) if expected else 0
    reported_missing = max(int(event_coverage.get("missing_events") or 0), 0)
    missing = max(expected - covered, reported_missing)
    missing = min(missing, expected) if expected else 0
    decision_group_rate = missing / expected if expected else 0.0

    quote.update(
        {
            "decision_groups": expected,
            "eligible_decision_groups": covered,
            "rejected_decision_groups": missing,
            "decision_group_rejection_rate": round(decision_group_rate, 6),
            "decision_group_rejection_rate_scope": (
                "upcoming_in_universe_earnings_events"
            ),
            # Preserve the historical field for compatibility; its scope is
            # explicitly chain-wide and duplicated under chain_* above.
            "eligible_symbol_expirations": quote.get(
                "chain_eligible_symbol_expirations"
            ),
            "top_decision_group_rejection_reasons": list(
                event_coverage.get("missing_reason_counts") or []
            ),
            "raw_chain_status": raw_status,
        }
    )

    diagnostics_exceeded = any(
        (
            _rate_exceeds(
                quote,
                "contract_rejection_rate",
                "max_contract_rejection_rate",
            ),
            _rate_exceeds(
                quote,
                "pair_rejection_rate",
                "max_pair_rejection_rate",
            ),
            _rate_exceeds(
                quote,
                "chain_decision_group_rejection_rate",
                "max_decision_group_rejection_rate",
            ),
        )
    )
    quote["diagnostic_status"] = "degraded" if diagnostics_exceeded else "passed"

    errors = [item for item in (quote.get("errors") or []) if item]
    empty_required_source = (
        ("contracts" in quote and int(quote.get("contracts") or 0) == 0)
        or (
            "same_strike_pairs" in quote
            and int(quote.get("same_strike_pairs") or 0) == 0
        )
    )
    scoped_decision_failure = _rate_exceeds(
        quote,
        "decision_group_rejection_rate",
        "max_decision_group_rejection_rate",
    )

    if errors or empty_required_source or scoped_decision_failure:
        quote["status"] = "failed"
    elif raw_status == "failed" and diagnostics_exceeded:
        # A chain-wide diagnostic alone cannot veto an otherwise supported
        # earnings decision surface. Keep the failure visible as degradation.
        quote["status"] = "degraded"
    elif raw_status == "failed":
        # Unknown/unclassified quote failures remain fail-closed.
        quote["status"] = "failed"
    elif raw_status in {"passed", "degraded"}:
        quote["status"] = "degraded" if diagnostics_exceeded else raw_status
    else:
        quote["status"] = raw_status

    return quote


def build_reconciliation_manifest(
    *,
    generated_at: str,
    datasets: dict[str, Any],
    event_coverage: dict[str, Any],
    duplicates: dict[str, Any],
    symbol_mappings: dict[str, Any],
    corporate_actions: dict[str, Any],
    pipeline_controls: dict[str, Any],
    quote_quality: dict[str, Any] | None = None,
    source_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one exception-first manifest from already-computed local controls."""
    exceptions: list[dict[str, Any]] = []

    for name, dataset in datasets.items():
        if dataset.get("status") != "passed":
            exceptions.append(
                _exception(
                    f"{name}_unavailable",
                    "critical",
                    f"{name} is missing, empty, or could not be inspected",
                )
            )
        elif int(dataset.get("lag_days", 0)) > int(dataset.get("max_lag_days", 0)):
            exceptions.append(
                _exception(
                    f"{name}_stale",
                    "critical",
                    f"{name} exceeds its freshness limit",
                    count=int(dataset["lag_days"]),
                )
            )

    for name, result in duplicates.items():
        duplicate_rows = int(result.get("duplicate_rows", 0))
        if duplicate_rows:
            exceptions.append(
                _exception(
                    f"{name}_duplicate_keys",
                    "critical",
                    f"{name} contains duplicate serving keys",
                    count=duplicate_rows,
                )
            )

    outside_universe_events = int(
        event_coverage.get("outside_option_universe_events", 0)
    )
    if outside_universe_events:
        exceptions.append(
            _exception(
                "upcoming_events_outside_option_universe",
                "warning",
                "Calendar events fall outside the current options decision universe",
                count=outside_universe_events,
                sample=event_coverage.get("outside_option_universe_sample") or [],
            )
        )

    missing_events = int(event_coverage.get("missing_events", 0))
    if missing_events:
        exceptions.append(
            _exception(
                "upcoming_events_without_option_chain",
                "warning",
                "In-universe earnings events have no decision-eligible option chain",
                count=missing_events,
                sample=event_coverage.get("missing_sample") or [],
            )
        )

    if event_coverage.get("status") == "failed":
        exceptions.append(
            _exception(
                "event_quote_coverage_below_limit",
                "critical",
                "Decision-eligible option coverage is below the publication limit",
                count=missing_events,
                sample=event_coverage.get("missing_sample") or [],
            )
        )
    horizon_coverage = event_coverage.get("horizon_coverage") or {}
    if horizon_coverage.get("status") == "failed":
        exceptions.append(
            _exception(
                "forecast_horizon_coverage_below_limit",
                "warning",
                "One or more model horizons lack sufficient eligible option coverage",
                count=int(horizon_coverage.get("missing_events", 0)),
                sample=horizon_coverage.get("failed_horizons") or [],
            )
        )

    quote_quality = _scope_quote_quality(quote_quality, event_coverage)
    quote_status = quote_quality.get("status")
    if quote_status not in {"passed", "degraded"}:
        exceptions.append(
            _exception(
                "option_quote_quality_below_limit",
                "critical",
                "Option evidence on the scoped earnings decision surface exceeds the publication limit",
                count=int(
                    quote_quality.get(
                        "rejected_decision_groups",
                        quote_quality.get("rejected_contracts", 0),
                    )
                ),
                sample=(
                    quote_quality.get("top_decision_group_rejection_reasons")
                    or quote_quality.get("top_rejection_reasons")
                    or []
                ),
            )
        )
    elif quote_status == "degraded":
        exceptions.append(
            _exception(
                "option_chain_diagnostics_above_limit",
                "warning",
                "Chain-wide option diagnostics exceed their limits outside the scoped earnings decision surface",
                count=int(
                    quote_quality.get(
                        "chain_rejected_decision_groups",
                        quote_quality.get("rejected_contracts", 0),
                    )
                ),
                sample=(
                    quote_quality.get("chain_top_decision_group_rejection_reasons")
                    or quote_quality.get("top_rejection_reasons")
                    or []
                ),
            )
        )

    source_reconciliation = source_reconciliation or {"status": "not_enforced"}
    if source_reconciliation.get("status") != "passed":
        exceptions.append(
            _exception(
                "source_partition_reconciliation_failed",
                "critical",
                "The newest source partition is missing expected/received, hash, or replay proof",
                sample=source_reconciliation.get("errors") or [],
            )
        )

    stale_symbols = symbol_mappings.get("stale_source_symbols") or []
    if stale_symbols:
        exceptions.append(
            _exception(
                "stale_symbol_mappings",
                "critical",
                "Retired or renamed-away symbols remain in active pipeline views",
                count=len(stale_symbols),
                sample=stale_symbols,
            )
        )
    quarantined_symbols = symbol_mappings.get("quarantined_latest_option_symbols") or []
    if quarantined_symbols:
        exceptions.append(
            _exception(
                "retired_source_symbols_quarantined",
                "warning",
                "Latest source quotes for retired symbols are excluded from the decision universe",
                count=len(quarantined_symbols),
                sample=quarantined_symbols,
            )
        )

    if corporate_actions.get("continuity_status") != "enforced":
        exceptions.append(
            _exception(
                "corporate_action_continuity_failed",
                "critical",
                "Split/dividend continuity is missing, stale, or does not match the active options universe",
                sample=corporate_actions.get("errors") or [],
            )
        )

    quarantine = pipeline_controls.get("quarantine") or {}
    if quarantine.get("status") != "enforced":
        exceptions.append(
            _exception(
                "record_quarantine_not_instrumented",
                "critical",
                "Rejected records are not retained in the required quarantine ledger",
            )
        )

    replay = pipeline_controls.get("idempotent_replay") or {}
    if replay.get("status") != "verified":
        exceptions.append(
            _exception(
                "idempotent_replay_not_verified",
                "warning",
                "Replay-safe keys exist, but an automated replay equivalence check is not yet recorded",
            )
        )

    critical_count = sum(item["severity"] == "critical" for item in exceptions)
    warning_count = sum(item["severity"] == "warning" for item in exceptions)
    core = {
        "schema": RECONCILIATION_SCHEMA,
        "quality": {
            "status": (
                "failed"
                if critical_count
                else "degraded"
                if warning_count
                else "passed"
            ),
            "decision_safe": critical_count == 0,
            "critical_exceptions": critical_count,
            "warnings": warning_count,
        },
        "datasets": datasets,
        "event_coverage": event_coverage,
        "quote_quality": quote_quality,
        "source_reconciliation": source_reconciliation,
        "duplicates": duplicates,
        "symbol_mappings": symbol_mappings,
        "corporate_actions": corporate_actions,
        "pipeline_controls": pipeline_controls,
        "exceptions": exceptions,
    }
    return {
        "manifest_id": _canonical_id(core),
        "generated_at": generated_at,
        **core,
    }
