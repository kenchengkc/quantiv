"""Signed admission of an exact frozen model on reserved historical events.

The reservation excludes test labels from this candidate's fitting and selection.
It does not establish that historical model development never saw these events,
or that provider calendar revisions have a retained point-in-time history.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from ml.model_artifact import sha256_file
from ml.model_protocol import CAUSAL_FEATURE_PROTOCOL, feature_protocol, target_protocol
from ml.training_split import eligible_mature_rows, temporal_dates, training_row_digest

SCHEMA = "quantiv.historical-model-admission.v1"
RESERVATION_SCHEMA = "quantiv.historical-test-reservation.v1"
QUANTILES = (10, 25, 50, 75, 90)
COHORTS = ("strict_options", "optionless")
DEFAULT_TEST_DAYS = 180
POLICY = {
    "version": "quantiv.historical-exact-artifact-policy.v1",
    "method": "sealed_temporal_exact_artifact_test",
    "minimum_events": 200,
    "minimum_matched_baseline_rows": 200,
    "coverage_tolerance": .08,
    "diagnostic_blocks": 3,
    "minimum_block_rows": 30,
    "maximum_block_mae_ratio": 1.50,
    "maximum_raw_crossing_rate": .20,
    "maximum_raw_negative_rate": .05,
    "bootstrap_draws": 5000,
    "bootstrap_seed": 17,
    "multiplicity": "bonferroni_over_all_development_declared_horizon_cohort_cells",
    "familywise_alpha": .05,
    "requires_conservative_clustered_ci_upper_below_zero": True,
    "aggregate_significance_claim": False,
}
DISCLOSURES = {
    "historical_model_family_exposure_status": "not_established",
    "calendar_vintage_status": "complete_point_in_time_revision_history_not_established",
    "global_untouched_history_claim": False,
    "scope": "current_exact_candidate_excludes_reserved_labels_from_fit_and_selection",
    "historical_source_availability": "reconstructed_end_of_day_market_observations",
    "historical_provider_delivery_timestamps": "not_retained",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode()


def _identity(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text())
    if not isinstance(result, dict):
        raise ValueError(f"expected an object: {path.name}")
    return result


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n")
    temporary.replace(path)


def _timestamp(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError("invalid temporal evidence")
    return result.tz_convert("UTC").tz_localize(None) if result.tzinfo else result


def _validate_frame(frame: pd.DataFrame, metadata: Mapping[str, Any], horizon: int) -> pd.DataFrame:
    if feature_protocol(metadata) != CAUSAL_FEATURE_PROTOCOL:
        raise ValueError("historical admission requires the causal feature protocol")
    target_version = target_protocol(metadata)
    required = {"target", "__symbol", "__earnings_date", "__snapshot_date", "__label_available_at",
                "__pre_price_date", "__post_price_date", "__target_protocol"}
    if not required.issubset(frame.columns):
        raise ValueError(f"historical input is missing temporal columns: {sorted(required - set(frame.columns))}")
    features = [str(column) for column in frame if column != "target" and not str(column).startswith("__")]
    forbidden = [column for column in features
                 if re.search(r"(^post_|future|realized_move|realized_source|(^|_)target($|_))", column, re.I)]
    if forbidden:
        raise ValueError(f"label-derived or future feature columns: {forbidden}")
    work = frame.copy().reset_index(drop=True)
    work["__earnings_date"] = pd.to_datetime(work["__earnings_date"], utc=True, errors="raise").dt.tz_localize(None)
    snapshots, labels, _ = temporal_dates(work, horizon_days=horizon)
    if not work["__target_protocol"].eq(target_version).all():
        raise ValueError("historical input targets use another target protocol")
    pre = pd.to_datetime(work["__pre_price_date"], utc=True, errors="raise").dt.tz_localize(None)
    post = pd.to_datetime(work["__post_price_date"], utc=True, errors="raise").dt.tz_localize(None)
    if pre.isna().any() or post.isna().any() or (pre >= post).any() or (labels < post).any():
        raise ValueError("historical reaction price dates disagree with label availability")
    if ((work["__earnings_date"].dt.normalize() - snapshots.dt.normalize()).dt.days != horizon).any():
        raise ValueError("historical input snapshot does not match the declared lead time")
    if work["__symbol"].isna().any() or work["__symbol"].astype(str).str.strip().eq("").any():
        raise ValueError("historical input contains missing issuer identities")
    identities = work["__symbol"].astype(str) + "|" + work["__earnings_date"].dt.date.astype(str)
    if identities.duplicated().any():
        raise ValueError("historical input contains duplicate symbol/date events")
    target = pd.to_numeric(work["target"], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(target).all() or (target < 0).any():
        raise ValueError("historical targets must be finite and nonnegative")
    return work.sort_values(["__earnings_date", "__symbol"], kind="mergesort").reset_index(drop=True)


def prepare_historical_admission(
    *, source_training_dir: Path, training_dir: Path, staging_dir: Path,
    horizons: Sequence[int], as_of: str | None = None,
    test_days: int = DEFAULT_TEST_DAYS, min_development_rows: int = 1000,
    source_admission_report: Path | None = None,
    source_data_dir: Path | None = None, reservation_registry: Path | None = None,
    private_key: str | bytes | None = None, public_key: str | bytes | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Copy availability-purged development data without changing full sources."""
    roots = [path.resolve() for path in (source_training_dir, training_dir, staging_dir)]
    if any(a == b or a in b.parents or b in a.parents for i, a in enumerate(roots) for b in roots[i + 1:]):
        raise ValueError("source, development and sealed-test directories must be disjoint")
    if test_days < 1 or min_development_rows < 1:
        raise ValueError("test window and development minimum must be positive")
    if not horizons or len(set(horizons)) != len(horizons):
        raise ValueError("reservation horizons must be nonempty and unique")
    for directory in (training_dir, staging_dir):
        if directory.exists() and any(directory.iterdir()):
            raise ValueError("historical reservation output already exists; use a fresh directory")
    cutoff = _timestamp(as_of or datetime.now(timezone.utc).isoformat())
    from ml.model_bundle import _sign, _public_from_private
    if source_data_dir is None or reservation_registry is None:
        raise ValueError("historical preparation requires actual source data and durable reservation registry")
    public_key = public_key or _public_from_private(private_key)
    try:
        from verify_historical_training_gate import verify_historical_training_gate
    except ModuleNotFoundError:
        from scripts.verify_historical_training_gate import verify_historical_training_gate
    verified_source = verify_historical_training_gate(data_dir=source_data_dir)
    if source_admission_report is not None and _read(source_admission_report) != verified_source:
        raise ValueError("source admission report differs from verified immutable source data")
    source_report_bytes = (source_admission_report.read_bytes() if source_admission_report is not None
                           else json.dumps(verified_source, indent=2, sort_keys=True).encode() + b"\n")
    source_admission = {"sha256": hashlib.sha256(source_report_bytes).hexdigest(), "report": verified_source}
    _verify_source_admission(source_admission)
    prepared = {}
    staged = []
    panel_events = {}
    sources = {}
    for horizon in sorted(horizons):
        source = source_training_dir / f"training_T{horizon}.parquet"
        meta = _read(source_training_dir / f"metadata_T{horizon}.json")
        full = _validate_frame(pd.read_parquet(source), meta, horizon)
        mature, snapshots, labels = eligible_mature_rows(full, as_of=cutoff, horizon_days=horizon)
        if mature.empty:
            raise ValueError(f"T-{horizon} has no mature historical rows")
        if (snapshots > cutoff).any():
            raise ValueError("historical features have future snapshot dates")
        sources[horizon] = (source, meta, full, mature, snapshots, labels)
    test_end = max(values[3]["__earnings_date"].max().normalize() for values in sources.values())
    test_start = test_end - pd.Timedelta(days=test_days - 1)
    test_frames = {}
    for horizon, (_, _, _, mature, snapshots, _) in sources.items():
        test = mature.loc[(mature["__earnings_date"] >= test_start)
                          & (mature["__earnings_date"] < test_end + pd.Timedelta(days=1))].copy()
        if test.empty:
            raise ValueError(f"T-{horizon} has no events in the common reserved calendar window")
        test_frames[horizon] = test
    global_snapshot = min(sources[h][4].loc[test.index].min() for h, test in test_frames.items())
    global_boundary = {"calendar_start": test_start.isoformat(), "calendar_end": test_end.isoformat(),
                       "first_test_snapshot": global_snapshot.isoformat(),
                       "selection_label_cutoff": global_snapshot.isoformat(),
                       "rule": "all_requested_heads_development_labels_strictly_before_earliest_reserved_snapshot"}
    for horizon in sorted(horizons):
        source, meta, full, mature, snapshots, labels = sources[horizon]
        test = test_frames[horizon]
        for event in test.to_dict(orient="records"):
            event_id = str(event["__symbol"]) + "|" + pd.Timestamp(event["__earnings_date"]).date().isoformat()
            record = {"event_id": event_id, "target_hex": float(event["target"]).hex(),
                      "target_protocol": target_protocol(meta)}
            if event_id in panel_events and panel_events[event_id] != record:
                raise ValueError("reserved horizons disagree on an event target")
            panel_events[event_id] = record
        first_snapshot = snapshots.loc[test.index].min()
        dev = mature.loc[(mature["__earnings_date"] < test_start) & (labels < global_snapshot)].copy()
        if len(dev) < min_development_rows:
            raise ValueError(f"T-{horizon} has only {len(dev)} development rows")
        row = {
            "horizon_days": horizon, "source_sha256": sha256_file(source),
            "source_metadata_sha256": sha256_file(source_training_dir / f"metadata_T{horizon}.json"),
            "source_rows": len(full), "immature_rows_excluded": len(full) - len(mature),
            "development_rows": len(dev), "development_row_digest": training_row_digest(dev),
            "development_label_available_end": labels.loc[dev.index].max().isoformat(),
            "first_test_snapshot": first_snapshot.isoformat(),
            "selection_label_cutoff": global_snapshot.isoformat(),
            "test_start": test["__earnings_date"].min().isoformat(),
            "test_end": test["__earnings_date"].max().isoformat(), "test_rows": len(test),
            "purged_rows": len(mature) - len(dev) - len(test),
            "test_file": f"final_test_T{horizon}.parquet",
            "feature_protocol": feature_protocol(meta), "target_protocol": target_protocol(meta),
        }
        staged.append((horizon, dev, test, meta, row))
        prepared[str(horizon)] = row
    training_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    for horizon, dev, test, meta, row in staged:
        dev_path = training_dir / f"training_T{horizon}.parquet"
        test_path = staging_dir / row["test_file"]
        dev.to_parquet(dev_path, index=False)
        test.to_parquet(test_path, index=False)
        row["development_sha256"] = sha256_file(dev_path)
        row["test_sha256"] = sha256_file(test_path)
        feature_columns = [column for column in dev if column != "target" and not column.startswith("__")]
        straddle = pd.to_numeric(dev.get("straddle_pct", pd.Series(np.nan, index=dev.index)), errors="coerce")
        strict = np.isfinite(straddle) & (straddle > 0)
        meta.update({"n_samples": len(dev), "historical_test_reserved": True,
                     "source_n_samples_before_historical_test": row["source_rows"],
                     "feature_cols": feature_columns,
                     "cohort_counts": {"strict_options": int(strict.sum()), "optionless": int((~strict).sum())},
                     "target_mean": float(dev["target"].mean()), "target_median": float(dev["target"].median()),
                     "target_std": float(dev["target"].std()), "target_p10": float(dev["target"].quantile(.10)),
                     "target_p90": float(dev["target"].quantile(.90)),
                     "historical_preparation_as_of": cutoff.isoformat(),
                     "historical_development_label_available_end": row["development_label_available_end"],
                     "historical_first_test_snapshot": row["first_test_snapshot"],
                     "historical_global_selection_label_cutoff": global_snapshot.isoformat()})
        for feature, field in (("event_move_implied", "event_move_coverage"), ("hist_move_avg_4q", "hist_move_coverage"),
                               ("hist_move_p75_8q", "hist_dist_coverage")):
            if feature in dev:
                meta[field] = float(dev[feature].notna().mean())
        if "__label_source" in dev:
            meta["ohlcv_coverage"] = float(dev["__label_source"].isin(["ohlcv", "ohlcv_session_close"]).mean())
        if straddle.notna().any():
            meta["straddle_pct_mean"] = float(straddle.mean())
        else:
            meta["straddle_pct_mean"] = None
        _write(training_dir / f"metadata_T{horizon}.json", meta)
        row["development_metadata_sha256"] = sha256_file(training_dir / f"metadata_T{horizon}.json")
    (staging_dir / "historical_training_admission.json").write_bytes(source_report_bytes)
    event_records = [panel_events[key] for key in sorted(panel_events)]
    panel_id = _identity({"events": event_records, "feature_protocol": CAUSAL_FEATURE_PROTOCOL})
    core = {"schema": RESERVATION_SCHEMA, "as_of": cutoff.isoformat(),
            "test_days": test_days, "horizons": prepared, "source_admission": source_admission,
            "panel_id": panel_id, "panel_events": event_records,
            "global_temporal_boundary": global_boundary,
            "protocol": {**DISCLOSURES, "method": POLICY["method"],
                         "availability_rule": "all_development_labels_strictly_before_global_first_test_snapshot",
                         "source_admission_verified": source_admission is not None,
                         "source_training_unchanged": True}}
    reservation = _sign({**core, "reservation_id": _identity(core)}, private_key)
    panel_path = reservation_registry / "panels" / (panel_id.removeprefix("sha256:") + ".json")
    _immutable_signed_file(panel_path, reservation, public_key=public_key)
    path = staging_dir / "reservation.json"
    _write(path, reservation)
    return path, reservation


