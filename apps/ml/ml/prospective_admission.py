"""Predeclared admission of new lead times and cohorts on future outcomes."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ml.model_bundle import DEFAULT_QUANTILES, verify_bundle_dir
from ml.model_control import (
    _coverage_error,
    _paired_metrics,
    prospective_event_mask,
    prospective_snapshot_mask,
    selection_exposure_end,
)
from ml.model_protocol import feature_protocol
from ml.serving_capabilities import allowed_cohorts

SCHEMA = "quantiv.prospective-admission.v1"
MINIMUM_ROWS = 200


def policy_fingerprint() -> str:
    """Bind imported acceptance, exposure, source-label and timing rules too."""
    root = Path(__file__).resolve().parents[3]
    names = (
        "apps/ml/ml/prospective_admission.py",
        "apps/ml/ml/model_control.py",
        "apps/ml/ml/model_protocol.py",
        "apps/ml/ml/serving_capabilities.py",
        "apps/ml/ml/quantiles.py",
        "apps/ml/ml/causal_features.py",
        "apps/ml/ml/corporate_actions.py",
        "scripts/prospective_outcomes.py",
        "scripts/event_forecast_ledger.py",
        "scripts/market_sessions.py",
        "config/market_sessions.json",
    )
    members = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names
    }
    return hashlib.sha256(json.dumps(members, sort_keys=True).encode()).hexdigest()


def _metadata(path: Path, horizon: int) -> dict:
    return json.loads((path / f"metadata_T{horizon}.json").read_text())


def declare_admission(
    candidate: Path, champion: Path | None, *, started_at: str
) -> dict:
    """Declare comparators before any admissible prediction is recorded.

    Replacements retain the incumbent comparison. A cohort/head the incumbent
    cannot serve must beat its matched external baseline and absolute quantile
    calibration checks; an unsupported native model is never used as truth.
    """
    cm = verify_bundle_dir(candidate)
    hm = verify_bundle_dir(champion) if champion is not None else None
    comparators = {}
    for horizon in cm["horizons"]:
        supported = allowed_cohorts(cm, horizon, _metadata(candidate, horizon))
        incumbent = (
            allowed_cohorts(hm, horizon, _metadata(champion, horizon))
            if hm and horizon in hm["horizons"]
            else []
        )
        comparators[str(horizon)] = {
            cohort: "incumbent"
            if cohort in incumbent
            else "straddle"
            if cohort == "strict_options"
            else "historical_median"
            for cohort in supported
        }
    return {
        "schema": SCHEMA,
        "candidate_bundle_id": cm["bundle_id"],
        "champion_bundle_id": hm["bundle_id"] if hm else None,
        "started_at": started_at,
        "minimum_rows": MINIMUM_ROWS,
        "policy_sha256": policy_fingerprint(),
        "comparators": comparators,
    }


def _observations(ledger, labels, bundle, metadata, horizon, start, now):
    work = ledger.loc[
        (ledger["bundle_id"] == bundle) & (ledger["model_horizon"] == horizon)
    ].copy()
    exposure = selection_exposure_end(metadata)
    if work.empty or exposure is None or labels.empty:
        return pd.DataFrame()
    work["earnings_date"] = pd.to_datetime(work["earnings_date"])
    work["snapshot_date"] = pd.to_datetime(work["snapshot_date"])
    valid = prospective_snapshot_mask(work) & prospective_event_mask(work)
    recorded = pd.to_datetime(work["recorded_at"], utc=True, errors="coerce")
    scored = pd.to_datetime(
        work.get("scored_at", work["recorded_at"]), utc=True, errors="coerce"
    )
    valid &= (
        (recorded >= start) & (scored >= start) & (recorded <= now) & (scored <= now)
    )
    valid &= work["snapshot_date"] > exposure
    valid &= (work["earnings_date"] - work["snapshot_date"]).dt.days == horizon
    valid &= work["feature_protocol"].eq(feature_protocol(metadata))
    work = work.loc[valid]
    outcomes = labels.rename(
        columns={"__symbol": "act_symbol", "__earnings_date": "earnings_date"}
    ).copy()
    outcomes["earnings_date"] = pd.to_datetime(outcomes["earnings_date"])
    if outcomes.duplicated(["act_symbol", "earnings_date"]).any():
        raise ValueError("prospective outcomes contain duplicate event identities")
    work = work.merge(
        outcomes, on=["act_symbol", "earnings_date"], validate="many_to_one"
    )
    available = pd.to_datetime(work["__label_available_at"], utc=True, errors="coerce")
    work = work.loc[
        (available <= now)
        & (pd.to_datetime(work["recorded_at"], utc=True) < available)
        & (
            pd.to_datetime(work.get("scored_at", work["recorded_at"]), utc=True)
            < available
        )
    ]
    baseline = pd.to_numeric(work["em_math_pct"], errors="coerce")
    work["__cohort"] = np.where(
        np.isfinite(baseline) & (baseline > 0), "strict_options", "optionless"
    )
    return work.drop_duplicates(["act_symbol", "earnings_date"], keep="first")


def _baseline_metrics(frame, baseline_name):
    baseline = pd.to_numeric(
        frame["em_math_pct" if baseline_name == "straddle" else "hist_move_med_4q"],
        errors="coerce",
    ).to_numpy(dtype=float)
    matched = np.isfinite(baseline) & (baseline >= 0)
    payload = {
        "rows": len(frame),
        "baseline_rows": int(matched.sum()),
        "baseline_name": baseline_name,
        "status": "insufficient_data",
        "evaluation_complete": False,
        "failure_kind": "insufficient_data",
        "issues": [],
    }
    if len(frame) < MINIMUM_ROWS or int(matched.sum()) < MINIMUM_ROWS:
        payload["issues"] = [
            f"require {MINIMUM_ROWS} independent events and matched baseline rows"
        ]
        return payload
    actual = frame["target"].to_numpy(dtype=float)
    point = frame["prediction"].to_numpy(dtype=float)
    quantiles = frame[[f"p{q:02d}" for q in DEFAULT_QUANTILES]].to_numpy(dtype=float)
    if (
        not all(
            np.isfinite(array).all() and (array >= 0).all()
            for array in (actual, point, quantiles)
        )
        or (np.diff(quantiles, axis=1) < 0).any()
    ):
        return {
            **payload,
            "status": "failed",
            "failure_kind": "invalid_evidence",
            "issues": [
                "targets, predictions and quantiles must be finite, nonnegative and monotone"
            ],
        }
    _, coverage = _coverage_error(actual, quantiles)
    targets = {
        "coverage_80": 0.8,
        "coverage_50": 0.5,
        **{f"q{q:02d}_coverage": q / 100 for q in DEFAULT_QUANTILES},
    }
    issues = []
    model_mae = float(np.mean(np.abs(actual[matched] - point[matched])))
    baseline_mae = float(np.mean(np.abs(actual[matched] - baseline[matched])))
    if model_mae >= baseline_mae:
        issues.append(f"candidate does not beat the {baseline_name} baseline")
    if any(abs(coverage[key] - value) > 0.08 for key, value in targets.items()):
        issues.append("candidate interval/quantile calibration exceeds tolerance")
    return {
        **payload,
        "status": "failed" if issues else "passed",
        "evaluation_complete": True,
        "failure_kind": "quality" if issues else None,
        "issues": issues,
        "candidate_mae": model_mae,
        "baseline_mae": baseline_mae,
        "candidate_coverage": coverage,
    }


def evaluate_admission(ledger, labels, candidate, champion, contract, *, now=None):
    """Evaluate an authenticated contract and ledger supplied by the control plane."""
    if contract.get("schema") != SCHEMA or contract.get("minimum_rows") != MINIMUM_ROWS:
        raise ValueError("invalid prospective admission policy")
    start = pd.Timestamp(contract["started_at"])
    if start.tzinfo is None:
        raise ValueError("prospective admission start must include a timezone")
    current = pd.Timestamp(now or datetime.now(timezone.utc))
    candidate_manifest = verify_bundle_dir(candidate) if (candidate / "manifest.json").exists() else None
    if candidate_manifest is not None and set(contract["comparators"]) != {str(h) for h in candidate_manifest["horizons"]}:
        raise ValueError("admission contract does not cover the authenticated model heads")
    report = {
        "status": "passed",
        "method": "predeclared_prospective_cohort_admission",
        "contract": contract,
        "horizons": {},
        "issues": [],
    }
    for key, cohorts in contract["comparators"].items():
        horizon = int(key)
        cm = _metadata(candidate, horizon)
        if set(cohorts) != set(allowed_cohorts(candidate_manifest, horizon, cm)):
            raise ValueError("admission contract does not cover every admitted cohort")
        c = _observations(
            ledger, labels, contract["candidate_bundle_id"], cm, horizon, start, current
        )
        payloads = {}
        for cohort, comparator in cohorts.items():
            frame = c.loc[c["__cohort"].eq(cohort)] if len(c) else c
            baseline_name = (
                "straddle" if cohort == "strict_options" else "historical_median"
            )
            payload = (
                _baseline_metrics(frame, baseline_name)
                if len(frame)
                else {
                    "rows": 0,
                    "status": "insufficient_data",
                    "evaluation_complete": False,
                    "failure_kind": "insufficient_data",
                    "issues": ["no mature independent observations"],
                }
            )
            if comparator == "incumbent" and payload["status"] == "passed":
                hm = _metadata(champion, horizon)
                h = _observations(
                    ledger,
                    labels,
                    contract["champion_bundle_id"],
                    hm,
                    horizon,
                    start,
                    current,
                )
                keys = ["act_symbol", "earnings_date", "snapshot_date", "model_horizon"]
                pair = (
                    frame.merge(h, on=keys, suffixes=("_candidate", "_champion"))
                    if len(h)
                    else pd.DataFrame()
                )
                if len(pair) < MINIMUM_ROWS:
                    payload.update(
                        status="insufficient_data",
                        evaluation_complete=False,
                        failure_kind="insufficient_data",
                        issues=["insufficient matched incumbent observations"],
                    )
                else:
                    qs = [f"p{q:02d}" for q in DEFAULT_QUANTILES]
                    payload = _paired_metrics(
                        pair.target_candidate.to_numpy(),
                        pair.prediction_candidate.to_numpy(),
                        pair.prediction_champion.to_numpy(),
                        pair[[q + "_candidate" for q in qs]].to_numpy(),
                        pair[[q + "_champion" for q in qs]].to_numpy(),
                        pair[
                            "em_math_pct_candidate"
                            if cohort == "strict_options"
                            else "hist_move_med_4q_candidate"
                        ].to_numpy(),
                        baseline_name=baseline_name,
                    )
            elif comparator not in {"incumbent", baseline_name}:
                raise ValueError("admission comparator is incompatible with its cohort")
            payloads[cohort] = payload
            report["issues"].extend(
                f"T-{horizon}/{cohort}: {issue}" for issue in payload["issues"]
            )
        complete = all(p["evaluation_complete"] for p in payloads.values())
        status = (
            "failed"
            if any(p["status"] == "failed" for p in payloads.values())
            else "insufficient_data"
            if any(p["status"] != "passed" for p in payloads.values())
            else "passed"
        )
        report["horizons"][key] = {
            "status": status,
            "evaluation_complete": complete,
            "rows": sum(p["rows"] for p in payloads.values()),
            "cohorts": payloads,
        }
    results = list(report["horizons"].values())
    report["status"] = (
        "failed"
        if any(p["status"] == "failed" for p in results)
        else "insufficient_data"
        if not results or any(p["status"] != "passed" for p in results)
        else "passed"
    )
    report["evaluation_complete"] = bool(results) and all(
        p["evaluation_complete"] for p in results
    )
    report["failure_kind"] = (
        "quality"
        if report["evaluation_complete"] and report["status"] == "failed"
        else None
    )
    return report
