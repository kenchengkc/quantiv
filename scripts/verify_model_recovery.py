#!/usr/bin/env python3
"""Verify recovery receipts, signed R2 read-back, and committed Neon identities.

Railway evidence comes from the exact-bundle activation response (including all
loaded horizons). Database checks are read-only and cover every restored key;
historical rows from other legitimate bundles need not be rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "ml"))
from ml.model_bundle import verify_control_pointer, verify_registry  # noqa: E402


def _json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _receipt(path: Path, schema: str) -> dict:
    receipt = _json(path)
    core = {key: value for key, value in receipt.items() if key != "receipt_id"}
    identity = "sha256:" + hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (receipt.get("receipt_id") != identity or receipt.get("schema") != schema
            or receipt.get("status") != "passed"):
        raise ValueError(f"invalid recovery receipt: {path.name}")
    return receipt


def verify_evidence(evidence_dir: Path, expected: str, *, public_key=None) -> dict:
    before = verify_control_pointer(_json(evidence_dir / "champion-before.json"), public_key=public_key)
    after = verify_control_pointer(_json(evidence_dir / "champion-after.json"), public_key=public_key)
    registry = verify_registry(_json(evidence_dir / "registry-after.json"), public_key=public_key)
    if before.get("previous_bundle_id") != expected or before["champion_bundle_id"] == expected:
        raise ValueError("restored champion was not the signed previous bundle")
    for state in (after, registry):
        if (state.get("champion_bundle_id") != expected
                or state.get("previous_bundle_id") is not None
                or state.get("challenger_bundle_id") is not None):
            raise ValueError("R2 recovery roles do not match the authorized recovery")
        decision = state.get("decision") or {}
        if (decision.get("action") != "operator_provenance_rollback"
                or decision.get("from_bundle_id") != before["champion_bundle_id"]
                or decision.get("to_bundle_id") != expected):
            raise ValueError("signed recovery decision does not match authorization")
    for name in ("data-pointer", "reconciliation"):
        if (evidence_dir / f"{name}-before.json").read_bytes() != (
            evidence_dir / f"{name}-after.json"
        ).read_bytes():
            raise ValueError(f"recovery changed {name}; publication hold must remain intact")
    decision_path = evidence_dir / "decision.json"
    decision = _json(decision_path)
    if (decision.get("status") != "passed"
            or decision.get("action") != "operator_provenance_rollback"
            or decision.get("champion_bundle_id") != expected
            or decision.get("disqualified_bundle_id") != before["champion_bundle_id"]):
        raise ValueError("recovery report disagrees with signed authorization")
    activation = _receipt(evidence_dir / "activation.json", "quantiv.serving-activation.v1")
    imported = _receipt(evidence_dir / "import.json", "quantiv.forecast-import.v1")
    if (activation.get("expected_bundle_id") != expected
            or activation.get("activated_bundle_id") != expected
            or activation.get("decision_sha256") != hashlib.sha256(decision_path.read_bytes()).hexdigest()
            or imported.get("model_bundle_id") != expected
            or imported.get("activation_receipt_id") != activation["receipt_id"]):
        raise ValueError("Railway/Neon receipt identities disagree with recovery")
    forecast = evidence_dir / "forecast.parquet"
    if imported.get("parquet_sha256") != hashlib.sha256(forecast.read_bytes()).hexdigest():
        raise ValueError("import receipt does not identify the validated recovery forecast")
    frame = pd.read_parquet(forecast)
    if (frame.empty or frame["model_bundle_id"].isna().any()
            or set(frame["model_bundle_id"]) != {expected}
            or imported.get("rows_upserted") != len(frame)
            or imported.get("selected_rows") != len(frame)):
        raise ValueError("incomplete or mixed-bundle recovery import")
    return {"before": before, "activation": activation, "import": imported, "frame": frame}


def verify_database(connection, evidence: dict, expected: str) -> dict:
    frame = evidence["frame"]
    keys = frame[["act_symbol", "earnings_date", "snapshot_date", "model_horizon"]].copy()
    for column in ("earnings_date", "snapshot_date"):
        keys[column] = pd.to_datetime(keys[column]).dt.strftime("%Y-%m-%d")
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT COUNT(*) FROM jsonb_to_recordset(%s::jsonb) AS wanted(
                 act_symbol text, earnings_date date, snapshot_date date, model_horizon int)
               LEFT JOIN em_forecasts f USING
                 (act_symbol, earnings_date, snapshot_date, model_horizon)
               WHERE f.model_bundle_id IS DISTINCT FROM %s""",
            (keys.to_json(orient="records"), expected),
        )
        if cursor.fetchone()[0]:
            raise ValueError("Neon is missing restored forecast keys or has a different bundle")
        cursor.execute(
            """SELECT model_bundle_id, parquet_file, rows_upserted, imported_at
               FROM em_forecast_imports ORDER BY imported_at DESC, id DESC LIMIT 1"""
        )
        latest = cursor.fetchone()
        imported = evidence["import"]
        if (latest is None or latest[0] != expected or latest[1] != imported["parquet_file"]
                or latest[2] != len(frame)
                or pd.Timestamp(latest[3]) < pd.Timestamp(evidence["activation"]["activated_at"])):
            raise ValueError("Neon latest import is not this exact recovery")
        cursor.execute(
            """SELECT COUNT(*) FROM em_forecasts
               WHERE model_bundle_id = %s AND earnings_date >= CURRENT_DATE""",
            (evidence["before"]["champion_bundle_id"],),
        )
        if cursor.fetchone()[0]:
            raise ValueError("Neon still contains upcoming forecasts from the disqualified bundle")
    return {"verified_forecast_keys": len(keys), "latest_import_bundle_id": expected}