def _cluster_intervals(difference: np.ndarray, clusters: pd.Series, *, multiplicity: int, seed: int) -> tuple[list[float], list[float], int]:
    codes, names = pd.factorize(clusters.astype(str), sort=True)
    if len(names) < 2:
        raise ValueError("cluster bootstrap requires at least two clusters")
    totals = np.bincount(codes, weights=difference)
    counts = np.bincount(codes)
    rng = np.random.default_rng(seed)
    draws = int(POLICY["bootstrap_draws"])
    boot = np.empty(draws)
    for start in range(0, draws, 128):
        size = min(128, draws - start)
        sampled = rng.integers(0, len(names), size=(size, len(names)))
        boot[start:start + size] = totals[sampled].sum(axis=1) / counts[sampled].sum(axis=1)
    tail = .025 / multiplicity
    adjusted = ([float(boot.min()), float(boot.max())] if draws * tail < 5
                else np.quantile(boot, [tail, 1 - tail]).tolist())
    return np.quantile(boot, [.025, .975]).tolist(), adjusted, len(names)


def assess_historical_cell(frame: pd.DataFrame, *, multiplicity: int = 1) -> dict[str, Any]:
    """Grade one predeclared horizon/cohort using its frozen six-head output."""
    if multiplicity < 1:
        raise ValueError("multiplicity must be positive")
    if frame["event_id"].duplicated().any():
        raise ValueError("historical assessment contains duplicate calendar event identities")
    report: dict[str, Any] = {"status": "insufficient_data", "rows": len(frame),
                              "events": int(frame["event_id"].nunique()), "issues": []}
    baseline = pd.to_numeric(frame["baseline"], errors="coerce").to_numpy(dtype=float)
    matched = np.isfinite(baseline) & (baseline >= 0)
    report["baseline_rows"] = int(matched.sum())
    if report["events"] < POLICY["minimum_events"] or matched.sum() < POLICY["minimum_matched_baseline_rows"]:
        report["issues"] = ["require 200 distinct events and 200 matched baseline rows"]
        return report
    actual = frame["actual"].to_numpy(dtype=float)
    point = frame["point"].to_numpy(dtype=float)
    quantiles = frame[[f"q{q:02d}" for q in QUANTILES]].to_numpy(dtype=float)
    raw = frame[[f"raw_q{q:02d}" for q in QUANTILES]].to_numpy(dtype=float)
    issues = report["issues"]
    if not all(np.isfinite(values).all() for values in (actual, point, quantiles, raw)) or any((values < 0).any() for values in (actual, point, quantiles)):
        return {**report, "status": "failed", "issues": ["predictions/targets must be finite and served values nonnegative"]}
    if (np.diff(quantiles, axis=1) < 0).any():
        issues.append("served quantiles are not monotone")
    report["quantile_crossing_rate_raw"] = float((np.diff(raw, axis=1) < 0).any(axis=1).mean())
    report["quantile_negative_rate_raw"] = float((raw < 0).any(axis=1).mean())
    if report["quantile_crossing_rate_raw"] > POLICY["maximum_raw_crossing_rate"]:
        issues.append("raw quantile crossing exceeds limit")
    if report["quantile_negative_rate_raw"] > POLICY["maximum_raw_negative_rate"]:
        issues.append("raw negative quantile rate exceeds limit")
    coverage = {f"q{q:02d}_coverage": float((actual <= quantiles[:, i]).mean()) for i, q in enumerate(QUANTILES)}
    coverage.update({"coverage_80": float(((actual >= quantiles[:, 0]) & (actual <= quantiles[:, 4])).mean()),
                     "coverage_50": float(((actual >= quantiles[:, 1]) & (actual <= quantiles[:, 3])).mean())})
    targets = {"coverage_80": .8, "coverage_50": .5, **{f"q{q:02d}_coverage": q / 100 for q in QUANTILES}}
    report["coverage"] = coverage
    report["pinball"] = {f"q{q:02d}": float(np.maximum((q / 100) * (actual - quantiles[:, i]),
                                             (q / 100 - 1) * (actual - quantiles[:, i])).mean())
                         for i, q in enumerate(QUANTILES)}
    if any(abs(coverage[key] - target) > POLICY["coverage_tolerance"] + 1e-12 for key, target in targets.items()):
        issues.append("interval or quantile calibration exceeds tolerance")
    error = np.abs(actual[matched] - point[matched])
    baseline_error = np.abs(actual[matched] - baseline[matched])
    report.update({"model_mae": float(error.mean()), "baseline_mae": float(baseline_error.mean()),
                   "mean_absolute_error_difference": float((error - baseline_error).mean())})
    if report["model_mae"] >= report["baseline_mae"]:
        issues.append("model MAE does not beat matched baseline")
    ordinary, family, clusters = {}, {}, {}
    paired = frame.loc[matched]
    for name, labels in (("issuer_clustered", paired["issuer"]),
                         ("earnings_week_clustered", pd.to_datetime(paired["earnings_date"]).dt.to_period("W-SUN").astype(str))):
        try:
            ordinary[name], family[name], clusters[name] = _cluster_intervals(error - baseline_error, labels,
                multiplicity=multiplicity, seed=int(POLICY["bootstrap_seed"]) + len(ordinary))
        except ValueError as exc:
            issues.append(str(exc))
    if len(ordinary) == 2:
        for intervals in (ordinary, family):
            intervals["conservative_envelope"] = [min(value[0] for value in intervals.values()), max(value[1] for value in intervals.values())]
        report.update({"mae_difference_95_ci": ordinary, "familywise_mae_difference_ci": family,
                       "bootstrap_clusters": clusters})
        if family["conservative_envelope"][1] >= 0:
            issues.append("familywise conservative clustered MAE difference interval includes zero")
    ordered = frame.sort_values(["earnings_date", "event_id"], kind="mergesort")
    dates = pd.to_datetime(ordered["earnings_date"])
    start, end = dates.min(), dates.max() + pd.Timedelta(days=1)
    edges = [start + (end - start) * fraction / 3 for fraction in range(4)]
    blocks = []
    for index in range(3):
        block = ordered.loc[(dates >= edges[index]) & (dates < edges[index + 1])]
        b = block["baseline"].to_numpy(dtype=float)
        valid = np.isfinite(b) & (b >= 0)
        row: dict[str, Any] = {"start": edges[index].isoformat(), "end_exclusive": edges[index + 1].isoformat(),
                                "rows": len(block), "baseline_rows": int(valid.sum()), "status": "low_sample"}
        if len(block):
            actual_block = block["actual"].to_numpy(dtype=float)
            bands = block[[f"q{q:02d}" for q in QUANTILES]].to_numpy(dtype=float)
            raw_bands = block[[f"raw_q{q:02d}" for q in QUANTILES]].to_numpy(dtype=float)
            row["coverage"] = {f"q{q:02d}_coverage": float((actual_block <= bands[:, i]).mean())
                               for i, q in enumerate(QUANTILES)}
            row["coverage"].update({"coverage_80": float(((actual_block >= bands[:, 0]) & (actual_block <= bands[:, 4])).mean()),
                                    "coverage_50": float(((actual_block >= bands[:, 1]) & (actual_block <= bands[:, 3])).mean())})
            row["pinball"] = {f"q{q:02d}": float(np.maximum((q / 100) * (actual_block - bands[:, i]),
                                                  (q / 100 - 1) * (actual_block - bands[:, i])).mean())
                              for i, q in enumerate(QUANTILES)}
            row["quantile_crossing_rate_raw"] = float((np.diff(raw_bands, axis=1) < 0).any(axis=1).mean())
            row["quantile_negative_rate_raw"] = float((raw_bands < 0).any(axis=1).mean())
        if valid.sum() >= POLICY["minimum_block_rows"]:
            y = block["actual"].to_numpy(dtype=float)[valid]
            model_mae = float(np.abs(y - block["point"].to_numpy(dtype=float)[valid]).mean())
            baseline_mae = float(np.abs(y - b[valid]).mean())
            ratio = model_mae / baseline_mae if baseline_mae else (0. if model_mae == 0 else None)
            row.update({"model_mae": model_mae, "baseline_mae": baseline_mae, "mae_ratio": ratio, "status": "reported"})
            if ratio is None or ratio > POLICY["maximum_block_mae_ratio"]:
                issues.append(f"chronological block {index + 1} MAE ratio exceeds limit")
        else:
            issues.append(f"chronological block {index + 1} has only {int(valid.sum())} matched baseline rows; require {POLICY['minimum_block_rows']}")
        blocks.append(row)
    report["blocks"] = blocks
    report["status"] = "failed" if issues else "passed"
    return report


