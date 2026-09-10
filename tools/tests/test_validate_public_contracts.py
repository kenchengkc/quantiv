from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.validate_public_contracts as contracts

EMPTY_ROWS_SHA256 = "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"


def test_committed_public_contracts_validate() -> None:
    # research-history.json is a materialized/generated artifact and is not
    # guaranteed to exist in a clean source checkout. Baseline CI validates the
    # committed corpus here; generated-history fixtures below exercise the
    # strict research-history gate directly.
    checks = [
        contracts.validate_schema_documents,
        contracts.validate_screener,
        contracts.validate_symbol_payloads,
        contracts.validate_dashboard_evidence,
        contracts.validate_control_plane,
        contracts.validate_model_validation,
    ]
    for check in checks:
        check()


def test_screener_contract_fails_closed_on_count_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    public = tmp_path / "public"
    public.mkdir()
    (public / "screener.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "version": "v1",
                    "as_of_date": "2026-09-01",
                    "generated_at": "2026-09-02T00:00:00Z",
                    "event_count": 2,
                },
                "events": [
                    {
                        "ticker": "AAPL",
                        "earnings_date": "2026-10-01",
                        "as_of_date": "2026-09-01",
                        "em_method": "ml_lightgbm",
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(contracts, "PUBLIC", public)

    with pytest.raises(contracts.ContractError, match="event_count"):
        contracts.validate_screener()


def test_model_validation_preserves_decision_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = tmp_path / "public" / "evidence"
    evidence.mkdir(parents=True)
    (evidence / "model-validation.json").write_text(
        json.dumps(
            {
                "schema": "quantiv.public-model-validation.v1",
                "generated_at": "2026-09-02T00:00:00Z",
                "model_source": {
                    "kind": "baked_fallback",
                    "bundle_id": None,
                    "artifact_sha256": None,
                },
                "summary": {
                    "supported_horizons": [1],
                    "validation_row_observations": 10,
                    "weighted_model_mae": 0.04,
                    "weighted_straddle_mae": 0.06,
                    "weighted_relative_mae_improvement": 0.33,
                    "weighted_coverage": {},
                },
                "horizons": [{"horizon_days": 1}],
                "validation_protocol": {
                    "decision_scope": "live_execution",
                    "live_trading_eligible": True,
                },
                "current_evidence": {},
            }
        )
    )
    monkeypatch.setattr(contracts, "PUBLIC", tmp_path / "public")

    with pytest.raises(contracts.ContractError, match="research-only"):
        contracts.validate_model_validation()


def test_preview_research_history_must_be_display_limited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public = tmp_path / "public"
    public.mkdir()
    (public / "research-history.json").write_text(
        json.dumps(
            {
                "schema": contracts.PREVIEW_UNIVERSE_SCHEMA,
                "source": {
                    "kind": "display_payload_fallback",
                    "completeness": "source_level",
                    "symbol_payloads": 1,
                    "as_of_min": "2026-09-01",
                    "as_of_max": "2026-09-01",
                },
                "evidence_rule": "preview",
                "decision_scope": "end_of_day_research",
                "live_trading_eligible": False,
                "event_count": 0,
                "events": [],
            }
        )
    )
    monkeypatch.setattr(contracts, "PUBLIC", public)

    with pytest.raises(contracts.ContractError, match="display_limited"):
        contracts.validate_research_history()


def _retired_membership() -> dict:
    empty = {
        "rows": [],
        "row_count": 0,
        "pages": 0,
        "sha256": EMPTY_ROWS_SHA256,
    }
    return {
        "status": "verified",
        "method": "bounded_provider_query_for_explicit_retirement_ledger",
        "configured_tickers": [],
        "earnings": dict(empty),
        "corporate_actions": {
            "splits": dict(empty),
            "dividends": dict(empty),
        },
        "missing_earnings_tickers": [],
        "installed_event_rows": 0,
        "corporate_action_control": {
            "receipt_id": "fixture",
            "source_options_date": "2026-09-10",
            "split_rows": 0,
            "dividend_rows": 0,
            "retired_split_rows": 0,
            "retired_dividend_rows": 0,
        },
    }


def _source_level_history() -> dict:
    return {
        "schema": contracts.SOURCE_UNIVERSE_SCHEMA,
        "source": {
            "kind": "analytical_duckdb",
            "completeness": "source_level",
            "as_of_date": "2026-09-10",
            "retired_membership": _retired_membership(),
        },
        "evidence_rule": "fixture",
        "decision_scope": "end_of_day_research",
        "live_trading_eligible": False,
        "event_count": 1,
        "audit": {
            "candidate_event_count": 1,
            "eligible_event_count": 1,
            "excluded_event_count": 0,
            "source_duplicate_rows_collapsed": 0,
            "exclusion_counts": {},
            "exclusions": [],
        },
        "events": [
            {
                "ticker": "AAPL",
                "date": "2026-08-01",
                "timing": "before_market_open",
                "actual": 0.10,
                "realized_abs": 0.10,
                "implied": 0.08,
                "implied_as_of": "2026-07-31",
                "implied_expiration": "2026-08-01",
                "implied_quality_status": "decision_eligible_eod",
                "edge": 0.02,
                "ratio": 1.25,
                "outside_implied": True,
                "realized_window": {
                    "pre_date": "2026-07-31",
                    "pre_price": 100.0,
                    "post_date": "2026-08-01",
                    "post_price_adjusted": 110.0,
                },
            }
        ],
        "universe_id": "sha256:" + "a" * 64,
        "generated_at": "2026-09-10T00:00:00+00:00",
    }


def test_source_level_research_history_checks_event_arithmetic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public = tmp_path / "public"
    public.mkdir()
    payload = _source_level_history()
    payload["events"][0]["ratio"] = 99.0
    (public / "research-history.json").write_text(json.dumps(payload))
    monkeypatch.setattr(contracts, "PUBLIC", public)

    with pytest.raises(contracts.ContractError, match="ratio arithmetic"):
        contracts.validate_research_history()


def test_source_level_research_history_rejects_live_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public = tmp_path / "public"
    public.mkdir()
    payload = _source_level_history()
    payload["live_trading_eligible"] = True
    (public / "research-history.json").write_text(json.dumps(payload))
    monkeypatch.setattr(contracts, "PUBLIC", public)

    with pytest.raises(contracts.ContractError, match="live-trading"):
        contracts.validate_research_history()


def test_source_level_research_history_requires_retired_membership_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public = tmp_path / "public"
    public.mkdir()
    payload = _source_level_history()
    del payload["source"]["retired_membership"]
    (public / "research-history.json").write_text(json.dumps(payload))
    monkeypatch.setattr(contracts, "PUBLIC", public)

    with pytest.raises(contracts.ContractError, match="retired_membership"):
        contracts.validate_research_history()


def test_source_level_research_history_rejects_tampered_membership_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public = tmp_path / "public"
    public.mkdir()
    payload = _source_level_history()
    payload["source"]["retired_membership"]["earnings"]["sha256"] = "sha256:" + "b" * 64
    (public / "research-history.json").write_text(json.dumps(payload))
    monkeypatch.setattr(contracts, "PUBLIC", public)

    with pytest.raises(contracts.ContractError, match="digest"):
        contracts.validate_research_history()
