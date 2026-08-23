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
                "critical",
                "One or more model horizons lack sufficient eligible option coverage",
                count=int(horizon_coverage.get("missing_events", 0)),
                sample=horizon_coverage.get("failed_horizons") or [],
            )
        )

    quote_quality = quote_quality or {"status": "not_enforced"}
    if quote_quality.get("status") != "passed":
        exceptions.append(
            _exception(
                "option_quote_quality_below_limit",
                "critical",
                "Option quote or same-strike pair rejection exceeds the publication limit",
                count=int(quote_quality.get("rejected_contracts", 0)),
                sample=quote_quality.get("top_rejection_reasons") or [],
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