def _verify_reservation(reservation: Mapping[str, Any], *, public_key=None) -> None:
    from ml.model_bundle import verify_signed_payload
    reservation = verify_signed_payload(reservation, public_key=public_key)
    if reservation.get("schema") != RESERVATION_SCHEMA:
        raise ValueError("unsupported historical reservation schema")
    core = {key: value for key, value in reservation.items() if key != "reservation_id"}
    if reservation.get("reservation_id") != _identity(core):
        raise ValueError("historical reservation identity mismatch")
    if not reservation.get("horizons") or reservation.get("protocol", {}).get("source_training_unchanged") is not True:
        raise ValueError("historical reservation is missing source/split evidence")
    events = reservation.get("panel_events")
    if not isinstance(events, list) or not events or len({row["event_id"] for row in events}) != len(events):
        raise ValueError("historical reservation lacks unique precommitted event membership")
    if reservation.get("panel_id") != _identity({"events": events, "feature_protocol": CAUSAL_FEATURE_PROTOCOL}):
        raise ValueError("historical reservation panel identity mismatch")
    _verify_global_boundary(reservation)


def _verify_global_boundary(reservation: Mapping[str, Any]) -> pd.Timestamp:
    boundary = reservation.get("global_temporal_boundary") or {}
    if boundary.get("rule") != "all_requested_heads_development_labels_strictly_before_earliest_reserved_snapshot":
        raise ValueError("historical reservation lacks the global candidate-family temporal boundary")
    start, end = _timestamp(boundary.get("calendar_start")), _timestamp(boundary.get("calendar_end"))
    cutoff = _timestamp(boundary.get("first_test_snapshot"))
    rows = list(reservation["horizons"].values())
    if (end - start != pd.Timedelta(days=int(reservation["test_days"]) - 1)
            or boundary.get("selection_label_cutoff") != cutoff.isoformat()
            or min(_timestamp(row["first_test_snapshot"]) for row in rows) != cutoff
            or max(_timestamp(row["test_end"]).normalize() for row in rows) != end):
        raise ValueError("historical global calendar/snapshot boundary disagrees with reserved heads")
    for row in rows:
        if (_timestamp(row["test_start"]) < start or _timestamp(row["test_end"]) >= end + pd.Timedelta(days=1)
                or _timestamp(row["development_label_available_end"]) >= cutoff
                or row.get("selection_label_cutoff") != cutoff.isoformat()):
            raise ValueError("historical development/test head crosses the global reserved boundary")
    return cutoff


