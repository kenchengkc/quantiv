from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest
from ml.model_bundle import ModelBundleError

from scripts import provenance_model_rollback as rollback
from scripts import verify_model_recovery as verification
from scripts.tests.test_provenance_model_rollback import _state


def _write_receipt(path, **core):
    identity = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path.write_text(json.dumps({**core, "receipt_id": f"sha256:{identity}"}))
    return f"sha256:{identity}"


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    models, current, previous, private, public = _state(tmp_path)
    directory = tmp_path / "evidence"
    directory.mkdir()
    for name in ("data-pointer", "reconciliation"):
        for suffix in ("before", "after"):
            (directory / f"{name}-{suffix}.json").write_text('{"publication":"held"}')
    shutil.copy2(models / "control" / "champion.json", directory / "champion-before.json")
    candidate = directory / "forecast.parquet"
    pd.DataFrame({
        "act_symbol": ["AAA"], "earnings_date": ["2026-09-10"],
        "snapshot_date": ["2026-09-01"], "model_horizon": [7], "model_bundle_id": [previous],
    }).to_parquet(candidate, index=False)
    monkeypatch.setattr(rollback, "verify_bundle_dir", lambda *a, **kw: {})
    monkeypatch.setattr(rollback, "validate_forecast_artifact", lambda *a, **kw: {})
    rollback.provenance_rollback(
        models_root=models, expected_current_bundle_id=current, target_bundle_id=previous,
        candidate_forecast=candidate, production_forecast_dir=tmp_path / "forecasts",
        report_path=directory / "decision.json", reason="held-release provenance violation",
        private_key=private, public_key=public,
    )
    for name in ("champion", "registry"):
        shutil.copy2(models / "control" / f"{name}.json", directory / f"{name}-after.json")
    activation_id = _write_receipt(
        directory / "activation.json", schema="quantiv.serving-activation.v1", status="passed",
        expected_bundle_id=previous, activated_bundle_id=previous,
        activated_at="2026-09-06T15:00:00+00:00",
        decision_sha256=hashlib.sha256((directory / "decision.json").read_bytes()).hexdigest(),
    )
    _write_receipt(
        directory / "import.json", schema="quantiv.forecast-import.v1", status="passed",
        model_bundle_id=previous, activation_receipt_id=activation_id,
        parquet_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        parquet_file="forecasts_2026-09-01.parquet", rows_upserted=1, selected_rows=1,
    )
    return directory, previous, public


def test_verifies_signed_readback_and_linked_receipts(evidence):
    directory, target, public = evidence
    result = verification.verify_evidence(directory, target, public_key=public)
    assert result["import"]["model_bundle_id"] == target


@pytest.mark.parametrize("artifact", [
    "champion-after.json", "registry-after.json", "data-pointer-after.json",
    "reconciliation-after.json", "decision.json", "activation.json", "import.json",
])
def test_rejects_tampered_or_changed_recovery_evidence(evidence, artifact):
    directory, target, public = evidence
    path = directory / artifact
    payload = json.loads(path.read_text())
    payload["unexpected_mutation"] = True
    path.write_text(json.dumps(payload))
    with pytest.raises((ValueError, ModelBundleError)):
        verification.verify_evidence(directory, target, public_key=public)


def test_rejects_forecast_different_from_committed_receipt(evidence):
    directory, target, public = evidence
    frame = pd.read_parquet(directory / "forecast.parquet")
    frame["model_bundle_id"] = "c" * 64
    frame.to_parquet(directory / "forecast.parquet", index=False)
    with pytest.raises(ValueError, match="validated recovery forecast"):
        verification.verify_evidence(directory, target, public_key=public)


@pytest.mark.parametrize("failure", [None, "missing_key", "different_import", "old_import", "remaining_rejected"])
def test_database_requires_exact_keys_recent_import_and_no_rejected_upcoming(evidence, failure):
    directory, target, public = evidence
    verified = verification.verify_evidence(directory, target, public_key=public)
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    latest = (
        "c" * 64 if failure == "different_import" else target,
        "forecasts_2026-09-01.parquet", 1,
        datetime(2026, 9, 6, 14 if failure == "old_import" else 16, tzinfo=timezone.utc),
    )
    cursor.fetchone.side_effect = [
        (int(failure == "missing_key"),), latest, (int(failure == "remaining_rejected"),),
    ]
    if failure:
        with pytest.raises(ValueError):
            verification.verify_database(conn, verified, target)
    else:
        assert verification.verify_database(conn, verified, target)["verified_forecast_keys"] == 1
    assert all(call.args[0].strip().startswith("SELECT") for call in cursor.execute.call_args_list)


@pytest.mark.parametrize("missing", [0, 3])
def test_preflight_blocks_uncovered_rejected_forecasts_before_publication(evidence, missing):
    directory, target, public = evidence
    verified = verification.verify_evidence(directory, target, public_key=public)
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (missing,)
    if missing:
        with pytest.raises(ValueError, match="lacks replacements for 3"):
            verification.verify_replacement_coverage(conn, verified["frame"], verified["before"]["champion_bundle_id"], target)
    else:
        verification.verify_replacement_coverage(conn, verified["frame"], verified["before"]["champion_bundle_id"], target)
    assert cursor.execute.call_args.args[0].strip().startswith("SELECT")