def verify_replacement_coverage(connection, frame: pd.DataFrame, rejected: str, target: str) -> None:
    """Reject an incomplete replacement before the first production mutation."""
    if frame.empty or frame["model_bundle_id"].isna().any() or set(frame["model_bundle_id"]) != {target}:
        raise ValueError("preflight forecast must contain only the exact target bundle")
    keys = frame[["act_symbol", "earnings_date", "snapshot_date", "model_horizon"]].copy()
    for column in ("earnings_date", "snapshot_date"):
        keys[column] = pd.to_datetime(keys[column]).dt.strftime("%Y-%m-%d")
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT COUNT(*) FROM em_forecasts f
               WHERE f.model_bundle_id = %s AND f.earnings_date >= CURRENT_DATE
               AND NOT EXISTS (
                 SELECT 1 FROM jsonb_to_recordset(%s::jsonb) AS replacement(
                   act_symbol text, earnings_date date, snapshot_date date, model_horizon int)
                 WHERE (replacement.act_symbol, replacement.earnings_date,
                        replacement.snapshot_date, replacement.model_horizon) =
                       (f.act_symbol, f.earnings_date, f.snapshot_date, f.model_horizon))""",
            (rejected, keys.to_json(orient="records")),
        )
        missing = cursor.fetchone()[0]
        if missing:
            raise ValueError(f"recovery lacks replacements for {missing} upcoming rejected forecast keys")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--expected-bundle-id", required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        before = verify_control_pointer(_json(args.evidence_dir / "champion-before.json"))
        if before.get("previous_bundle_id") != args.expected_bundle_id:
            raise ValueError("preflight target is not the signed previous bundle")
        frame = pd.read_parquet(args.evidence_dir / "forecast.parquet")
    else:
        evidence = verify_evidence(args.evidence_dir, args.expected_bundle_id)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required for read-only recovery verification")
    connection = psycopg2.connect(database_url, connect_timeout=30)
    try:
        connection.set_session(readonly=True)
        with connection:
            if args.preflight:
                verify_replacement_coverage(connection, frame, before["champion_bundle_id"], args.expected_bundle_id)
                print("Read-only Neon preflight: all upcoming rejected forecast keys have replacements")
                return 0
            database = verify_database(connection, evidence, args.expected_bundle_id)
    finally:
        connection.close()
    report = {
        "schema": "quantiv.model-recovery-verification.v1", "status": "passed",
        "r2_champion_bundle_id": args.expected_bundle_id,
        "railway_activated_bundle_id": evidence["activation"]["activated_bundle_id"],
        "activation_receipt_id": evidence["activation"]["receipt_id"],
        "import_receipt_id": evidence["import"]["receipt_id"],
        "data_publication_unchanged": True, **database,
    }
    (args.evidence_dir / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