def _verify_source_admission(binding: Mapping[str, Any]) -> None:
    report = binding.get("report") or {}
    calendar = report.get("calendar_source") or {}
    actions = report.get("corporate_actions") or {}
    digests = (binding.get("sha256"), report.get("release_id"), report.get("earnings_calendar_sha256"),
               calendar.get("sha256"), actions.get("receipt_id"))
    if (report.get("schema") != "quantiv.historical-training-admission.v1" or report.get("status") != "passed"
            or calendar.get("path") not in {"earnings_calendar.csv", "earnings_calendar.parquet"}
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in digests)):
        raise ValueError("historical admission requires a passed immutable source admission report")


def _verify_fit(metadata: Mapping[str, Any], development: pd.DataFrame, test: pd.DataFrame,
                split: Mapping[str, Any], horizon: int, *, global_cutoff: pd.Timestamp) -> None:
    digest = training_row_digest(development)
    fit, exposure = metadata.get("final_fit") or {}, metadata.get("selection_exposure") or {}
    if fit.get("rows") != len(development) or fit.get("row_digest") != digest or split.get("development_row_digest") != digest:
        raise ValueError(f"T-{horizon} exact final fit does not match reserved development rows")
    if exposure.get("row_digest") != digest:
        raise ValueError(f"T-{horizon} selection exposure does not match development rows")
    snapshots, _, _ = temporal_dates(test, horizon_days=horizon)
    first_snapshot = snapshots.min()
    cutoff = global_cutoff
    _, labels, _ = temporal_dates(development, horizon_days=horizon)
    if labels.max() >= cutoff:
        raise ValueError(f"T-{horizon} development labels overlap test prediction snapshots")
    for label, value in (("final fit", fit.get("label_available_end")), ("selection exposure", exposure.get("through_date"))):
        if not value or _timestamp(value) >= cutoff or _timestamp(value) < labels.max():
            raise ValueError(f"T-{horizon} {label} cutoff is missing or overlaps reserved snapshots")
    if first_snapshot.isoformat() != split.get("first_test_snapshot"):
        raise ValueError("reserved first test snapshot changed")


def _reserved_family_inputs(reservation: Mapping[str, Any], training_dir: Path,
                            test_dir: Path, *, retained: bool = False) -> dict[int, tuple]:
    """Verify every requested head, including heads excluded by development gates."""
    global_cutoff = _verify_global_boundary(reservation)
    boundary = reservation["global_temporal_boundary"]
    start, end = _timestamp(boundary["calendar_start"]), _timestamp(boundary["calendar_end"])
    inputs, events, first_snapshots = {}, {}, []
    for key, split in reservation["horizons"].items():
        horizon = int(key)
        dev_path = training_dir / f"{'development' if retained else 'training'}_T{horizon}.parquet"
        metadata_path = training_dir / f"{'training_metadata' if retained else 'metadata'}_T{horizon}.json"
        if split.get("test_file") != f"final_test_T{horizon}.parquet":
            raise ValueError("unsafe historical family test path")
        test_path = test_dir / split["test_file"]
        for path, field in ((dev_path, "development_sha256"), (test_path, "test_sha256"),
                            (metadata_path, "development_metadata_sha256")):
            if sha256_file(path) != split[field]:
                raise ValueError("reserved candidate-family development/test input bytes changed")
        metadata = _read(metadata_path)
        dev = _validate_frame(pd.read_parquet(dev_path), metadata, horizon)
        test = _validate_frame(pd.read_parquet(test_path), metadata, horizon)
        if (len(dev) != split["development_rows"] or len(test) != split["test_rows"]
                or training_row_digest(dev) != split["development_row_digest"] or test.empty):
            raise ValueError("reserved candidate-family row identities or counts changed")
        snapshots, labels, _ = temporal_dates(dev, horizon_days=horizon)
        if labels.max() >= global_cutoff or labels.max().isoformat() != split["development_label_available_end"]:
            raise ValueError("candidate-family development labels cross the global test snapshot cutoff")
        test_snapshots, test_labels, _ = temporal_dates(test, horizon_days=horizon)
        if (test["__earnings_date"].min() < start or test["__earnings_date"].max() >= end + pd.Timedelta(days=1)
                or test_snapshots.min().isoformat() != split["first_test_snapshot"]
                or (test_labels > _timestamp(reservation["as_of"])).any()):
            raise ValueError("candidate-family test rows disagree with the global temporal boundary")
        first_snapshots.append(test_snapshots.min())
        for event in test.to_dict(orient="records"):
            event_id = str(event["__symbol"]) + "|" + pd.Timestamp(event["__earnings_date"]).date().isoformat()
            record = {"event_id": event_id, "target_hex": float(event["target"]).hex(),
                      "target_protocol": target_protocol(metadata)}
            if event_id in events and events[event_id] != record:
                raise ValueError("reserved candidate-family event labels disagree across heads")
            events[event_id] = record
        inputs[horizon] = (dev_path, test_path, metadata_path, dev, test)
    if min(first_snapshots) != global_cutoff or [events[key] for key in sorted(events)] != reservation["panel_events"]:
        raise ValueError("candidate-family inputs disagree with the precommitted global test panel")
    return inputs


