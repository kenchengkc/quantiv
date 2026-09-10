#!/usr/bin/env python3
"""Reserve and evaluate a current-run independent chronological final test.

The weekly retrain uses two explicit phases:

``prepare``
    After the existing operational outcome/rollback check, seal a recent test
    period, exclude every exact symbol/date/horizon key present in the prediction
    ledger used by that operational check, apply a label-availability embargo,
    and rewrite ``ml_training`` to development-only rows. Current-candidate
    tuning, early stopping, calibration, walk-forward, and common-holdout gates
    therefore cannot read the reserved labels.

``evaluate``
    Only after the promotion decision, verify the promoted signed bundle and its
    model-validation receipt, score the sealed rows, retain paired predictions,
    build a content-addressed research manifest, and sign a replayable evaluation
    receipt. Final-test performance is evidence and is not a post-decision gate.

This establishes independence for the current candidate-selection run. It does
not claim that pre-protocol researchers or earlier model-family iterations never
observed the same historical labels; the receipt records that boundary explicitly.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_PACKAGE_ROOT = REPO_ROOT / "apps" / "ml"
for candidate in (REPO_ROOT, ML_PACKAGE_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from ml.evidence_receipt import verify_evidence_receipt  # noqa: E402
from ml.model_bundle import (  # noqa: E402
    DEFAULT_HORIZONS,
    ModelBundleError,
    verify_bundle_dir,
    verify_control_pointer,
    verify_signed_payload,
)
from ml.model_control import score_bundle_frame  # noqa: E402
from research.paired_benchmark import compare_forecasts  # noqa: E402
from research.research_manifest import (  # noqa: E402
    build_manifest,
    sha256_file,
    verify_manifest,
)

RESERVATION_SCHEMA = "quantiv.independent-test-reservation.v1"
EVALUATION_SCHEMA = "quantiv.independent-model-evaluation.v1"
SELECTION_FAMILY = "quantiv.lightgbm.earnings-move.v1"
DEFAULT_TEST_DAYS = 120
DEFAULT_LABEL_AVAILABILITY_DAYS = 5
DEFAULT_PURGE_DAYS = 5
DEFAULT_MIN_DEVELOPMENT_ROWS = 1_000
DEFAULT_MIN_TEST_ROWS = 100
DEFAULT_BOOTSTRAP_DRAWS = 5_000
DEFAULT_BOOTSTRAP_SEED = 17
QUANTILE_LABELS = (10, 25, 50, 75, 90)
_LEDGER_COLUMNS = ("act_symbol", "earnings_date", "model_horizon")


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def _repo_relative(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"evaluation path escapes repository root: {path}") from exc


def _normalized_training_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    required = {"target", "straddle_pct", "__earnings_date", "__symbol"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{path.name} is missing required columns: {missing}")
    frame = frame.copy()
    frame["__earnings_date"] = pd.to_datetime(
        frame["__earnings_date"], errors="raise"
    ).dt.normalize()
    if frame.duplicated(["__symbol", "__earnings_date"]).any():
        raise ValueError(f"{path.name} contains duplicate symbol/event identities")
    target = pd.to_numeric(frame["target"], errors="coerce")
    if not np.isfinite(target.to_numpy(dtype=float)).all():
        raise ValueError(f"{path.name} contains non-finite targets")
    tie_breakers = [
        column
        for column in ("__symbol", "act_symbol", "symbol", "lead_days")
        if column in frame.columns
    ]
    return frame.sort_values(["__earnings_date", *tie_breakers], kind="mergesort")


def _ledger_evidence(
    repo_root: Path, ledger_path: Path
) -> tuple[set[tuple[str, str, int]], dict[str, Any]]:
    """Return exact operational outcome keys and immutable evidence of the ledger.

    ``evaluate_realized_outcomes`` can only consume a realized label after an
    exact join to the prediction ledger on symbol, event date, and horizon. By
    excluding every such key from the reserved test, the final-test labels are
    disjoint from the operational rollback calculation that ran earlier in the
    same weekly workflow.
    """
    if not ledger_path.exists():
        return set(), {
            "method": "exclude_prediction_ledger_symbol_date_horizon_keys",
            "prediction_ledger_present": False,
            "prediction_ledger_path": _repo_relative(repo_root, ledger_path),
            "prediction_ledger_sha256": None,
            "prediction_ledger_rows": 0,
            "prediction_ledger_event_keys": 0,
        }

    available = set(pd.read_parquet(ledger_path).columns)
    missing = sorted(set(_LEDGER_COLUMNS) - available)
    if missing:
        raise ValueError(
            f"prediction ledger is missing outcome-join columns: {missing}"
        )
    frame = pd.read_parquet(ledger_path, columns=list(_LEDGER_COLUMNS)).copy()
    frame["earnings_date"] = pd.to_datetime(
        frame["earnings_date"], errors="raise"
    ).dt.date.astype(str)
    horizons = pd.to_numeric(frame["model_horizon"], errors="raise")
    if not np.equal(horizons, np.floor(horizons)).all():
        raise ValueError("prediction ledger contains non-integral model horizons")
    frame["model_horizon"] = horizons.astype(int)
    frame["act_symbol"] = frame["act_symbol"].astype(str).str.strip()
    if frame["act_symbol"].eq("").any():
        raise ValueError("prediction ledger contains blank symbols")
    keys = set(
        zip(
            frame["act_symbol"],
            frame["earnings_date"],
            frame["model_horizon"],
            strict=True,
        )
    )
    return keys, {
        "method": "exclude_prediction_ledger_symbol_date_horizon_keys",
        "prediction_ledger_present": True,
        "prediction_ledger_path": _repo_relative(repo_root, ledger_path),
        "prediction_ledger_sha256": sha256_file(ledger_path),
        "prediction_ledger_rows": len(frame),
        "prediction_ledger_event_keys": len(keys),
    }


def _reservation_identity(core: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical(core))


def prepare_independent_test(
    *,
    repo_root: Path,
    training_dir: Path,
    staging_dir: Path,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    test_days: int = DEFAULT_TEST_DAYS,
    purge_days: int = DEFAULT_PURGE_DAYS,
    label_availability_days: int = DEFAULT_LABEL_AVAILABILITY_DAYS,
    min_development_rows: int = DEFAULT_MIN_DEVELOPMENT_ROWS,
    min_test_rows: int = DEFAULT_MIN_TEST_ROWS,
    source_revision: str | None = None,
    prediction_ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Seal final-test rows and remove them from all current selection inputs."""
    if test_days < 1:
        raise ValueError("test_days must be at least 1")
    if purge_days < 0 or label_availability_days < 0:
        raise ValueError("purge and label-availability windows must be non-negative")
    effective_embargo = max(purge_days, label_availability_days)
    source_revision = source_revision or os.getenv("GITHUB_SHA") or "unknown"
    ledger_path = prediction_ledger_path or (
        repo_root / "data/models/monitoring/prediction_ledger.parquet"
    )
    operational_keys, operational_evidence = _ledger_evidence(repo_root, ledger_path)

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    prepared: list[dict[str, Any]] = []
    development_paths: dict[int, Path] = {}
    metadata_payloads: dict[int, dict[str, Any]] = {}
    for horizon in sorted(set(int(value) for value in horizons)):
        training_path = training_dir / f"training_T{horizon}.parquet"
        metadata_path = training_dir / f"metadata_T{horizon}.json"
        if not training_path.is_file() or not metadata_path.is_file():
            raise FileNotFoundError(
                f"T-{horizon} training parquet and feature metadata are required"
            )
        frame = _normalized_training_frame(training_path)
        source_sha = sha256_file(training_path)
        test_end = pd.Timestamp(frame["__earnings_date"].max()).normalize()
        test_start = test_end - pd.Timedelta(days=test_days - 1)
        development_cutoff = test_start - pd.Timedelta(days=effective_embargo)

        recent = frame.loc[frame["__earnings_date"] >= test_start].copy()
        recent_keys = pd.Series(
            list(
                zip(
                    recent["__symbol"].astype(str),
                    recent["__earnings_date"].dt.date.astype(str),
                    [horizon] * len(recent),
                    strict=True,
                )
            ),
            index=recent.index,
            dtype=object,
        )
        operational_mask = recent_keys.isin(operational_keys)
        final_test = recent.loc[~operational_mask].copy()
        operational_excluded = recent.loc[operational_mask].copy()
        development = frame.loc[
            frame["__earnings_date"] < development_cutoff
        ].copy()
        purged = frame.loc[
            (frame["__earnings_date"] >= development_cutoff)
            & (frame["__earnings_date"] < test_start)
        ].copy()

        if len(development) < min_development_rows:
            raise ValueError(
                f"T-{horizon} has only {len(development)} development rows; "
                f"require {min_development_rows}"
            )
        if len(final_test) < min_test_rows:
            raise ValueError(
                f"T-{horizon} has only {len(final_test)} final-test rows after "
                f"operational-outcome exclusions; require {min_test_rows}"
            )
        if development["__earnings_date"].max() >= development_cutoff:
            raise AssertionError("development rows crossed the embargo boundary")
        final_keys = set(
            zip(
                final_test["__symbol"].astype(str),
                final_test["__earnings_date"].dt.date.astype(str),
                [horizon] * len(final_test),
                strict=True,
            )
        )
        if final_keys & operational_keys:
            raise AssertionError("final test overlaps operational outcome ledger")

        development_path = staging_dir / f"development_T{horizon}.parquet"
        final_test_path = staging_dir / f"final_test_T{horizon}.parquet"
        _atomic_parquet(development_path, development)
        _atomic_parquet(final_test_path, final_test)
        development_paths[horizon] = development_path
        metadata_payloads[horizon] = _read_json(metadata_path)
        prepared.append(
            {
                "horizon_days": horizon,
                "source_rows": len(frame),
                "source_sha256": source_sha,
                "development_rows": len(development),
                "development_sha256": sha256_file(development_path),
                "purged_rows": len(purged),
                "operational_excluded_rows": len(operational_excluded),
                "final_test_rows": len(final_test),
                "final_test_unique_events": int(
                    final_test[["__symbol", "__earnings_date"]]
                    .drop_duplicates()
                    .shape[0]
                ),
                "final_test_sha256": sha256_file(final_test_path),
                "development_start": development["__earnings_date"].min().date().isoformat(),
                "development_end": development["__earnings_date"].max().date().isoformat(),
                "embargo_start": development_cutoff.date().isoformat(),
                "reservation_window_start": test_start.date().isoformat(),
                "reservation_window_end": test_end.date().isoformat(),
                "final_test_start": final_test["__earnings_date"].min().date().isoformat(),
                "final_test_end": final_test["__earnings_date"].max().date().isoformat(),
                "final_test_path": _repo_relative(repo_root, final_test_path),
            }
        )

    core = {
        "schema": RESERVATION_SCHEMA,
        "source_revision": source_revision,
        "protocol": {
            "method": "recent_completed_events_with_label_embargo_and_operational_exclusion",
            "test_days": test_days,
            "requested_purge_days": purge_days,
            "label_availability_days": label_availability_days,
            "effective_embargo_days": effective_embargo,
            "target_availability_basis": (
                "realized move uses the first post-earnings observation within five calendar days"
            ),
            "operational_outcome_exclusion": operational_evidence,
        },
        "horizons": prepared,
    }
    reservation = {
        **core,
        "reservation_id": _reservation_identity(core),
        "prepared_at": datetime.now(UTC).isoformat(),
    }

    # Replace selection inputs only after every horizon has been staged and
    # validated. The GitHub runner is ephemeral; failure aborts before R2 push.
    for row in prepared:
        horizon = int(row["horizon_days"])
        training_path = training_dir / f"training_T{horizon}.parquet"
        shutil.copy2(development_paths[horizon], training_path)
        metadata_path = training_dir / f"metadata_T{horizon}.json"
        metadata = metadata_payloads[horizon]
        metadata["source_n_samples_before_independent_test"] = metadata.get("n_samples")
        metadata["n_samples"] = int(row["development_rows"])
        metadata["independent_test_reservation"] = {
            "reservation_id": reservation["reservation_id"],
            "reservation_window_start": row["reservation_window_start"],
            "reservation_window_end": row["reservation_window_end"],
            "development_end": row["development_end"],
            "effective_embargo_days": effective_embargo,
            "operational_excluded_rows": row["operational_excluded_rows"],
            "final_test_rows": row["final_test_rows"],
        }
        _atomic_json(metadata_path, metadata)
        if sha256_file(training_path) != row["development_sha256"]:
            raise RuntimeError(f"T-{horizon} development training copy changed bytes")

    _atomic_json(staging_dir / "reservation.json", reservation)
    return reservation


