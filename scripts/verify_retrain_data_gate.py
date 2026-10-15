#!/usr/bin/env python3
"""Require current decision-safe data evidence before model activation.

The weekly retrain restores the published R2 data release instead of ingesting a new
options candidate itself. That means a plain age check is insufficient: a bounded
fallback release can still be recent in calendar days while the latest candidate was
held by reconciliation. This gate binds retraining to the same reconciliation
contract used by the daily publication path.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ML_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "apps" / "ml"
if str(ML_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_PACKAGE_ROOT))

try:
    from market_sessions import latest_completed_us_market_session
except ModuleNotFoundError:  # imported as scripts.verify_retrain_data_gate in pytest
    from scripts.market_sessions import latest_completed_us_market_session

RECONCILIATION_SCHEMA = "quantiv.data-reconciliation.v2"
_OPTION_SNAPSHOT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.parquet$")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"retrain reconciliation evidence unavailable: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("retrain reconciliation evidence must be a JSON object")
    return payload


def _active_options_date(data_dir: Path) -> str:
    dates: list[str] = []
    root = data_dir / "parquet" / "options_chain"
    for path in root.glob("year=*/month=*/*.parquet"):
        match = _OPTION_SNAPSHOT_RE.fullmatch(path.name)
        if match:
            dates.append(match.group(1))
    if not dates:
        raise RuntimeError("published data release has no options snapshot")
    return max(dates)


def _aggregate_activation_holds(
    manifest: dict[str, Any], quote: dict[str, Any],
) -> set[str]:
    """Reconstruct the original aggregate gates independently of serving flags."""
    holds = {
        str(item.get("code"))
        for item in manifest["exceptions"]
        if item.get("code") in {
            "event_quote_coverage_below_limit", "option_quote_quality_below_limit",
        }
    }
    coverage = manifest.get("event_coverage", {})
    if not isinstance(coverage, dict):
        raise RuntimeError("model activation coverage contract is invalid")
    if coverage.get("status") == "failed":
        holds.add("event_quote_coverage_below_limit")
    if all(key in coverage for key in (
        "expected_events", "covered_events", "minimum_coverage_pct",
    )):
        try:
            expected = int(coverage["expected_events"])
            covered = int(coverage["covered_events"])
            floor = float(coverage["minimum_coverage_pct"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("model activation coverage contract is invalid") from exc
        if expected < 0 or not 0 <= covered <= expected or not math.isfinite(floor):
            raise RuntimeError("model activation coverage contract is invalid")
        if expected and covered / expected < floor:
            holds.add("event_quote_coverage_below_limit")
    rate = quote.get("decision_group_rejection_rate")
    limit = quote.get("max_decision_group_rejection_rate")
    if rate is not None and limit is not None:
        try:
            rate, limit = float(rate), float(limit)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("model activation quote-rate contract is invalid") from exc
        if not math.isfinite(rate) or not math.isfinite(limit):
            raise RuntimeError("model activation quote-rate contract is invalid")
        if rate > limit:
            holds.add("option_quote_quality_below_limit")
    return holds


def verify_retrain_data_gate(
    *,
    data_dir: Path,
    now: datetime | None = None,
    max_report_age_hours: float = 36.0,
    forecast_path: Path | None = None,
) -> dict[str, Any]:
    if forecast_path is not None:
        try:
            from forecast_activation_gate import verify_forecast_activation_gate
        except ModuleNotFoundError:
            from scripts.forecast_activation_gate import verify_forecast_activation_gate
        return verify_forecast_activation_gate(
            data_dir=data_dir, forecast_path=forecast_path, now=now,
            max_report_age_hours=max_report_age_hours,
        )
    report_path = data_dir / "validation" / "data_reconciliation.json"
    manifest = _read_json(report_path)

    if manifest.get("schema") != RECONCILIATION_SCHEMA:
        raise RuntimeError("retrain reconciliation evidence has the wrong schema")

    quality = manifest.get("quality")
    exceptions = manifest.get("exceptions")
    if not isinstance(quality, dict) or not isinstance(exceptions, list):
        raise RuntimeError("retrain reconciliation evidence has an invalid decision contract")
    if type(quality.get("decision_safe")) is not bool:
        raise RuntimeError("retrain reconciliation decision must be boolean")
    if any(not isinstance(item, dict) for item in exceptions):
        raise RuntimeError("retrain reconciliation exceptions are malformed")

    critical = [item for item in exceptions if item.get("severity") == "critical"]
    critical_count = quality.get("critical_exceptions")
    if critical_count != len(critical):
        raise RuntimeError("retrain reconciliation critical count is inconsistent")
    if quality["decision_safe"] != (len(critical) == 0):
        raise RuntimeError("retrain reconciliation decision contradicts its exceptions")
    if not quality["decision_safe"]:
        codes = ", ".join(
            sorted(str(item.get("code") or "unknown") for item in critical)
        )
        raise RuntimeError(
            "published data release is held by reconciliation; retraining is blocked"
            + (f": {codes}" if codes else "")
        )

    generated_raw = manifest.get("generated_at")
    try:
        generated_at = datetime.fromisoformat(
            str(generated_raw).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise RuntimeError("retrain reconciliation generated_at is invalid") from exc
    if generated_at.tzinfo is None:
        raise RuntimeError("retrain reconciliation generated_at must include a timezone")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age_hours = (
        current.astimezone(timezone.utc) - generated_at.astimezone(timezone.utc)
    ).total_seconds() / 3600
    if age_hours < -1.0:
        raise RuntimeError("retrain reconciliation evidence is dated in the future")
    if age_hours > max_report_age_hours:
        raise RuntimeError(
            f"retrain reconciliation evidence is {age_hours:.1f}h old; "
            "current evidence is required"
        )

    active_source_date = _active_options_date(data_dir)
    source = manifest.get("source_reconciliation")
    quote = manifest.get("quote_quality")
    if not isinstance(source, dict) or not isinstance(quote, dict):
        raise RuntimeError("retrain reconciliation evidence has an invalid source contract")
    activation_holds = _aggregate_activation_holds(manifest, quote)
    activation_eligible = quality.get("model_activation_eligible")
    activation_codes = quality.get("model_activation_blocking_codes")
    if "model_activation_eligible" in quality or "model_activation_blocking_codes" in quality:
        if (
            type(activation_eligible) is not bool
            or not isinstance(activation_codes, list)
            or any(not isinstance(code, str) or not code for code in activation_codes)
        ):
            raise RuntimeError("retrain reconciliation model activation contract is invalid")
        activation_holds.update(activation_codes)
        if not activation_eligible and not activation_holds:
            activation_holds.add("model_activation_ineligible")
    if activation_holds:
        raise RuntimeError(
            "published data release is serving-safe but model activation is blocked: "
            + ", ".join(sorted(activation_holds))
        )
    source_dates = {
        str(value)
        for value in (source.get("source_date"), quote.get("source_date"))
        if value
    }
    if source_dates != {active_source_date}:
        raise RuntimeError(
            "retrain reconciliation source date does not match the published options release"
        )

    expected_source_date = latest_completed_us_market_session(current).isoformat()
    if active_source_date != expected_source_date:
        raise RuntimeError(
            "published options release is not the latest completed market session: "
            f"active={active_source_date}, expected={expected_source_date}"
        )

    return {
        "status": "passed",
        "manifest_id": manifest.get("manifest_id"),
        "source_date": active_source_date,
        "expected_source_date": expected_source_date,
        "generated_at": generated_at.isoformat(),
        "age_hours": round(age_hours, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--max-report-age-hours", type=float, default=36.0)
    parser.add_argument("--forecast-path", type=Path,
                        help="Bind activation to the actual signed forecast cohorts")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--allow-hold", action="store_true",
                        help="Record an activation hold without blocking historical training")
    args = parser.parse_args()

    try:
        result = verify_retrain_data_gate(
            data_dir=args.data_dir,
            max_report_age_hours=args.max_report_age_hours,
            forecast_path=args.forecast_path,
        )
    except RuntimeError as exc:
        if not args.allow_hold:
            raise
        result = {"status": "held", "reason": str(exc), "reasons": [str(exc)]}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if result["status"] == "held":
        print(f"Model activation held: {result['reason']}")
        return 0
    print(
        "Retrain data gate passed: "
        f"source={result['source_date']} manifest={result['manifest_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
