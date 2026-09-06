from __future__ import annotations

from ml.data_reconciliation import build_reconciliation_manifest


def test_september_4_candidate_keeps_real_coverage_hold_after_scope_correction() -> None:
    manifest = build_reconciliation_manifest(
        generated_at="2026-09-06T14:43:21+00:00",
        datasets={
            "options": {
                "status": "passed",
                "rows": 113742,
                "lag_days": 2,
                "max_lag_days": 5,
            }
        },
        event_coverage={
            "status": "failed",
            "expected_events": 38,
            "covered_events": 25,
            "missing_events": 13,
            "coverage_pct": 0.657895,
            "minimum_coverage_pct": 0.7,
            "missing_reason_counts": [
                {"reason": "noncommercial_leg_quotes", "events": 13}
            ],
            "missing_sample": [
                {"symbol": "ABM", "earnings_date": "2026-09-08"},
                {"symbol": "ASO", "earnings_date": "2026-09-09"},
            ],
        },
        duplicates={"options": {"duplicate_rows": 0, "affected_dates": 0}},
        symbol_mappings={
            "rename_rules": 8,
            "retired_symbols": 0,
            "stale_source_symbols": [],
        },
        corporate_actions={"continuity_status": "enforced", "errors": []},
        pipeline_controls={
            "quarantine": {
                "status": "enforced",
                "mode": "compact_parquet_ledger",
                "records": 102270,
            },
            "idempotent_replay": {"status": "verified"},
        },
        quote_quality={
            "status": "failed",
            "source_date": "2026-09-04",
            "expected_source_date": "2026-09-04",
            "source_session_lag": 0,
            "contracts": 113742,
            "eligible_contracts": 62114,
            "rejected_contracts": 51628,
            "contract_rejection_rate": 0.453904,
            "max_contract_rejection_rate": 0.65,
            "same_strike_pairs": 56871,
            "eligible_pairs": 6229,
            "rejected_pairs": 50642,
            "pair_rejection_rate": 0.890471,
            "max_pair_rejection_rate": 0.9,
            "decision_groups": 3739,
            "eligible_decision_groups": 1703,
            "rejected_decision_groups": 2036,
            "decision_group_rejection_rate": 0.544531,
            "max_decision_group_rejection_rate": 0.5,
            "eligible_symbol_expirations": 1703,
            "top_decision_group_rejection_reasons": [
                {"reason": "call_excessive_leg_spread", "groups": 1620},
                {"reason": "put_excessive_leg_spread", "groups": 220},
                {"reason": "call_zero_or_noncommercial_side", "groups": 175},
                {"reason": "call_dte_out_of_policy", "groups": 2},
            ],
            "errors": [],
        },
        source_reconciliation={
            "status": "passed",
            "source_date": "2026-09-04",
            "expected_rows": 113742,
            "received_rows": 113742,
            "duckdb_rows": 113742,
            "replay_equivalence": "verified",
        },
    )

    quote = manifest["quote_quality"]
    assert quote["chain_decision_groups"] == 3739
    assert quote["chain_rejected_decision_groups"] == 2036
    assert quote["chain_decision_group_rejection_rate"] == 0.544531
    assert quote["diagnostic_status"] == "degraded"

    assert quote["decision_groups"] == 38
    assert quote["eligible_decision_groups"] == 25
    assert quote["rejected_decision_groups"] == 13
    assert quote["decision_group_rejection_rate"] == 0.342105
    assert quote["status"] == "degraded"

    assert manifest["quality"]["status"] == "failed"
    assert manifest["quality"]["decision_safe"] is False
    assert manifest["quality"]["critical_exceptions"] == 1
    severities = {issue["code"]: issue["severity"] for issue in manifest["exceptions"]}
    assert severities["event_quote_coverage_below_limit"] == "critical"
    assert severities["option_chain_diagnostics_above_limit"] == "warning"
    assert "option_quote_quality_below_limit" not in severities
