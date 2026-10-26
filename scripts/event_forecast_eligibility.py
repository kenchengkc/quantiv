"""Select publication rows while keeping source identity failures fatal."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import tempfile
from typing import Any, Callable

import numpy as np
import pandas as pd

from ml.live_forecast_validation import validate_live_forecast_artifact
from ml.model_control import prospective_event_mask, prospective_snapshot_mask
from ml.model_protocol import LEGACY_FEATURE_PROTOCOL, LEGACY_TARGET_PROTOCOL, feature_protocol, target_protocol
from ml.model_bundle import verify_bundle_dir
from ml.serving_capabilities import allowed_cohorts
from ml.pipeline_validation import FORECAST_REQUIRED_COLUMNS, PipelineValidationError
from ml.forecast_snapshot import forecast_snapshot_id, forecast_snapshot_key

try:
    from event_forecast_ledger import annotate_forecasts
except ModuleNotFoundError:
    from scripts.event_forecast_ledger import annotate_forecasts


SCHEMA = "quantiv.event-forecast-eligibility.v1"


def event_keys(frame: pd.DataFrame) -> list[str]:
    dates = pd.to_datetime(frame["earnings_date"], errors="raise").dt.strftime("%Y-%m-%d")
    return (frame["act_symbol"].astype(str).str.strip().str.upper() + "|" + dates).tolist()


def _current_source_coverage(bundle_dir: Path) -> dict[str, tuple[str, str]]:
    try:
        from verify_historical_training_gate import verify_historical_corporate_actions
        from data_release import MUTABLE_PARQUET_ALIASES
    except ModuleNotFoundError:
        from scripts.verify_historical_training_gate import verify_historical_corporate_actions
        from scripts.data_release import MUTABLE_PARQUET_ALIASES
    data_dir = bundle_dir.parent.parent.parent
    declared = {path.relative_to(data_dir).as_posix()
                for path in (data_dir / "parquet").rglob("*.parquet")} - MUTABLE_PARQUET_ALIASES
    evidence = verify_historical_corporate_actions(data_dir, declared)
    return {symbol: (evidence["query_start"], evidence["symbol_window_ends"][symbol])
            for symbol in evidence["covered_symbols"]}


def verify_candidate_identity(frame: pd.DataFrame, bundle_dir: Path, bundle_id: str,
                              horizons: list[int], *, require_admitted_cohorts: bool = False) -> pd.DataFrame:
    # Annotation emits rows in source order with a RangeIndex. A filtered
    # publication may have sparse labels; compare its values positionally.
    frame = frame.reset_index(drop=True)
    missing = sorted(FORECAST_REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"forecast artifact lacks required columns: {missing}")
    if frame.empty:
        out = frame.copy()
        out["forecast_id"] = pd.Series(dtype="str")
        out["__cohort"] = pd.Series(dtype="str")
        return out
    if frame["act_symbol"].fillna("").astype(str).str.strip().eq("").any():
        raise ValueError("forecast identity contains a blank symbol")
    for column in ("earnings_date", "snapshot_date"):
        if pd.to_datetime(frame[column], errors="coerce").isna().any():
            raise ValueError(f"forecast identity has invalid {column}")
    keys = frame.assign(act_symbol=frame["act_symbol"].astype(str).str.strip().str.upper(),
                        earnings_date=pd.to_datetime(frame["earnings_date"]).dt.normalize(),
                        snapshot_date=pd.to_datetime(frame["snapshot_date"]).dt.normalize(),
                        model_horizon=pd.to_numeric(frame["model_horizon"], errors="coerce"))
    if keys.duplicated(["act_symbol", "earnings_date", "snapshot_date", "model_horizon"]).any():
        raise ValueError("forecast artifact contains duplicate serving keys")
    if not frame["model_bundle_id"].eq(bundle_id).all():
        raise ValueError("production forecast bundle does not match champion")
    numeric_horizons = pd.to_numeric(frame["model_horizon"], errors="coerce")
    if not numeric_horizons.isin(horizons).all():
        raise ValueError("forecast model horizon does not match champion")
    manifest = verify_bundle_dir(bundle_dir) if (bundle_dir / "manifest.json").exists() else None
    if manifest is not None and (manifest["bundle_id"] != bundle_id or set(manifest["horizons"]) != set(horizons)):
        raise ValueError("forecast serving contract differs from the authenticated champion")
    for horizon in numeric_horizons.unique():
        metadata = json.loads((bundle_dir / f"metadata_T{int(horizon)}.json").read_text())
        selected = frame.loc[numeric_horizons.eq(horizon)]
        if manifest is not None and require_admitted_cohorts:
            baseline = pd.to_numeric(selected["em_math_pct"], errors="coerce")
            cohorts = set(np.where(np.isfinite(baseline) & baseline.gt(0), "strict_options", "optionless"))
            if not cohorts.issubset(allowed_cohorts(manifest, int(horizon), metadata)):
                raise ValueError("forecast cohort is not admitted by the champion serving capability")
        for column, expected, legacy in (
            ("feature_protocol", feature_protocol(metadata), LEGACY_FEATURE_PROTOCOL),
            ("target_protocol", target_protocol(metadata), LEGACY_TARGET_PROTOCOL),
        ):
            declared = selected.get(column, pd.Series(legacy, index=selected.index))
            if not declared.eq(expected).all():
                raise ValueError(f"forecast {column} identity differs from champion")
    annotated = annotate_forecasts(frame)
    audit_columns = ("forecast_id", "feature_hash", "feature_cutoff_at",
                     "prediction_deadline_at", "feature_snapshot_at",
                     "freeze_eligible", "freeze_ineligible_reason")
    timestamps = {"feature_cutoff_at", "prediction_deadline_at", "feature_snapshot_at"}
    out = frame.copy()
    for column in audit_columns:
        if column not in frame:
            out[column] = annotated[column]
            continue
        source, expected = frame[column], annotated[column]
        if column in timestamps:
            same = pd.to_datetime(source, utc=True, errors="coerce").eq(
                pd.to_datetime(expected, utc=True, errors="coerce")
            )
        else:
            same = source.eq(expected).fillna(False)
        same |= source.isna() & expected.isna()
        if not same.all():
            raise ValueError(f"forecast {column} does not match its recorded source")
    if out["forecast_id"].duplicated().any():
        raise ValueError("forecast artifact contains duplicate forecast identities")
    baseline = pd.to_numeric(frame["em_math_pct"], errors="coerce")
    derived = pd.Series(np.where(np.isfinite(baseline) & baseline.gt(0), "strict_options", "optionless"), index=frame.index)
    if "__cohort" in frame and not frame["__cohort"].eq(derived).all():
        raise ValueError("forecast cohort identity differs from its recorded baseline")
    if "__cohort" not in out:
        out["__cohort"] = derived.to_numpy()
    return out.reset_index(drop=True)


def select_publication_rows(frame: pd.DataFrame, *, bundle_dir: Path, now: datetime,
                            assess_cohort: Callable[[pd.DataFrame, int], dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Validate each actual row, then apply the unchanged rolling cohort gate."""
    reasons: dict[int, list[str]] = {}
    try:
        source_coverage = _current_source_coverage(bundle_dir)
    except (RuntimeError, ValueError, OSError):
        source_coverage = {}
    with tempfile.TemporaryDirectory(prefix="quantiv-event-eligibility-") as temp_dir:
        row_path = Path(temp_dir) / "row.parquet"
        for index, row in frame.iterrows():
            window = source_coverage.get(str(row["act_symbol"]).strip().upper())
            snapshot = str(row["snapshot_date"])[:10]
            if window is None or not window[0] <= snapshot <= window[1]:
                reasons[index] = ["corporate_action_source_unavailable"]
            row_frame = frame.loc[[index]].copy()
            row_frame.to_parquet(row_path, index=False)
            try:
                validate_live_forecast_artifact(row_path, models_dir=bundle_dir, now=now)
            except PipelineValidationError as exc:
                if any(issue.code in {"invalid_quote_quality_policy", "missing_forecast_model_metadata"} for issue in exc.issues):
                    raise
                reasons[index] = sorted({issue.code for issue in exc.issues})
            except (TypeError, ValueError, OverflowError):
                reasons[index] = ["invalid_forecast_value"]
            freshness = row_frame.copy()
            freshness["recorded_at"] = now.isoformat()
            scored = pd.to_datetime(freshness["scored_at"], utc=True, errors="coerce")
            if not (scored.le(pd.Timestamp(now)).all() and prospective_snapshot_mask(freshness).all()):
                reasons.setdefault(index, []).append("stale_current_snapshot")
            if not prospective_event_mask(freshness).all():
                reasons.setdefault(index, []).append("event_prediction_deadline")
    local = frame.loc[~frame.index.isin(reasons)].copy()
    cohort_checks = {}
    for (horizon, cohort), selected in local.groupby(["model_horizon", "__cohort"], sort=True):
        check = assess_cohort(selected, int(horizon))
        cohort_checks[f"{int(horizon)}|{cohort}"] = check
        if check["status"] not in {"passed", "warning"}:
            for index in selected.index:
                reasons[index] = [f"model_{check['status']}"]
    eligible = frame.loc[~frame.index.isin(reasons)].copy()
    all_keys = event_keys(frame)
    eligible_keys = sorted(set(event_keys(eligible)))
    held: dict[str, set[str]] = {}
    for index, failures in reasons.items():
        held.setdefault(all_keys[index], set()).update(failures)
    # Event allowlists remain unambiguous if a candidate contains several rows
    # for one event: any accepted row can supply that event's selected forecast.
    held = {key: failures for key, failures in held.items() if key not in eligible_keys}
    coverage = {"candidate_rows": len(frame), "eligible_rows": len(eligible),
                "held_rows": len(frame) - len(eligible), "candidate_events": len(set(all_keys)),
                "eligible_events": len(eligible_keys), "held_events": len(held)}
    contract = {"schema": SCHEMA, "eligible_forecast_rows": len(eligible),
                "eligible_snapshot_ids": sorted(forecast_snapshot_id(row) for row in eligible.to_dict("records")),
                "eligible_snapshot_bindings": {forecast_snapshot_key(row): forecast_snapshot_id(row)
                                                for row in eligible.to_dict("records")},
                "eligible_forecast_ids": sorted(eligible["forecast_id"].astype(str).tolist()),
                "eligible_event_keys": eligible_keys, "held_event_keys": sorted(held),
                "held_events": [{"event_key": key, "reasons": sorted(held[key])} for key in sorted(held)],
                "held_rows": [{"forecast_id": str(frame.loc[index, "forecast_id"]),
                               "event_key": all_keys[index], "reasons": sorted(set(failures))}
                              for index, failures in sorted(reasons.items())],
                "cohort_checks": cohort_checks, "coverage": coverage}
    return eligible, local, contract