def _native_predictions(bundle_dir: Path, horizon: int, frame: pd.DataFrame,
                        metadata: Mapping[str, Any]) -> pd.DataFrame:
    from ml.model_artifact import load_native_model, point_model_name, quantile_model_name
    from ml.quantiles import rearrange_quantile_array

    features = metadata.get("feature_cols")
    if not isinstance(features, list) or not features or len(set(features)) != len(features):
        raise ValueError("candidate metadata has no unique feature schema")
    if not set(features).issubset(frame.columns) or any(name == "target" or name.startswith("__") for name in features):
        raise ValueError("candidate feature schema contains missing or label/sidecar columns")
    X = frame[features].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    models = [load_native_model(bundle_dir / point_model_name(horizon)),
              *[load_native_model(bundle_dir / quantile_model_name(horizon, q)) for q in QUANTILES]]
    if any(model.feature_name() != features for model in models):
        raise ValueError("native candidate feature names differ from signed metadata")
    raw_point = np.asarray(models[0].predict(X), dtype=float)
    raw = np.column_stack([model.predict(X) for model in models[1:]])
    if not np.isfinite(raw_point).all() or not np.isfinite(raw).all():
        raise ValueError("native candidate emits non-finite predictions")
    quantiles = rearrange_quantile_array(raw)
    straddle = pd.to_numeric(frame.get("straddle_pct", pd.Series(np.nan, index=frame.index)), errors="coerce").to_numpy(dtype=float)
    strict = np.isfinite(straddle) & (straddle > 0)
    historical = pd.to_numeric(frame.get("hist_move_med_4q", pd.Series(np.nan, index=frame.index)), errors="coerce").to_numpy(dtype=float)
    dates = pd.to_datetime(frame["__earnings_date"])
    return pd.DataFrame({
        "event_id": frame["__symbol"].astype(str) + "|" + dates.dt.date.astype(str),
        "issuer": frame["__symbol"].astype(str), "earnings_date": dates,
        "snapshot_date": pd.to_datetime(frame["__snapshot_date"]),
        "actual": frame["target"].to_numpy(dtype=float), "point": np.clip(raw_point, 0., None),
        "raw_point": raw_point, "baseline": np.where(strict, straddle, historical),
        "cohort": np.where(strict, "strict_options", "optionless"),
        **{f"q{q:02d}": quantiles[:, index] for index, q in enumerate(QUANTILES)},
        **{f"raw_q{q:02d}": raw[:, index] for index, q in enumerate(QUANTILES)},
    }).reset_index(drop=True)


