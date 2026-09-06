from __future__ import annotations

from ml.data_reconciliation import build_reconciliation_manifest


def _manifest(
    generated_at: str,
    *,
    duplicate_rows: int = 0,
    corporate_actions_enforced: bool = True,
) -> dict:
    return build_reconciliation_manifest(
        generated_at=generated_at,
        datasets={
            "options": {
                "status": "passed",
                "rows": 100,
                "lag_days": 1,
                "max_lag_days": 5,
            }
        },
        event_coverage={
            "expected_events": 10,
            "covered_events": 8,
            "missing_events": 2,
            "missing_sample": [{"symbol": "MISS", "earnings_date": "2026-08-25"}],
        },
        duplicates={"options": {"duplicate_rows": duplicate_rows, "affected_dates": 0}},
        symbol_mappings={
            "rename_rules": 7,
            "retired_symbols": 20,
            "stale_source_symbols": [],
        },
        corporate_actions={
            "rows": 12,
            "symbols": 4,
            "continuity_status": (
                "enforced" if corporate_actions_enforced else "not_enforced"
            ),
            "errors": [] if corporate_actions_enforced else ["receipt missing"],
        },
        pipeline_controls={
            "quarantine": {"status": "enforced", "mode": "compact_parquet_ledger"},
            "idempotent_replay": {"status": "verified"},
        },
        quote_quality={"status": "passed", "rejected_contracts": 5},
        source_reconciliation={"status": "passed"},
    )


def _rebuild(
    base: dict,
    *,
    event_coverage: dict | None = None,
    quote_quality: dict | None = None,
) -> dict:
    return build_reconciliation_manifest(
        generated_at="2026-08-22T12:00:00+00:00",
        datasets=base["datasets"],
        event_coverage=event_coverage or base["event_coverage"],
        duplicates=base["duplicates"],
        symbol_mappings=base["symbol_mappings"],
        corporate_actions=base["corporate_actions"],
        pipeline_controls=base["pipeline_controls"],
        quote_quality=quote_quality or base["quote_quality"],
        source_reconciliation=base["source_reconciliation"],
    )


def _chain_quote_quality(*, errors: list[str] | None = None) -> dict:
    return {
        "status": "failed",
        "contracts": 1000,
        "eligible_contracts": 600,
        "rejected_contracts": 400,
        "contract_rejection_rate": 0.4,
        "max_contract_rejection_rate": 0.65,
        "same_strike_pairs": 500,
        "eligible_pairs": 100,
        "rejected_pairs": 400,
        "pair_rejection_rate": 0.8,
        "max_pair_rejection_rate": 0.9,
        "decision_groups": 100,
        "eligible_decision_groups": 40,
        "rejected_decision_groups": 60,
        "decision_group_rejection_rate": 0.6,
        "max_decision_group_rejection_rate": 0.5,
        "eligible_symbol_expirations": 40,
        "top_decision_group_rejection_reasons": [
            {"reason": "call_excessive_leg_spread", "groups": 55}
        ],
        "errors": errors or [],
    }


def test_manifest_is_reproducible_and_surfaces_coverage_gaps() -> None:
    first = _manifest("2026-08-22T12:00:00+00:00")
    second = _manifest("2026-08-22T13:00:00+00:00")

    assert first["manifest_id"] == second["manifest_id"]
    assert first["quality"] == {
        "status": "degraded",
        "decision_safe": True,
        "critical_exceptions": 0,
        "warnings": 1,
    }
    assert {issue["code"] for issue in first["exceptions"]} == {
        "upcoming_events_without_option_chain",
    }


def test_sparse_horizon_coverage_is_advisory_when_aggregate_coverage_passes() -> None:
    base = _manifest("2026-08-22T12:00:00+00:00")
    event_coverage = {
        **base["event_coverage"],
        "status": "passed",
        "horizon_coverage": {
            "status": "failed",
            "missing_events": 2,
            "failed_horizons": [
                {"horizon": 3, "coverage_pct": 0.666667},
                {"horizon": 14, "coverage_pct": 0.0},
            ],
        },
    }
    manifest = _rebuild(base, event_coverage=event_coverage)

    horizon_issue = next(
        issue
        for issue in manifest["exceptions"]
        if issue["code"] == "forecast_horizon_coverage_below_limit"
    )
    assert horizon_issue["severity"] == "warning"
    assert manifest["quality"]["status"] == "degraded"
    assert manifest["quality"]["decision_safe"] is True
    assert manifest["quality"]["critical_exceptions"] == 0


