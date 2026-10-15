"""Verify activation inputs for every actual forecast cohort without global coverage vetoes."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from ml.model_bundle import verify_bundle_dir
from ml.model_control import prospective_event_mask, prospective_snapshot_mask, score_bundle_frame
from ml.pipeline_validation import validate_forecast_artifact

try:
    from market_sessions import latest_completed_us_market_session
    from verify_historical_training_gate import verify_historical_training_gate
except ModuleNotFoundError:
    from scripts.market_sessions import latest_completed_us_market_session
    from scripts.verify_historical_training_gate import verify_historical_training_gate


# These codes have options-only meanings in quantiv.data-reconciliation.v2.
# In particular source_partition_reconciliation_failed describes the options
# ingestion receipt, never the OHLCV/calendar/action source contracts.
_OPTIONS_ONLY_CODES = frozenset({
    "options_unavailable", "options_stale", "options_duplicate_keys",
    "event_quote_coverage_below_limit", "option_quote_quality_below_limit",
    "source_partition_reconciliation_failed",
})
_AGGREGATE_CODES = frozenset({
    "event_quote_coverage_below_limit", "option_quote_quality_below_limit",
})


def _object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise RuntimeError(f"activation evidence must be a JSON object: {path}")
    return payload


def _reconciliation(data_dir: Path, current: datetime, max_age: float) -> tuple[dict, float]:
    manifest = _object(data_dir / "validation/data_reconciliation.json")
    if manifest.get("schema") != "quantiv.data-reconciliation.v2":
        raise RuntimeError("retrain reconciliation evidence has the wrong schema")
    quality, exceptions = manifest.get("quality"), manifest.get("exceptions")
    if (not isinstance(quality, dict) or not isinstance(exceptions, list)
            or any(not isinstance(item, dict) for item in exceptions)
            or type(quality.get("decision_safe")) is not bool):
        raise RuntimeError("retrain reconciliation evidence has an invalid decision contract")
    critical = [item for item in exceptions if item.get("severity") == "critical"]
    if (quality.get("critical_exceptions") != len(critical)
            or quality["decision_safe"] != (len(critical) == 0)):
        raise RuntimeError("retrain reconciliation decision contradicts its exceptions")
    core = {key: value for key, value in manifest.items()
            if key not in {"manifest_id", "generated_at"}}
    identity = "sha256:" + hashlib.sha256(json.dumps(
        core, sort_keys=True, separators=(",", ":"), default=str,
    ).encode()).hexdigest()
    if manifest.get("manifest_id") != identity:
        raise RuntimeError("retrain reconciliation identity does not match its contents")
    generated = datetime.fromisoformat(str(manifest.get("generated_at")).replace("Z", "+00:00"))
    if generated.tzinfo is None:
        raise RuntimeError("retrain reconciliation generated_at must include a timezone")
    age = (current - generated).total_seconds() / 3600
    if age < -1:
        raise RuntimeError("retrain reconciliation evidence is dated in the future")
    if age > max_age:
        raise RuntimeError(f"retrain reconciliation evidence is {age:.1f}h old; current evidence is required")
    if any(not isinstance(manifest.get(section), dict)
           for section in ("source_reconciliation", "quote_quality", "datasets")):
        raise RuntimeError("retrain reconciliation evidence has an invalid source contract")
    return manifest, age


def _immutable_sources(data_dir: Path) -> tuple[dict, dict]:
    pointer = _object(data_dir / "control/current_data_release.json")
    identity = pointer.get("release_id")
    if (not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity)
            or pointer.get("manifest") != f"control/releases/{identity}.json"):
        raise RuntimeError("current immutable data-release identity is invalid")
    release_path = data_dir / pointer["manifest"]
    if release_path.is_symlink():
        raise RuntimeError("immutable data-release manifest may not be a symlink")
    manifest = _object(release_path)
    if manifest.get("release_id") != identity or manifest.get("schema") != "quantiv.data-release.v1":
        raise RuntimeError("current immutable data-release manifest is invalid")
    members = manifest.get("files")
    if not isinstance(members, list) or not members:
        raise RuntimeError("current immutable data-release has no source members")
    names = []
    for member in members:
        relative = Path(member["path"])
        path = data_dir / relative
        if (relative.is_absolute() or ".." in relative.parts
                or not relative.as_posix().startswith("parquet/")
                or path.is_symlink() or data_dir.resolve() not in path.resolve().parents):
            raise RuntimeError("immutable source member escapes the data release")
        names.append(relative.as_posix())
    if len(set(names)) != len(names):
        raise RuntimeError("immutable data-release has duplicate source members")
    historical = verify_historical_training_gate(data_dir=data_dir)
    return historical, {item["path"]: item for item in members}


def _live_rows(forecast_path: Path, data_dir: Path, current: datetime) -> tuple[pd.DataFrame, dict, dict, list[str]]:
    frame = pd.read_parquet(forecast_path)
    if frame.empty or "model_bundle_id" not in frame:
        raise RuntimeError("activation requires a nonempty actual forecast artifact")
    ids = frame["model_bundle_id"]
    if (not ids.map(lambda value: isinstance(value, str)
                    and re.fullmatch(r"[0-9a-f]{64}", value) is not None).all()
            or ids.nunique() != 1):
        raise RuntimeError("every actual forecast row must reference the same exact signed bundle id")
    bundle = data_dir / "models/bundles" / ids.iloc[0]
    verified = verify_bundle_dir(bundle)
    horizons = pd.to_numeric(frame.get("model_horizon"), errors="coerce")
    if not horizons.isin(verified["horizons"]).all():
        raise RuntimeError("forecast horizons are not members of the verified signed bundle")
    validation = validate_forecast_artifact(forecast_path, models_dir=bundle, now=current)
    leads = (pd.to_datetime(frame["earnings_date"]) - pd.to_datetime(frame["snapshot_date"])).dt.days
    if not horizons.eq(leads).all():
        raise RuntimeError("actual forecast model horizon differs from its exact event calendar lead")
    for horizon in sorted(set(horizons.astype(int))):
        selected = frame.loc[horizons.eq(horizon)]
        vectors = pd.DataFrame([json.loads(value) for value in selected["feature_vector"]],
                               index=selected.index)
        if "feature_protocol" in selected:
            vectors["feature_protocol"] = selected["feature_protocol"]
        point, quantiles = score_bundle_frame(bundle, horizon, vectors)
        actual_quantiles = selected[["p10", "p25", "p50", "p75", "p90"]].to_numpy(dtype=float)
        if (not np.isclose(selected["em_ml_pct"].to_numpy(dtype=float), point, rtol=1e-6, atol=1e-8).all()
                or not np.isclose(actual_quantiles, quantiles, rtol=1e-6, atol=1e-8).all()):
            raise RuntimeError("actual forecast predictions do not replay from the signed native models and exact feature vectors")
    validation["native_prediction_replay"] = "verified"
    baseline = pd.to_numeric(frame["em_math_pct"], errors="coerce")
    cohorts = baseline.map(lambda value: "optionless" if pd.isna(value) else "strict_options")
    if "__cohort" in frame and not frame["__cohort"].eq(cohorts).all():
        raise RuntimeError("actual forecast cohort contradicts its validated option evidence")
    freshness = frame.assign(recorded_at=current.isoformat())
    scored = pd.to_datetime(frame["scored_at"], utc=True, errors="coerce")
    if not (scored.le(pd.Timestamp(current)).all() and prospective_snapshot_mask(freshness).all()):
        raise RuntimeError("every actual forecast row must use the latest genuinely scored market snapshot")
    if not prospective_event_mask(freshness).all():
        raise RuntimeError("actual forecast rows fail the live event prediction deadline")
    return frame, verified, validation, sorted(set(cohorts))


def _shared_sources(data_dir: Path, members: dict, historical: dict, frame: pd.DataFrame,
                    manifest: dict, expected: str) -> dict:
    controls, mappings, duplicates = (manifest.get(name) for name in
                                     ("pipeline_controls", "symbol_mappings", "duplicates"))
    if (not isinstance(controls, dict)
            or not isinstance(controls.get("quarantine"), dict)
            or controls["quarantine"].get("status") != "enforced"):
        raise RuntimeError("record_quarantine_not_instrumented: current source quarantine is not enforced")
    if not isinstance(mappings, dict) or mappings.get("stale_source_symbols"):
        raise RuntimeError("stale_symbol_mappings: current sources contain retired symbol identities")
    if not isinstance(duplicates, dict):
        raise RuntimeError("current source duplicate-key contract is malformed")
    for name, evidence in duplicates.items():
        if name == "options":
            continue
        if not isinstance(evidence, dict) or evidence.get("duplicate_rows", 0) != 0:
            raise RuntimeError(f"{name}_duplicate_keys: current non-options sources contain duplicate keys")
    datasets = manifest["datasets"]
    for name in ("ohlcv", "earnings"):
        dataset = datasets.get(name)
        if (not isinstance(dataset, dict) or dataset.get("status") != "passed"
                or type(dataset.get("rows")) is not int or dataset["rows"] <= 0):
            raise RuntimeError(f"current {name} source contract has not passed")
    if datasets["ohlcv"].get("max_date") != expected:
        raise RuntimeError("reconciliation OHLCV source is not the latest completed market session")
    actions = historical["corporate_actions"]
    symbols = set(frame["act_symbol"].astype(str))
    ends = actions["symbol_window_ends"]
    if (not symbols.issubset(actions["covered_symbols"])
            or any(ends.get(symbol, "") < expected for symbol in symbols)):
        raise RuntimeError("verified corporate-action receipts do not cover every live symbol and snapshot")
    corporate = manifest.get("corporate_actions")
    if (not isinstance(corporate, dict) or corporate.get("status") != "passed" or corporate.get("errors")
            or corporate.get("continuity_status") != "enforced"):
        raise RuntimeError("current corporate-action continuity contract has not passed")
    calendar = pd.read_csv(data_dir / "earnings_calendar.csv", converters={"act_symbol": str})
    calendar_dates = pd.to_datetime(calendar["date"], errors="raise").dt.date.astype(str)
    calendar_events = set(zip(calendar["act_symbol"], calendar_dates,
                              calendar["timing"].str.strip().str.lower()))
    actual_events = zip(frame["act_symbol"], pd.to_datetime(frame["earnings_date"]).dt.date.astype(str),
                        frame["timing"].str.strip().str.lower())
    if any(event not in calendar_events for event in actual_events):
        raise RuntimeError("actual forecast event identities differ from the canonical earnings source")
    paths = [str(data_dir / name) for name in members if name.startswith("parquet/ohlcv/")]
    with duckdb.connect() as conn:
        maximum, rows = conn.execute(
            "SELECT MAX(CAST(date AS DATE)), COUNT(*) FROM read_parquet(?, hive_partitioning=false, union_by_name=true)",
            [paths],
        ).fetchone()
        prices = conn.execute(
            "SELECT act_symbol, close FROM read_parquet(?, hive_partitioning=false, union_by_name=true) "
            "WHERE CAST(date AS DATE) = CAST(? AS DATE) AND act_symbol IN (SELECT UNNEST(?))",
            [paths, expected, sorted(symbols)],
        ).df()
    if str(maximum) != expected or rows != datasets["ohlcv"]["rows"]:
        raise RuntimeError("actual immutable OHLCV prices contradict the current source contract")
    if prices["act_symbol"].duplicated().any() or set(prices["act_symbol"]) != symbols:
        raise RuntimeError("every actual forecast symbol requires one latest-session OHLCV price")
    closes = dict(zip(prices["act_symbol"], prices["close"]))
    for row in frame.itertuples():
        close = float(closes[row.act_symbol])
        if not math.isfinite(close) or close <= 0 or not math.isclose(float(row.spot_price), close, rel_tol=1e-6, abs_tol=1e-8):
            raise RuntimeError("actual forecast spot price differs from its immutable latest-session OHLCV close")
    return {"status": "passed", "source_date": expected, "rows": rows,
            "forecast_symbols": len(symbols), "partitions": len(paths)}


def _strict_options(data_dir: Path, members: dict, manifest: dict, expected: str,
                    frame: pd.DataFrame) -> dict:
    try:
        from verify_retrain_data_gate import _active_options_date
        from sync_dolthub import _content_digest
    except ModuleNotFoundError:
        from scripts.verify_retrain_data_gate import _active_options_date
        from scripts.sync_dolthub import _content_digest
    active = _active_options_date(data_dir)
    if active != expected:
        raise RuntimeError("published options release is not the latest completed market session: "
                           f"active={active}, expected={expected}")
    source, quote = manifest["source_reconciliation"], manifest["quote_quality"]
    if (source.get("source_date") != expected or quote.get("source_date") != expected
            or source.get("status") != "passed" or source.get("errors")
            or quote.get("status") not in {"passed", "degraded"} or quote.get("errors")):
        raise RuntimeError("strict option rows require current passed options source and quote evidence")
    relative = f"parquet/options_chain/year={expected[:4]}/month={expected[5:7]}/{expected}.parquet"
    member = members.get(relative)
    ingestion_path = data_dir / f"control/ingestion/options/{expected}.json"
    ingestion = _object(ingestion_path)
    options = pd.read_parquet(data_dir / relative)
    rows = len(options)
    content = _content_digest(options)
    if (member is None or ingestion.get("schema") != "quantiv.options-ingestion.v1"
            or ingestion.get("status") != "passed"
            or ingestion.get("source") != "dolthub/post-no-preference/options/option_chain"
            or ingestion.get("source_date") != expected or ingestion.get("partition") != relative
            or ingestion.get("partition_sha256") != member["sha256"]
            or ingestion.get("replay_equivalence") != "verified"
            or ingestion.get("content_sha256") != content
            or ingestion.get("persisted_content_sha256") != content
            or not isinstance(ingestion.get("source_capabilities"), dict)
            or ingestion["source_capabilities"].get("timestamp_precision") != "date"
            or source.get("source_capabilities") != ingestion["source_capabilities"]
            or ingestion.get("expected_rows") != rows or ingestion.get("received_rows") != rows
            or rows <= 0 or source.get("partition_sha256") != member["sha256"]
            or source.get("content_sha256") != content or source.get("replay_equivalence") != "verified"
            or any(source.get(key) != rows for key in ("expected_rows", "received_rows", "duckdb_rows"))):
        raise RuntimeError("strict option rows lack verified current immutable options source proof")
    dates = pd.to_datetime(options["date"], errors="raise").dt.date.astype(str)
    if (not dates.eq(expected).all()
            or options.duplicated(["act_symbol", "date", "expiration", "strike", "call_put"]).any()):
        raise RuntimeError("current options source has invalid session identities or duplicate primary keys")
    options["expiration"] = pd.to_datetime(options["expiration"], errors="raise")
    eligible_pairs = _canonical_source_pairs(data_dir / relative, relative, frame)
    for row in frame.loc[frame["em_math_pct"].notna()].to_dict("records"):
        vector = json.loads(row["feature_vector"])
        expiry = pd.Timestamp(expected) + pd.Timedelta(days=float(vector["dte"]))
        evidence = options.loc[options["act_symbol"].eq(row["act_symbol"])
                               & options["expiration"].eq(expiry)
                               & np.isclose(pd.to_numeric(options["strike"]), float(row["atm_strike"]),
                                            rtol=1e-6, atol=1e-8)]
        call, put = evidence.loc[evidence["call_put"].eq("Call")], evidence.loc[evidence["call_put"].eq("Put")]
        if len(call) != 1 or len(put) != 1:
            raise RuntimeError("strict forecast legs do not identify one same-expiry immutable source pair")
        if (row["act_symbol"], expiry, float(row["atm_strike"])) not in eligible_pairs:
            raise RuntimeError("strict forecast source pair is not eligible under the canonical quote policy")
        call, put = call.iloc[0], put.iloc[0]
        earnings = pd.Timestamp(row["earnings_date"])
        if expiry < earnings or (row["timing"] == "amc" and expiry <= earnings):
            raise RuntimeError("strict source expiration does not cover the actual earnings event")
        for side, source_row in (("call", call), ("put", put)):
            for field in ("bid", "ask"):
                if not math.isclose(float(row[f"{side}_{field}"]), float(source_row[field]),
                                    rel_tol=1e-6, abs_tol=1e-8):
                    raise RuntimeError("strict forecast quote legs differ from their immutable source pair")
            for field, source_field in (("volume", "option_volume"), ("open_interest", "open_interest"),
                                         ("quote_timestamp", "quote_timestamp")):
                actual, original = row.get(f"{side}_{field}"), source_row.get(source_field)
                if pd.isna(actual) and pd.isna(original):
                    continue
                if pd.isna(actual) or pd.isna(original):
                    raise RuntimeError("strict forecast claims quote evidence absent from its immutable source pair")
                if field == "quote_timestamp":
                    equal = pd.to_datetime(actual, utc=True) == pd.to_datetime(original, utc=True)
                else:
                    equal = math.isclose(float(actual), float(original), rel_tol=1e-6, abs_tol=1e-8)
                if not equal:
                    raise RuntimeError("strict forecast quote evidence differs from its immutable source pair")
        if (not math.isclose(float(row["atm_iv"]), (float(call["vol"]) + float(put["vol"])) / 2,
                             rel_tol=1e-6, abs_tol=1e-8)
                or not math.isclose(float(row["em_math_pct"]), float(row["straddle_mid"]) / float(row["atm_strike"]),
                                    rel_tol=1e-6, abs_tol=1e-8)):
            raise RuntimeError("strict forecast IV or straddle baseline contradicts its immutable source pair")
    return {"status": "passed", "source_date": expected, "rows": rows,
            "partition": relative, "sha256": member["sha256"],
            "ingestion_receipt_sha256": hashlib.sha256(ingestion_path.read_bytes()).hexdigest()}


def _canonical_source_pairs(partition: Path, relative: str, frame: pd.DataFrame) -> set[tuple]:
    """Reuse the production quote selection over the verified current partition.

    A temporary read-only link isolates this one immutable session from optional
    historical datasets. The ordinary source views enforce every leg and pair
    policy, including delta, liquidity, IV, DTE, timestamps and ATM ranking.
    """
    try:
        from setup_duckdb_from_parquet import setup_views
    except ModuleNotFoundError:
        from scripts.setup_duckdb_from_parquet import setup_views
    symbols = sorted(set(frame.loc[frame["em_math_pct"].notna(), "act_symbol"]))
    with tempfile.TemporaryDirectory(prefix="quantiv-activation-source-") as directory:
        root = Path(directory)
        selected = root / relative
        selected.parent.mkdir(parents=True)
        selected.symlink_to(partition.resolve())
        with duckdb.connect() as conn:
            with redirect_stdout(StringIO()):
                setup_views(conn, root)
            pairs = conn.execute(
                "SELECT act_symbol, expiration, atm_strike FROM v_straddle_features "
                "WHERE act_symbol IN (SELECT UNNEST(?))", [symbols],
            ).fetchall()
    return {(symbol, pd.Timestamp(expiry), float(strike)) for symbol, expiry, strike in pairs}


def verify_forecast_activation_gate(*, data_dir: Path, forecast_path: Path,
                                    now: datetime | None, max_report_age_hours: float) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    if not math.isfinite(max_report_age_hours) or not 0 < max_report_age_hours <= 36:
        raise RuntimeError("forecast activation requires reconciliation evidence no older than 36h")
    try:
        manifest, age = _reconciliation(data_dir, current, max_report_age_hours)
        critical = {str(item.get("code") or "unknown") for item in manifest["exceptions"]
                    if item.get("severity") == "critical"}
        shared_holds = critical - _OPTIONS_ONLY_CODES
        if shared_holds:
            raise RuntimeError("published data release is held by reconciliation: " + ", ".join(sorted(shared_holds)))
        frame, bundle, validation, cohorts = _live_rows(forecast_path, data_dir, current)
        allowed = _OPTIONS_ONLY_CODES if cohorts == ["optionless"] else frozenset()
        blocking = critical - allowed
        quality = manifest["quality"]
        codes = quality.get("model_activation_blocking_codes", [])
        if (not isinstance(codes, list) or any(not isinstance(code, str) or not code for code in codes)
                or ("model_activation_eligible" in quality and type(quality["model_activation_eligible"]) is not bool)):
            raise RuntimeError("retrain reconciliation model activation contract is invalid")
        blocking.update(set(codes) - allowed - _AGGREGATE_CODES)
        if quality.get("model_activation_eligible") is False and not codes:
            blocking.add("model_activation_ineligible")
        if blocking:
            raise RuntimeError("published data release is held by reconciliation: " + ", ".join(sorted(blocking)))
        historical, members = _immutable_sources(data_dir)
        expected = latest_completed_us_market_session(current).isoformat()
        prices = _shared_sources(data_dir, members, historical, frame, manifest, expected)
        sources = {"data_release": {key: historical[key] for key in ("release_id", "files", "bytes")},
                   "ohlcv": prices, "corporate_actions": historical["corporate_actions"],
                   "earnings_calendar": historical["calendar_source"]}
        if "strict_options" in cohorts:
            sources["options"] = _strict_options(data_dir, members, manifest, expected, frame)
        advisory = {str(item["code"]) for item in manifest["exceptions"]
                    if item.get("code") in _OPTIONS_ONLY_CODES}
        advisory.update(set(codes) & (allowed | _AGGREGATE_CODES))
        return {"status": "passed", "manifest_id": manifest["manifest_id"],
                "model_bundle_id": bundle["bundle_id"], "cohorts": cohorts,
                "snapshot_date": expected, "source_date": expected,
                "expected_source_date": expected, "generated_at": manifest["generated_at"],
                "age_hours": round(age, 3), "source_evidence": sources,
                "forecast_validation": validation, "advisory_codes": sorted(advisory)}
    except RuntimeError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, duckdb.Error) as exc:
        raise RuntimeError(f"forecast activation evidence verification failed: {exc}") from exc