def _verify_model_receipt(receipt: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    from ml.evidence_receipt import verify_evidence_receipt

    verified = verify_evidence_receipt(receipt, expected_scope="models")
    if verified["receipt_id"] != manifest["receipt_id"] or verified["quality"].get("status") != "passed":
        raise ValueError("historical admission model validation receipt disagrees with signed bundle")
    if sorted(verified["horizons"]) != sorted(manifest["horizons"]):
        raise ValueError("historical admission model validation horizons differ from signed bundle")
    model_sets = [item for item in verified["artifacts"] if item.get("name") == "model_bundle"]
    training_sets = [item for item in verified["artifacts"] if item.get("name") == "training_bundle"]
    if len(model_sets) != 1 or len(training_sets) != 1:
        raise ValueError("historical admission requires unique validated model and training bundles")
    members = model_sets[0]["members"]
    names = [Path(str(item["path"])).name for item in members]
    signed = {item["name"]: item for item in manifest["artifacts"]}
    if len(names) != len(set(names)) or set(names) != set(signed):
        raise ValueError("validated model artifact names differ from signed candidate")
    for name, item in zip(names, members):
        if any(item[key] != signed[name][key] for key in ("sha256", "bytes")):
            raise ValueError("validated model artifacts differ from signed candidate")
    return verified


def _immutable_signed_file(path: Path, payload: Mapping[str, Any], *, public_key=None) -> dict[str, Any]:
    from ml.model_bundle import verify_signed_payload
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        prior = _read(path)
        if verify_signed_payload(prior, public_key=public_key) != verify_signed_payload(payload, public_key=public_key):
            raise ValueError("durable historical reservation/claim already binds different contents")
    return _read(path)


def _native_candidate_id(bundle_dir: Path, manifest: Mapping[str, Any]) -> str:
    recipes = {}
    for horizon in manifest["horizons"]:
        metadata = _read(bundle_dir / f"metadata_T{horizon}.json")
        recipes[str(horizon)] = {key: metadata.get(key) for key in (
            "feature_cols", "feature_protocol", "target_protocol", "supported_cohorts",
            "selection_recipe", "selected_iterations", "time_decay_years")}
        recipes[str(horizon)]["fit_row_digest"] = (metadata.get("final_fit") or {}).get("row_digest")
    return _identity({"artifacts": [row for row in manifest["artifacts"] if row["name"].endswith(".txt")],
                      "recipes": recipes})


def claim_historical_candidate(bundle_dir: Path, reservation_path: Path, reservation_registry: Path,
                               private_key=None, public_key=None) -> tuple[Path, dict[str, Any]]:
    """Commit model identity durably before any reserved outcomes are evaluated."""
    from ml.model_bundle import _sign, _public_from_private, verify_bundle_dir, verify_signed_payload
    public_key = public_key or _public_from_private(private_key)
    manifest = verify_bundle_dir(bundle_dir, public_key=public_key)
    reservation = _read(reservation_path)
    _verify_reservation(reservation, public_key=public_key)
    panel_id = reservation["panel_id"]
    panel_path = reservation_registry / "panels" / (panel_id.removeprefix("sha256:") + ".json")
    if not panel_path.is_file() or _read(panel_path) != reservation:
        raise ValueError("historical reservation is not the durable authenticated precommit")
    native_id = _native_candidate_id(bundle_dir, manifest)
    event_ids = sorted(row["event_id"] for row in reservation["panel_events"])
    claim_core = {"schema": "quantiv.historical-panel-claim.v1", "panel_id": panel_id,
                  "native_candidate_id": native_id, "event_ids": event_ids}
    for path in sorted((reservation_registry / "claims").glob("*.json")):
        prior = verify_signed_payload(_read(path), public_key=public_key)
        if prior.get("schema") != claim_core["schema"]:
            raise ValueError("unsupported durable historical claim schema")
        if set(prior.get("event_ids", [])).intersection(event_ids) and prior.get("native_candidate_id") != native_id:
            raise ValueError("historical event outcomes were already claimed by a different candidate")
    destination = reservation_registry / "claims" / (panel_id.removeprefix("sha256:") + ".json")
    payload = _sign(claim_core, private_key)
    result = _immutable_signed_file(destination, payload, public_key=public_key)
    return destination, result


def _check_passed_metrics(cell: Mapping[str, Any]) -> None:
    from ml.model_bundle import ModelBundleError

    def finite(value: Any) -> bool:
        return type(value) in (int, float) and np.isfinite(value)

    def require(condition: bool) -> None:
        if not condition:
            raise ModelBundleError("historical admission passed cell lacks qualifying metrics")

    require(cell.get("issues") == [])
    require(type(cell.get("events")) is int and cell["events"] >= POLICY["minimum_events"])
    require(type(cell.get("baseline_rows")) is int and cell["baseline_rows"] >= POLICY["minimum_matched_baseline_rows"])
    require(finite(cell.get("model_mae")) and finite(cell.get("baseline_mae")))
    require(0 <= cell["model_mae"] < cell["baseline_mae"])
    for name in ("mae_difference_95_ci", "familywise_mae_difference_ci"):
        intervals = cell.get(name) or {}
        require(set(intervals) == {"issuer_clustered", "earnings_week_clustered", "conservative_envelope"})
        for interval in intervals.values():
            require(isinstance(interval, list) and len(interval) == 2 and all(finite(x) for x in interval))
            require(interval[0] <= interval[1] < 0)
        require(intervals["conservative_envelope"] == [min(intervals[key][0] for key in ("issuer_clustered", "earnings_week_clustered")),
                                                        max(intervals[key][1] for key in ("issuer_clustered", "earnings_week_clustered"))])
    targets = {"coverage_80": .8, "coverage_50": .5, **{f"q{q:02d}_coverage": q / 100 for q in QUANTILES}}
    coverage = cell.get("coverage") or {}
    require(set(coverage) == set(targets))
    require(all(finite(coverage[key]) and 0 <= coverage[key] <= 1
                and abs(coverage[key] - target) <= POLICY["coverage_tolerance"] + 1e-12
                for key, target in targets.items()))
    for key, limit in (("quantile_crossing_rate_raw", "maximum_raw_crossing_rate"),
                       ("quantile_negative_rate_raw", "maximum_raw_negative_rate")):
        require(finite(cell.get(key)) and 0 <= cell[key] <= POLICY[limit])
    require(set(cell.get("pinball") or {}) == {f"q{q:02d}" for q in QUANTILES})
    require(all(finite(value) and value >= 0 for value in cell["pinball"].values()))
    blocks = cell.get("blocks")
    require(isinstance(blocks, list) and len(blocks) == POLICY["diagnostic_blocks"])
    for block in blocks:
        count = block.get("baseline_rows")
        require(type(count) is int and count >= POLICY["minimum_block_rows"])
        require(block.get("status") == "reported" and finite(block.get("mae_ratio")))
        require(0 <= block["mae_ratio"] <= POLICY["maximum_block_mae_ratio"])


def evaluate_historical_admission(
    bundle_dir: Path, reservation_path: Path, model_receipt: Mapping[str, Any],
    training_dir: Path, output_dir: Path, *, private_key: str | bytes | None = None,
    public_key: str | bytes | None = None,
    reservation_registry: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Score an exact native candidate before promotion and sign its capabilities."""
    from ml.model_bundle import _sign, verify_bundle_dir

    manifest = verify_bundle_dir(bundle_dir, public_key=public_key)
    verified_receipt = _verify_model_receipt(model_receipt, manifest)
    reservation = _read(reservation_path)
    _verify_reservation(reservation, public_key=public_key)
    source_admission = reservation.get("source_admission")
    if not isinstance(source_admission, Mapping):
        raise ValueError("historical evaluation requires a verified source admission binding")
    _verify_source_admission(source_admission)
    source_admission_path = reservation_path.parent / "historical_training_admission.json"
    if sha256_file(source_admission_path) != source_admission["sha256"] or _read(source_admission_path) != source_admission["report"]:
        raise ValueError("historical source admission bytes changed after reservation")
    if not {str(h) for h in manifest["horizons"]}.issubset(reservation["horizons"]):
        raise ValueError("signed candidate horizons are outside the pretest reservation")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("historical evaluation output already exists; use a fresh directory")
    metadata = {h: _read(bundle_dir / f"metadata_T{h}.json") for h in manifest["horizons"]}
    declared = {str(h): metadata[h].get("supported_cohorts", []) for h in manifest["horizons"]}
    if any(not isinstance(values, list) or len(values) != len(set(values)) or not set(values).issubset(COHORTS) for values in declared.values()):
        raise ValueError("candidate declares invalid cohort capabilities")
    multiplicity = max(1, sum(len(values) for values in declared.values()))
    training_bundle = next(item for item in verified_receipt["artifacts"] if item["name"] == "training_bundle")
    training_members = {Path(item["path"]).name: item for item in training_bundle["members"]}
    if len(training_members) != len(training_bundle["members"]):
        raise ValueError("duplicate validated development training members")
    if reservation_registry is None:
        raise ValueError("historical evaluation requires the durable reservation registry")
    binding_path = reservation_registry / "claims" / (reservation["panel_id"].removeprefix("sha256:") + ".json")
    if not binding_path.is_file():
        raise ValueError("historical candidate must be durably claimed before evaluation")
    from ml.model_bundle import verify_signed_payload
    claim = _read(binding_path)
    verified_claim = verify_signed_payload(claim, public_key=public_key)
    expected_claim = {"schema": "quantiv.historical-panel-claim.v1", "panel_id": reservation["panel_id"],
                      "native_candidate_id": _native_candidate_id(bundle_dir, manifest),
                      "event_ids": sorted(row["event_id"] for row in reservation["panel_events"])}
    panel_path = reservation_registry / "panels" / (reservation["panel_id"].removeprefix("sha256:") + ".json")
    if verified_claim != expected_claim or not panel_path.is_file() or _read(panel_path) != reservation:
        raise ValueError("historical reservation/candidate differs from durable precommitted registry")
    family_inputs = _reserved_family_inputs(reservation, training_dir, reservation_path.parent)
    global_cutoff = _verify_global_boundary(reservation)
    staged = []
    for horizon in manifest["horizons"]:
        split = reservation["horizons"][str(horizon)]
        dev_path = training_dir / f"training_T{horizon}.parquet"
        test_name = str(split.get("test_file", ""))
        if test_name != f"final_test_T{horizon}.parquet":
            raise ValueError("unsafe historical test reservation path")
        test_path = reservation_path.parent / test_name
        if sha256_file(dev_path) != split["development_sha256"] or sha256_file(test_path) != split["test_sha256"]:
            raise ValueError("reserved development or test bytes changed")
        if sha256_file(training_dir / f"metadata_T{horizon}.json") != split["development_metadata_sha256"]:
            raise ValueError("reserved development feature metadata changed")
        for name in (dev_path.name, f"metadata_T{horizon}.json"):
            member = training_members.get(name)
            current = training_dir / name
            if not member or sha256_file(current) != member["sha256"] or current.stat().st_size != member["bytes"]:
                raise ValueError("candidate training differs from validated development bytes")
        dev = _validate_frame(pd.read_parquet(dev_path), metadata[horizon], horizon)
        test = _validate_frame(pd.read_parquet(test_path), metadata[horizon], horizon)
        if len(test) != split["test_rows"] or len(dev) != split["development_rows"]:
            raise ValueError("reserved development or test row counts changed")
        _verify_fit(metadata[horizon], dev, test, split, horizon, global_cutoff=global_cutoff)
        _, test_labels, _ = temporal_dates(test, horizon_days=horizon)
        if (test_labels > _timestamp(reservation["as_of"])).any():
            raise ValueError("historical test contains labels unavailable at reservation cutoff")
        predictions = _native_predictions(bundle_dir, horizon, test, metadata[horizon])
        cohort_reports = {cohort: assess_historical_cell(predictions.loc[predictions["cohort"].eq(cohort)], multiplicity=multiplicity)
                          if cohort in declared[str(horizon)] else {"status": "not_declared", "rows": int(predictions["cohort"].eq(cohort).sum()), "issues": []}
                          for cohort in COHORTS}
        staged.append((horizon, dev_path, test_path, predictions, cohort_reports))
    output_dir.mkdir(parents=True, exist_ok=True)
    _write(output_dir / "reservation.json", reservation)
    _write(output_dir / "model_validation_receipt.json", verified_receipt)
    shutil.copy2(binding_path, output_dir / "candidate_binding.json")
    shutil.copy2(source_admission_path, output_dir / source_admission_path.name)
    for horizon, (dev_path, test_path, metadata_path, _, _) in family_inputs.items():
        shutil.copy2(dev_path, output_dir / f"development_T{horizon}.parquet")
        shutil.copy2(test_path, output_dir / test_path.name)
        shutil.copy2(metadata_path, output_dir / f"training_metadata_T{horizon}.json")
    reports, admitted = {}, {}
    for horizon, dev_path, test_path, predictions, cohort_reports in staged:
        shutil.copy2(dev_path, output_dir / f"development_T{horizon}.parquet")
        shutil.copy2(training_dir / f"metadata_T{horizon}.json", output_dir / f"training_metadata_T{horizon}.json")
        shutil.copy2(test_path, output_dir / test_path.name)
        predictions.to_parquet(output_dir / f"predictions_T{horizon}.parquet", index=False)
        reports[str(horizon)] = {"declared_cohorts": declared[str(horizon)], "cohorts": cohort_reports,
                                 "feature_protocol": feature_protocol(metadata[horizon]),
                                 "target_protocol": target_protocol(metadata[horizon])}
        passing = [cohort for cohort in declared[str(horizon)] if cohort_reports[cohort]["status"] == "passed"]
        if passing:
            admitted[str(horizon)] = passing
    evidence_files = [{"path": path.relative_to(output_dir).as_posix(), "bytes": path.stat().st_size,
                       "sha256": sha256_file(path)} for path in sorted(output_dir.iterdir()) if path.is_file()]
    core = {"schema": SCHEMA, "bundle_id": manifest["bundle_id"],
            "model_validation_receipt_id": manifest["receipt_id"],
            "artifacts": manifest["artifacts"], "reservation_id": reservation["reservation_id"],
            "source_admission": source_admission,
            "panel_id": reservation["panel_id"], "candidate_claim": claim,
            "reservation_registry_record": reservation,
            "global_temporal_boundary": reservation["global_temporal_boundary"],
            "reservation": reservation, "model_validation_receipt": verified_receipt,
            "policy": POLICY, "multiplicity_cells": multiplicity,
            "pretest_head_selection": {
                "requested_horizons": sorted(int(h) for h in reservation["horizons"]),
                "selected_horizons": sorted(manifest["horizons"]),
                "basis": "development_model_validation_and_walk_forward_only", "test_labels_used": False},
            "protocol": {**DISCLOSURES, "method": POLICY["method"],
                         "selection_test_labels_used": False,
                         "test_labels_used_for_hyperparameter_tuning": False,
                         "test_labels_used_for_early_stopping": False,
                         "test_labels_used_for_quantile_calibration": False,
                         "candidate_attempts_per_reservation": 1},
            "splits": reservation["horizons"], "horizons": reports,
            "admitted_cohorts": admitted, "status": "passed" if admitted else "failed",
            "evidence_files": evidence_files, "evaluated_at": datetime.now(timezone.utc).isoformat()}
    signed = _sign({**core, "admission_id": _identity(core)}, private_key)
    receipt_path = output_dir / "receipt.json"
    _write(receipt_path, signed)
    verify_historical_admission_receipt(signed, bundle_dir=bundle_dir, public_key=public_key, evidence_root=output_dir)
    return receipt_path, signed


def verify_historical_admission_receipt(
    receipt: Mapping[str, Any], *, bundle_manifest: Mapping[str, Any] | None = None,
    bundle_dir: Path | None = None, public_key: str | bytes | None = None,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    """Authenticate source artifacts and qualified cells, with optional full replay."""
    from ml.model_bundle import ModelBundleError, verify_bundle_dir, verify_bundle_manifest, verify_signed_payload

    payload = verify_signed_payload(receipt, public_key=public_key)
    if payload.get("schema") != SCHEMA or payload.get("policy") != POLICY:
        raise ModelBundleError("unsupported historical admission schema or policy")
    core = {key: value for key, value in payload.items() if key != "admission_id"}
    if payload.get("admission_id") != _identity(core):
        raise ModelBundleError("historical admission identity mismatch")
    if bundle_dir is not None:
        manifest = verify_bundle_dir(bundle_dir, public_key=public_key)
    elif bundle_manifest is not None:
        manifest = (verify_bundle_manifest(bundle_manifest, public_key=public_key)
                    if "signature" in bundle_manifest else dict(bundle_manifest))
    else:
        raise ModelBundleError("historical admission requires an authenticated source bundle")
    if payload.get("bundle_id") != manifest.get("bundle_id") or payload.get("model_validation_receipt_id") != manifest.get("receipt_id"):
        raise ModelBundleError("historical admission targets a different source bundle or model receipt")
    if payload.get("artifacts") != manifest.get("artifacts"):
        raise ModelBundleError("historical admission source model artifacts differ")
    _verify_model_receipt(payload.get("model_validation_receipt") or {}, manifest)
    reservation = payload.get("reservation") or {}
    _verify_reservation(reservation, public_key=public_key)
    global_cutoff = _verify_global_boundary(reservation)
    if payload.get("global_temporal_boundary") != reservation.get("global_temporal_boundary"):
        raise ModelBundleError("historical admission global temporal boundary differs from the reservation")
    claim = verify_signed_payload(payload.get("candidate_claim") or {}, public_key=public_key)
    if (payload.get("panel_id") != reservation.get("panel_id")
            or payload.get("reservation_registry_record") != reservation
            or claim.get("panel_id") != reservation.get("panel_id")
            or claim.get("event_ids") != sorted(row["event_id"] for row in reservation["panel_events"])
            or claim.get("schema") != "quantiv.historical-panel-claim.v1"):
        raise ModelBundleError("historical admission lacks authenticated durable panel/candidate commitments")
    if bundle_dir is not None and claim.get("native_candidate_id") != _native_candidate_id(bundle_dir, manifest):
        raise ModelBundleError("historical admission native candidate differs from pretest claim")
    _verify_source_admission(payload.get("source_admission") or {})
    if payload.get("source_admission") != reservation.get("source_admission"):
        raise ModelBundleError("historical source admission differs from reserved source lineage")
    horizon_keys = {str(h) for h in manifest["horizons"]}
    if (payload.get("reservation_id") != reservation.get("reservation_id")
            or payload.get("splits") != reservation.get("horizons")
            or set(payload.get("horizons") or {}) != horizon_keys
            or not horizon_keys.issubset(reservation.get("horizons") or {})):
        raise ModelBundleError("historical admission horizon or reservation binding mismatch")
    expected_selection = {"requested_horizons": sorted(int(h) for h in reservation["horizons"]),
                          "selected_horizons": sorted(manifest["horizons"]),
                          "basis": "development_model_validation_and_walk_forward_only", "test_labels_used": False}
    if payload.get("pretest_head_selection") != expected_selection:
        raise ModelBundleError("historical admission does not bind development-only head selection")
    protocol = payload.get("protocol") or {}
    if any(protocol.get(key) != value for key, value in DISCLOSURES.items()) or any(
        protocol.get(key) is not False for key in ("selection_test_labels_used", "test_labels_used_for_hyperparameter_tuning",
            "test_labels_used_for_early_stopping", "test_labels_used_for_quantile_calibration")):
        raise ModelBundleError("historical admission lacks required exposure disclosures")
    expected = {}
    declared_count = 0
    for key, horizon in payload["horizons"].items():
        declared = horizon.get("declared_cohorts")
        if not isinstance(declared, list) or len(set(declared)) != len(declared) or not set(declared).issubset(COHORTS):
            raise ModelBundleError("historical admission has invalid declared cohort set")
        declared_count += len(declared)
        cohorts = horizon.get("cohorts") or {}
        if set(cohorts) != set(COHORTS):
            raise ModelBundleError("historical admission is missing cohort dispositions")
        passing = [cohort for cohort in declared if cohorts[cohort].get("status") == "passed"]
        for cohort in passing:
            cell = cohorts[cohort]
            _check_passed_metrics(cell)
        if passing:
            expected[key] = passing
    if payload.get("multiplicity_cells") != max(1, declared_count):
        raise ModelBundleError("historical admission multiplicity family mismatch")
    if payload.get("admitted_cohorts") != expected or payload.get("status") != ("passed" if expected else "failed"):
        raise ModelBundleError("historical admission capability map disagrees with cell outcomes")
    members = payload.get("evidence_files")
    if not isinstance(members, list) or not members:
        raise ModelBundleError("historical admission has no retained evidence file digests")
    names = set()
    for member in members:
        relative = Path(str(member.get("path", "")))
        if relative.is_absolute() or len(relative.parts) != 1 or relative.name in names or relative.name in {".", "..", "receipt.json"}:
            raise ModelBundleError("historical admission has unsafe evidence paths")
        names.add(relative.name)
        if evidence_root is not None:
            path = evidence_root / relative
            if path.is_symlink() or not path.is_file() or path.stat().st_size != member.get("bytes") or sha256_file(path) != member.get("sha256"):
                raise ModelBundleError(f"historical evidence digest mismatch: {relative}")
    required = {"reservation.json", "model_validation_receipt.json", "candidate_binding.json", "historical_training_admission.json",
                *[f"predictions_T{horizon}.parquet" for horizon in manifest["horizons"]], *[
        f"{prefix}_T{horizon}.{suffix}" for horizon in reservation["horizons"]
        for prefix, suffix in (("development", "parquet"), ("final_test", "parquet"), ("training_metadata", "json"))]}
    if names != required:
        raise ModelBundleError("historical evidence file member set mismatch")
    if evidence_root is not None:
        if _read(evidence_root / "reservation.json") != reservation or _read(evidence_root / "model_validation_receipt.json") != payload["model_validation_receipt"]:
            raise ModelBundleError("historical evidence reservation/model receipt changed")
        source_path = evidence_root / "historical_training_admission.json"
        if sha256_file(source_path) != payload["source_admission"]["sha256"] or _read(source_path) != payload["source_admission"]["report"]:
            raise ModelBundleError("historical evidence source admission digest mismatch")
        binding = _read(evidence_root / "candidate_binding.json")
        if binding != payload["candidate_claim"]:
            raise ModelBundleError("historical evidence reservation candidate binding mismatch")
        _reserved_family_inputs(reservation, evidence_root, evidence_root, retained=True)
        for horizon in manifest["horizons"]:
            key = str(horizon)
            test_path = evidence_root / f"final_test_T{horizon}.parquet"
            dev_path = evidence_root / f"development_T{horizon}.parquet"
            split = payload["splits"][key]
            if sha256_file(test_path) != split["test_sha256"] or sha256_file(dev_path) != split["development_sha256"]:
                raise ModelBundleError("historical evidence split input digest mismatch")
            if sha256_file(evidence_root / f"training_metadata_T{horizon}.json") != split["development_metadata_sha256"]:
                raise ModelBundleError("historical evidence development metadata digest mismatch")
            predictions = pd.read_parquet(evidence_root / f"predictions_T{horizon}.parquet")
            if bundle_dir is not None:
                metadata = _read(bundle_dir / f"metadata_T{horizon}.json")
                dev = _validate_frame(pd.read_parquet(dev_path), metadata, horizon)
                test = _validate_frame(pd.read_parquet(test_path), metadata, horizon)
                _verify_fit(metadata, dev, test, split, horizon, global_cutoff=global_cutoff)
                replay = _native_predictions(bundle_dir, horizon, test, metadata)
                if training_row_digest(replay) != training_row_digest(predictions):
                    raise ModelBundleError("historical evidence predictions do not replay exact native candidate")
            for cohort in payload["horizons"][key]["declared_cohorts"]:
                assessment = assess_historical_cell(predictions.loc[predictions["cohort"].eq(cohort)], multiplicity=payload["multiplicity_cells"])
                if _canonical(assessment) != _canonical(payload["horizons"][key]["cohorts"][cohort]):
                    raise ModelBundleError("historical evidence metrics do not replay retained predictions")
    return dict(payload)
