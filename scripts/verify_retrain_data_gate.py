#!/usr/bin/env python3
"""Require current decision-safe data evidence before a model retrain can mutate prod.

The weekly retrain restores the published R2 data release instead of ingesting a new
options candidate itself.  That means a plain age check is insufficient: a bounded
fallback release can still be recent in calendar days while the latest candidate was
held by reconciliation.  This gate binds retraining to the same reconciliation
contract used by the daily publication path.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market_sessions import latest_completed_us_market_session

RECONCILIATION_SCHEMA = "quantiv.data-reconciliation.v2"
_OPTION_SNAPSHOT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.parquet$")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"retrain reconciliation evidence unavailable: {path}") from exc
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


def verify_retrain_data_gate(
    *,
    data_dir: Path,
    now: datetime | None = None,
    max_report_age_hours: float = 36.0,
) -> dict[str, Any]:
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
        codes = ", ".join(sorted(str(item.get("code") or "unknown") for item in critical))
        raise RuntimeError(
            "published data release is held by reconciliation; retraining is blocked"
            + (f": {codes}" if codes else "")
        )

    generated_raw = manifest.get("generated_at")
    try:
        generated_at = datetime.fromisoformat(str(generated_raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("retrain reconciliation generated_at is invalid") from exc
    if generated_at.tzinfo is None:
        raise RuntimeError("retrain reconciliation generated_at must include a timezone")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age_hours = (current.astimezone(timezone.utc) - generated_at.astimezone(timezone.utc)).total_seconds() / 3600
    if age_hours < -1.0:
        raise RuntimeError("retrain reconciliation evidence is dated in the future")
    if age_hours > max_report_age_hours:
        raise RuntimeError(
            f"retrain reconciliation evidence is {age_hours:.1f}h old; current evidence is required"
        )

    active_source_date = _active_options_date(data_dir)
    source = manifest.get("source_reconciliation") or {}
    quote = manifest.get("quote_quality") or {}
    source_dates = {str(value) for value in (source.get("source_date"), quote.get("source_date")) if value}
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
    args = parser.parse_args()

    result = verify_retrain_data_gate(
        data_dir=args.data_dir,
        max_report_age_hours=args.max_report_age_hours,
    )
    print(
        "Retrain data gate passed: "
        f"source={result['source_date']} manifest={result['manifest_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