def _receipt_artifact(receipt: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    matches = [
        item
        for item in (receipt.get("artifacts") or [])
        if isinstance(item, Mapping) and item.get("name") == name
    ]
    if len(matches) != 1:
        raise ModelBundleError(f"model validation receipt must contain one {name}")
    return matches[0]


def _immutable_model_receipt(models_root: Path, receipt_id: str) -> dict[str, Any]:
    if not receipt_id.startswith("sha256:") or len(receipt_id) != 71:
        raise ModelBundleError("bundle has an invalid model-validation receipt id")
    digest = receipt_id.removeprefix("sha256:")
    path = models_root / "receipts" / f"models.{digest[:12]}.receipt.json"
    receipt = verify_evidence_receipt(_read_json(path), expected_scope="models")
    if receipt["receipt_id"] != receipt_id:
        raise ModelBundleError("bundle and model-validation receipt identities disagree")
    if receipt.get("quality", {}).get("status") != "passed":
        raise ModelBundleError("model-validation receipt is not passed")
    return receipt


def _verify_development_binding(
    reservation: Mapping[str, Any], model_receipt: Mapping[str, Any]
) -> None:
    members = _receipt_artifact(model_receipt, "training_bundle").get("members")
    if not isinstance(members, list):
        raise ModelBundleError("model-validation receipt has no training members")
    member_by_name = {
        Path(str(item.get("path", ""))).name: item
        for item in members
        if isinstance(item, Mapping)
    }
    for row in reservation.get("horizons") or []:
        if not isinstance(row, Mapping):
            raise ModelBundleError("independent-test reservation has invalid horizon evidence")
        horizon = int(row["horizon_days"])
        member = member_by_name.get(f"training_T{horizon}.parquet")
        if not isinstance(member, Mapping):
            raise ModelBundleError(
                f"model-validation receipt is missing T-{horizon} development training"
            )
        expected = str(row["development_sha256"]).removeprefix("sha256:")
        if member.get("sha256") != expected:
            raise ModelBundleError(
                f"T-{horizon} model validation was not run on reserved development data"
            )


def _signing_key_bytes(private_key: str | bytes | None = None) -> bytes:
    material = private_key if private_key is not None else os.getenv("MODEL_BUNDLE_SIGNING_KEY")
    if not material:
        raise ModelBundleError("MODEL_BUNDLE_SIGNING_KEY is required for independent evaluation")
    return material if isinstance(material, bytes) else material.encode()


def _sign_evaluation(
    payload: Mapping[str, Any], private_key: str | bytes | None = None
) -> dict[str, Any]:
    key = serialization.load_pem_private_key(_signing_key_bytes(private_key), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ModelBundleError("independent evaluation signing key must be Ed25519")
    signature = key.sign(_canonical(payload))
    return {
        **payload,
        "signature": {
            "algorithm": "ed25519",
            "value": base64.b64encode(signature).decode(),
        },
    }


def _evaluation_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "bundle_id",
        "model_validation_receipt_id",
        "training_bundle_sha256",
        "source_revision",
        "reservation_id",
        "selection",
        "protocol",
        "summary",
        "horizons",
        "research_manifest",
    )
    return {field: payload.get(field) for field in fields}


def verify_independent_evaluation_receipt(
    receipt: Mapping[str, Any],
    *,
    repo_root: Path,
    bundle_dir: Path,
    public_key: str | bytes | None = None,
) -> dict[str, Any]:
    payload = verify_signed_payload(receipt, public_key=public_key)
    if payload.get("schema") != EVALUATION_SCHEMA:
        raise ModelBundleError("unsupported independent evaluation schema")
    manifest = verify_bundle_dir(bundle_dir, public_key=public_key)
    if payload.get("bundle_id") != manifest.get("bundle_id"):
        raise ModelBundleError("independent evaluation targets a different model bundle")
    if payload.get("model_validation_receipt_id") != manifest.get("receipt_id"):
        raise ModelBundleError(
            "independent evaluation and signed bundle disagree on validation receipt"
        )
    expected_id = _sha256_bytes(_canonical(_evaluation_identity(payload)))
    if payload.get("evaluation_id") != expected_id:
        raise ModelBundleError("independent evaluation id does not match its contents")
    manifest_report = verify_manifest(
        payload.get("research_manifest") or {}, repo_root=repo_root
    )
    if not manifest_report.get("ok"):
        raise ModelBundleError(
            "independent evaluation research manifest failed verification: "
            f"{manifest_report.get('errors')}"
        )
    return dict(payload)


def _horizon_reservation(
    reservation: Mapping[str, Any], horizon: int
) -> Mapping[str, Any]:
    matches = [
        row
        for row in (reservation.get("horizons") or [])
        if isinstance(row, Mapping) and int(row.get("horizon_days", -1)) == horizon
    ]
    if len(matches) != 1:
        raise ValueError(f"reservation must contain exactly one T-{horizon} row")
    return matches[0]


def _coverage(actual: np.ndarray, quantiles: np.ndarray) -> dict[str, float]:
    result = {
        f"q{quantile:02d}": float(np.mean(actual <= quantiles[:, index]))
        for index, quantile in enumerate(QUANTILE_LABELS)
    }
    result["interval_50"] = float(
        np.mean((actual >= quantiles[:, 1]) & (actual <= quantiles[:, 3]))
    )
    result["interval_80"] = float(
        np.mean((actual >= quantiles[:, 0]) & (actual <= quantiles[:, 4]))
    )
    return result


def _conservative_interval(*reports: Mapping[str, Any]) -> list[float]:
    intervals = [
        report["overall"]["mean_absolute_error_difference_95_ci"]
        for report in reports
    ]
    return [
        float(min(interval[0] for interval in intervals)),
        float(max(interval[1] for interval in intervals)),
    ]


def evaluate_independent_test(
    *,
    repo_root: Path,
    bundle_dir: Path,
    reservation_path: Path,
    decision_path: Path,
    models_root: Path,
    output_root: Path,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    source_revision: str | None = None,
    bootstrap_draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    private_key: str | bytes | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Score a promoted bundle on sealed labels and sign the evidence release."""
    reservation = _read_json(reservation_path)
    if reservation.get("schema") != RESERVATION_SCHEMA:
        raise ValueError("unsupported independent-test reservation schema")
    core = {
        key: reservation[key]
        for key in ("schema", "source_revision", "protocol", "horizons")
    }
    if reservation.get("reservation_id") != _reservation_identity(core):
        raise ValueError("independent-test reservation id does not match its contents")

    bundle_manifest = verify_bundle_dir(bundle_dir)
    bundle_id = str(bundle_manifest["bundle_id"])
    decision = _read_json(decision_path)
    if decision.get("status") != "passed" or decision.get("promoted") is not True:
        raise ModelBundleError(
            "independent final-test evaluation requires a completed promotion decision"
        )
    if (
        decision.get("candidate_bundle_id") != bundle_id
        or decision.get("champion_bundle_id") != bundle_id
    ):
        raise ModelBundleError(
            "promotion decision does not select the bundle being independently evaluated"
        )
    champion_pointer = verify_control_pointer(
        _read_json(models_root / "control" / "champion.json")
    )
    if champion_pointer.get("champion_bundle_id") != bundle_id:
        raise ModelBundleError("signed champion pointer does not match evaluation bundle")

    model_receipt = _immutable_model_receipt(
        models_root, str(bundle_manifest["receipt_id"])
    )
    _verify_development_binding(reservation, model_receipt)
    training_bundle = _receipt_artifact(model_receipt, "training_bundle")

    release_dir = output_root / bundle_id
    if release_dir.exists():
        shutil.rmtree(release_dir)
    inputs_dir = release_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    horizon_reports: list[dict[str, Any]] = []
    manifest_paths: list[Path] = [decision_path, bundle_dir / "manifest.json"]
    manifest_paths.extend(
        bundle_dir / str(item["name"])
        for item in bundle_manifest.get("artifacts") or []
        if isinstance(item, Mapping)
    )
    ledger_evidence = (
        (reservation.get("protocol") or {}).get("operational_outcome_exclusion") or {}
    )
    ledger_relative = ledger_evidence.get("prediction_ledger_path")
    if ledger_evidence.get("prediction_ledger_present") and isinstance(
        ledger_relative, str
    ):
        ledger_path = repo_root / ledger_relative
        if sha256_file(ledger_path) != ledger_evidence.get("prediction_ledger_sha256"):
            raise ValueError("prediction ledger changed after independent-test reservation")
        manifest_paths.append(ledger_path)

    all_event_ids: set[str] = set()
    total_paired_rows = 0
    weighted_model_error = 0.0
    weighted_baseline_error = 0.0

    for offset, horizon in enumerate(sorted(set(int(value) for value in horizons))):
        reserved = _horizon_reservation(reservation, horizon)
        source_path = repo_root / str(reserved["final_test_path"])
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        if sha256_file(source_path) != reserved.get("final_test_sha256"):
            raise ValueError(f"T-{horizon} sealed final-test bytes changed after reservation")
        final_test = _normalized_training_frame(source_path)
        if len(final_test) != int(reserved["final_test_rows"]):
            raise ValueError(f"T-{horizon} final-test row count changed after reservation")
        expected_start = pd.Timestamp(str(reserved["final_test_start"]))
        expected_end = pd.Timestamp(str(reserved["final_test_end"]))
        if (
            final_test["__earnings_date"].min() != expected_start
            or final_test["__earnings_date"].max() != expected_end
        ):
            raise ValueError(f"T-{horizon} final-test date range changed after reservation")

        metadata = _read_json(bundle_dir / f"metadata_T{horizon}.json")
        validation_split = metadata.get("validation_split") or {}
        validation_end = pd.Timestamp(str(validation_split.get("validation_end")))
        reservation_start = pd.Timestamp(str(reserved["reservation_window_start"]))
        if pd.isna(validation_end) or validation_end >= reservation_start:
            raise ModelBundleError(
                f"T-{horizon} development validation overlaps reserved final-test window"
            )

        point, quantiles = score_bundle_frame(bundle_dir, horizon, final_test)
        actual = pd.to_numeric(final_test["target"], errors="raise").to_numpy(dtype=float)
        baseline_series = pd.to_numeric(final_test["straddle_pct"], errors="coerce")
        predictions = pd.DataFrame(
            {
                "event_id": (
                    final_test["__symbol"].astype(str)
                    + "|"
                    + final_test["__earnings_date"].dt.date.astype(str)
                ),
                "issuer": final_test["__symbol"].astype(str).to_numpy(),
                "earnings_date": final_test["__earnings_date"].dt.date.astype(str).to_numpy(),
                "horizon_days": horizon,
                "actual_move": actual,
                "model_move": point,
                "straddle_baseline": baseline_series.to_numpy(dtype=float),
                **{
                    f"q{quantile:02d}": quantiles[:, index]
                    for index, quantile in enumerate(QUANTILE_LABELS)
                },
            }
        )
        predictions["earnings_week"] = pd.to_datetime(
            predictions["earnings_date"]
        ).dt.to_period("W-SUN").astype(str)
        if predictions["event_id"].duplicated().any():
            raise ValueError(f"T-{horizon} independent predictions contain duplicate events")

        paired = predictions.loc[
            np.isfinite(predictions["straddle_baseline"].to_numpy(dtype=float))
            & (predictions["straddle_baseline"].to_numpy(dtype=float) > 0)
        ].copy()
        if len(paired) < 20:
            raise ValueError(f"T-{horizon} has only {len(paired)} paired baseline rows")
        issuer_bootstrap = compare_forecasts(
            paired,
            actual_column="actual_move",
            model_column="model_move",
            baseline_column="straddle_baseline",
            cluster_column="issuer",
            draws=bootstrap_draws,
            seed=bootstrap_seed + offset * 2,
        )
        week_bootstrap = compare_forecasts(
            paired,
            actual_column="actual_move",
            model_column="model_move",
            baseline_column="straddle_baseline",
            cluster_column="earnings_week",
            draws=bootstrap_draws,
            seed=bootstrap_seed + offset * 2 + 1,
        )
        paired_metrics = issuer_bootstrap["overall"]
        report = {
            "horizon_days": horizon,
            "test_start": expected_start.date().isoformat(),
            "test_end": expected_end.date().isoformat(),
            "rows": len(predictions),
            "unique_events": int(predictions["event_id"].nunique()),
            "issuers": int(predictions["issuer"].nunique()),
            "paired_baseline_rows": len(paired),
            "model_mae_all_rows": float(mean_absolute_error(actual, point)),
            "model_rmse_all_rows": float(
                np.sqrt(mean_squared_error(actual, point))
            ),
            "model_r2_all_rows": float(r2_score(actual, point)),
            "paired_model_mae": paired_metrics["model_mae"],
            "paired_straddle_mae": paired_metrics["baseline_mae"],
            "paired_relative_mae_improvement": (
                1.0 - paired_metrics["model_mae"] / paired_metrics["baseline_mae"]
                if paired_metrics["baseline_mae"] > 0
                else None
            ),
            "mean_absolute_error_difference": paired_metrics[
                "mean_absolute_error_difference"
            ],
            "mae_difference_95_ci": {
                "issuer_clustered": issuer_bootstrap["overall"][
                    "mean_absolute_error_difference_95_ci"
                ],
                "earnings_week_clustered": week_bootstrap["overall"][
                    "mean_absolute_error_difference_95_ci"
                ],
                "conservative_envelope": _conservative_interval(
                    issuer_bootstrap, week_bootstrap
                ),
            },
            "coverage": _coverage(actual, quantiles),
        }
        input_path = inputs_dir / f"final_test_T{horizon}.parquet"
        prediction_path = release_dir / f"predictions_T{horizon}.parquet"
        shutil.copy2(source_path, input_path)
        _atomic_parquet(prediction_path, predictions)
        report["input_sha256"] = sha256_file(input_path)
        report["predictions_sha256"] = sha256_file(prediction_path)
        horizon_reports.append(report)
        manifest_paths.extend([input_path, prediction_path])
        all_event_ids.update(predictions["event_id"].astype(str))
        total_paired_rows += len(paired)
        weighted_model_error += float(paired_metrics["model_mae"]) * len(paired)
        weighted_baseline_error += float(paired_metrics["baseline_mae"]) * len(paired)

    source_revision = (
        source_revision
        or os.getenv("GITHUB_SHA")
        or str(bundle_manifest.get("source_revision") or "unknown")
    )
    research_manifest = build_manifest(
        [_repo_relative(repo_root, path) for path in manifest_paths],
        repo_root=repo_root,
        as_of=str(reservation.get("prepared_at") or ""),
        git_commit=source_revision,
        metadata={
            "purpose": "current_run_independent_final_model_evaluation",
            "bundle_id": bundle_id,
            "reservation_id": reservation["reservation_id"],
        },
    )
    weighted_model = weighted_model_error / total_paired_rows
    weighted_baseline = weighted_baseline_error / total_paired_rows
    identity_payload = {
        "bundle_id": bundle_id,
        "model_validation_receipt_id": model_receipt["receipt_id"],
        "training_bundle_sha256": training_bundle.get("sha256"),
        "source_revision": source_revision,
        "reservation_id": reservation["reservation_id"],
        "selection": {
            "family": SELECTION_FAMILY,
            "current_candidate_test_labels_used_for_hyperparameter_tuning": False,
            "current_candidate_test_labels_used_for_early_stopping": False,
            "current_candidate_test_labels_used_for_quantile_calibration": False,
            "current_candidate_test_rows_used_in_common_holdout": False,
            "current_run_test_rows_used_in_operational_outcome_check": False,
            "promotion_decision_evaluated_at": decision.get("evaluated_at"),
            "performance_gate_applied_after_decision": False,
            "historical_model_family_exposure_status": "not_established",
        },
        "protocol": {
            **dict(reservation.get("protocol") or {}),
            "uncertainty": (
                "paired error-difference bootstrap clustered separately by issuer "
                "and earnings week; conservative interval is their envelope"
            ),
            "bootstrap_draws": bootstrap_draws,
            "bootstrap_seed": bootstrap_seed,
        },
        "summary": {
            "row_observations": sum(int(row["rows"]) for row in horizon_reports),
            "unique_events_across_horizons": len(all_event_ids),
            "paired_baseline_row_observations": total_paired_rows,
            "weighted_paired_model_mae": weighted_model,
            "weighted_paired_straddle_mae": weighted_baseline,
            "weighted_paired_relative_mae_improvement": (
                1.0 - weighted_model / weighted_baseline
                if weighted_baseline > 0
                else None
            ),
            "aggregate_significance_claim": False,
        },
        "horizons": horizon_reports,
        "research_manifest": research_manifest,
    }
    unsigned = {
        "schema": EVALUATION_SCHEMA,
        "evaluation_id": _sha256_bytes(_canonical(identity_payload)),
        "evaluated_at": datetime.now(UTC).isoformat(),
        **identity_payload,
    }
    receipt = _sign_evaluation(unsigned, private_key=private_key)
    receipt_path = release_dir / "receipt.json"
    _atomic_json(receipt_path, receipt)
    verify_independent_evaluation_receipt(
        receipt,
        repo_root=repo_root,
        bundle_dir=bundle_dir,
    )
    return receipt_path, receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare", help="seal the final test and rewrite training to development rows"
    )
    prepare.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    prepare.add_argument("--training-dir", type=Path, default=REPO_ROOT / "data/ml_training")
    prepare.add_argument(
        "--staging-dir",
        type=Path,
        default=REPO_ROOT / "data/independent_evaluation/pending",
    )
    prepare.add_argument(
        "--prediction-ledger",
        type=Path,
        default=REPO_ROOT / "data/models/monitoring/prediction_ledger.parquet",
    )
    prepare.add_argument("--test-days", type=int, default=DEFAULT_TEST_DAYS)
    prepare.add_argument("--purge-days", type=int, default=DEFAULT_PURGE_DAYS)
    prepare.add_argument(
        "--label-availability-days",
        type=int,
        default=DEFAULT_LABEL_AVAILABILITY_DAYS,
    )
    prepare.add_argument(
        "--min-development-rows", type=int, default=DEFAULT_MIN_DEVELOPMENT_ROWS
    )
    prepare.add_argument("--min-test-rows", type=int, default=DEFAULT_MIN_TEST_ROWS)
    prepare.add_argument("--source-revision")

    evaluate = subparsers.add_parser(
        "evaluate", help="evaluate a promoted bundle on the sealed final test"
    )
    evaluate.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    evaluate.add_argument("--bundle-dir", type=Path, required=True)
    evaluate.add_argument(
        "--reservation",
        type=Path,
        default=REPO_ROOT / "data/independent_evaluation/pending/reservation.json",
    )
    evaluate.add_argument(
        "--decision",
        type=Path,
        default=REPO_ROOT / "data/validation/model_decision.json",
    )
    evaluate.add_argument("--models-root", type=Path, default=REPO_ROOT / "data/models")
    evaluate.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "data/models/evaluations",
    )
    evaluate.add_argument("--bootstrap-draws", type=int, default=DEFAULT_BOOTSTRAP_DRAWS)
    evaluate.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    evaluate.add_argument("--source-revision")

    verify = subparsers.add_parser("verify", help="verify a retained evaluation release")
    verify.add_argument("receipt", type=Path)
    verify.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    verify.add_argument("--bundle-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "prepare":
        reservation = prepare_independent_test(
            repo_root=args.repo_root.resolve(),
            training_dir=args.training_dir.resolve(),
            staging_dir=args.staging_dir.resolve(),
            prediction_ledger_path=args.prediction_ledger.resolve(),
            test_days=args.test_days,
            purge_days=args.purge_days,
            label_availability_days=args.label_availability_days,
            min_development_rows=args.min_development_rows,
            min_test_rows=args.min_test_rows,
            source_revision=args.source_revision,
        )
        print(json.dumps(reservation, indent=2, sort_keys=True))
        return 0
    if args.command == "evaluate":
        receipt_path, receipt = evaluate_independent_test(
            repo_root=args.repo_root.resolve(),
            bundle_dir=args.bundle_dir.resolve(),
            reservation_path=args.reservation.resolve(),
            decision_path=args.decision.resolve(),
            models_root=args.models_root.resolve(),
            output_root=args.output_root.resolve(),
            source_revision=args.source_revision,
            bootstrap_draws=args.bootstrap_draws,
            bootstrap_seed=args.bootstrap_seed,
        )
        print(
            json.dumps(
                {"receipt": str(receipt_path), "evaluation_id": receipt["evaluation_id"]},
                indent=2,
            )
        )
        return 0

    receipt = _read_json(args.receipt.resolve())
    verified = verify_independent_evaluation_receipt(
        receipt,
        repo_root=args.repo_root.resolve(),
        bundle_dir=args.bundle_dir.resolve(),
    )
    print(
        json.dumps(
            {"evaluation_id": verified["evaluation_id"], "status": "verified"},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