def test_chain_wide_quote_failure_is_diagnostic_when_event_surface_passes() -> None:
    base = _manifest("2026-08-22T12:00:00+00:00")
    event_coverage = {
        **base["event_coverage"],
        "status": "passed",
        "missing_reason_counts": [
            {"reason": "noncommercial_leg_quotes", "events": 2}
        ],
    }
    manifest = _rebuild(
        base,
        event_coverage=event_coverage,
        quote_quality=_chain_quote_quality(),
    )
    quote = manifest["quote_quality"]

    assert quote["chain_decision_groups"] == 100
    assert quote["chain_rejected_decision_groups"] == 60
    assert quote["chain_decision_group_rejection_rate"] == 0.6
    assert quote["decision_groups"] == 10
    assert quote["eligible_decision_groups"] == 8
    assert quote["rejected_decision_groups"] == 2
    assert quote["decision_group_rejection_rate"] == 0.2
    assert quote["decision_group_rejection_rate_scope"] == (
        "upcoming_in_universe_earnings_events"
    )
    assert quote["status"] == "degraded"
    assert manifest["quality"]["decision_safe"] is True
    assert "option_quote_quality_below_limit" not in {
        issue["code"] for issue in manifest["exceptions"]
    }
    assert "option_chain_diagnostics_above_limit" in {
        issue["code"] for issue in manifest["exceptions"]
    }


def test_source_quote_error_remains_fail_closed_despite_supported_events() -> None:
    base = _manifest("2026-08-22T12:00:00+00:00")
    event_coverage = {**base["event_coverage"], "status": "passed"}
    manifest = _rebuild(
        base,
        event_coverage=event_coverage,
        quote_quality=_chain_quote_quality(
            errors=["latest source date is stale relative to the completed session"]
        ),
    )

    assert manifest["quote_quality"]["status"] == "failed"
    assert manifest["quality"]["decision_safe"] is False
    assert "option_quote_quality_below_limit" in {
        issue["code"] for issue in manifest["exceptions"]
    }


def test_scoped_decision_group_failure_remains_fail_closed() -> None:
    base = _manifest("2026-08-22T12:00:00+00:00")
    event_coverage = {
        **base["event_coverage"],
        "status": "failed",
        "covered_events": 4,
        "missing_events": 6,
        "missing_reason_counts": [
            {"reason": "noncommercial_leg_quotes", "events": 6}
        ],
    }
    quote_quality = _chain_quote_quality()
    quote_quality.update(
        {
            "status": "passed",
            "decision_group_rejection_rate": 0.4,
            "eligible_decision_groups": 60,
            "rejected_decision_groups": 40,
        }
    )
    manifest = _rebuild(
        base,
        event_coverage=event_coverage,
        quote_quality=quote_quality,
    )

    assert manifest["quote_quality"]["decision_group_rejection_rate"] == 0.6
    assert manifest["quote_quality"]["status"] == "failed"
    assert manifest["quality"]["decision_safe"] is False
    codes = {issue["code"] for issue in manifest["exceptions"]}
    assert "event_quote_coverage_below_limit" in codes
    assert "option_quote_quality_below_limit" in codes


def test_duplicate_serving_keys_make_manifest_fail_closed() -> None:
    clean = _manifest("2026-08-22T12:00:00+00:00")
    failed = _manifest("2026-08-22T12:00:00+00:00", duplicate_rows=3)

    assert failed["manifest_id"] != clean["manifest_id"]
    assert failed["quality"]["status"] == "failed"
    assert failed["quality"]["decision_safe"] is False
    assert failed["quality"]["critical_exceptions"] == 1
    assert "options_duplicate_keys" in {issue["code"] for issue in failed["exceptions"]}


def test_quote_quality_failure_makes_manifest_fail_closed() -> None:
    manifest = _manifest("2026-08-22T12:00:00+00:00")
    manifest["quote_quality"]["status"] = "failed"
    failed = _rebuild(manifest)
    assert failed["quality"]["decision_safe"] is False
    assert failed["quality"]["status"] == "failed"
    assert "option_quote_quality_below_limit" in {
        issue["code"] for issue in failed["exceptions"]
    }


def test_missing_corporate_action_receipt_makes_manifest_fail_closed() -> None:
    failed = _manifest(
        "2026-08-22T12:00:00+00:00", corporate_actions_enforced=False
    )

    assert failed["quality"]["decision_safe"] is False
    assert "corporate_action_continuity_failed" in {
        issue["code"] for issue in failed["exceptions"]
    }
