from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.validate_public_contracts as contracts


def test_committed_public_contracts_validate() -> None:
    passed = contracts.validate_repo()
    assert passed == [
        "schema documents",
        "screener",
        "symbol payloads",
        "forecast evidence",
        "control plane",
        "model validation",
    ]


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
