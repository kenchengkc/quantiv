"""Current signed forecast admission, independent of model rollback or caches."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ml.model_bundle import MONITOR_SCHEMA, ModelBundleError, verify_signed_payload
from ml.source_coverage import completed_market_session
from ml.forecast_snapshot import forecast_snapshot_id, forecast_snapshot_key
from services import r2_models


@dataclass(frozen=True)
class PublicationAuthority:
    cache_identity: str
    bundle_id: str
    snapshot_date: date
    eligible_event_keys: frozenset[str]
    eligible_snapshot_ids: frozenset[str]
    snapshot_bindings: dict[str, str]

    def allows(self, symbol: str, horizon: int, earnings_date: date | None = None) -> bool:
        expected_event = self.snapshot_date + timedelta(days=horizon)
        if earnings_date is not None and earnings_date != expected_event:
            return False
        return f"{symbol.strip().upper()}|{expected_event.isoformat()}" in self.eligible_event_keys

    def accepts_snapshot(self, symbol: str, horizon: int, snapshot: dict) -> bool:
        try:
            snapshot_date = date.fromisoformat(str(snapshot["snapshot_date"])[:10])
            earnings_date = date.fromisoformat(str(snapshot["earnings_date"])[:10])
            identity = self.snapshot_identity(symbol, horizon, snapshot)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return False
        return (snapshot_date == self.snapshot_date and self.allows(symbol, horizon, earnings_date)
                and self.snapshot_bindings.get(forecast_snapshot_key({"act_symbol": symbol,
                    "earnings_date": earnings_date, "model_horizon": horizon})) == identity)

    def snapshot_identity(self, symbol: str, horizon: int, snapshot: dict) -> str:
        return forecast_snapshot_id({**snapshot, "act_symbol": symbol, "model_horizon": horizon,
            "spot_price": snapshot.get("spot_at_snapshot"), "scored_at": snapshot.get("forecast_scored_at")})

    def accepts_cached(self, symbol: str, horizon: int, payload: dict) -> bool:
        if not isinstance(payload.get("_snapshot_id"), str):
            return False
        try:
            event = date.fromisoformat(str(payload["earnings_date"])[:10])
            snapshot = date.fromisoformat(str(payload["feature_snapshot_date"])[:10])
        except (KeyError, TypeError, ValueError):
            return False
        return (snapshot == self.snapshot_date and self.allows(symbol, horizon, event)
                and self.snapshot_bindings.get(forecast_snapshot_key({"act_symbol": symbol,
                    "earnings_date": event, "model_horizon": horizon})) == payload.get("_snapshot_id"))


def load_current_authority(bundle_id: str, *, now: datetime | None = None) -> PublicationAuthority:
    """Read current commit markers on every call; no stale authority fallback."""
    bucket = os.getenv("R2_BUCKET")
    client = r2_models._build_client() if bucket else None
    if client is None:
        raise ModelBundleError("current forecast publication authority is unavailable")
    current = now or datetime.now(timezone.utc)
    release = r2_models._get_json(client, bucket, "control/current_data_release.json")
    release_id = release.get("release_id")
    if (not isinstance(release_id, str) or re.fullmatch(r"[0-9a-f]{64}", release_id) is None
            or release.get("manifest") != f"control/releases/{release_id}.json"):
        raise ModelBundleError("current data-release identity is invalid")
    receipt = r2_models._get_json(client, bucket, "models/monitoring/latest_monitoring.receipt.json")
    verified = verify_signed_payload(receipt)
    if verified.get("schema") != MONITOR_SCHEMA:
        raise ModelBundleError("current forecast monitor receipt is invalid")
    raw = client.get_object(Bucket=bucket, Key="models/monitoring/latest_monitoring.json")["Body"].read()
    artifact = verified.get("report") or {}
    if (artifact.get("name") != "latest_monitoring.json" or artifact.get("bytes") != len(raw)
            or artifact.get("sha256") != hashlib.sha256(raw).hexdigest()):
        raise ModelBundleError("current forecast monitor report digest differs")
    report = json.loads(raw)
    snapshot = completed_market_session(current)
    monitored = datetime.fromisoformat(str(report.get("monitored_at", "")).replace("Z", "+00:00"))
    if (monitored.tzinfo is None or monitored > current or current - monitored > timedelta(days=4)
            or report.get("schema") != "quantiv.model-monitoring.v1"
            or report.get("champion_bundle_id") != bundle_id
            or report.get("current_data_release_id") != release_id
            or report.get("snapshot_date") != snapshot.isoformat()
            or verified.get("snapshot_date") != snapshot.isoformat()):
        raise ModelBundleError("current forecast authority differs from the active sources, model or session")
    eligibility = report.get("publication_eligibility") or {}
    events = eligibility.get("eligible_event_keys")
    snapshots = eligibility.get("eligible_snapshot_ids")
    bindings = eligibility.get("eligible_snapshot_bindings")
    if (eligibility.get("schema") != "quantiv.event-forecast-eligibility.v1"
            or report.get("status") not in {"passed", "partial", "failed"}
            or not isinstance(events, list) or any(not isinstance(value, str) for value in events)
            or len(events) != len(set(events))
            or not isinstance(snapshots, list)
            or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in snapshots)
            or len(snapshots) != len(set(snapshots))
            or not isinstance(bindings, dict) or len(bindings) != len(snapshots)
            or set(bindings.values()) != set(snapshots)
            or bool(events) != bool(snapshots)
            or (report["status"] == "failed" and events)):
        raise ModelBundleError("current forecast event eligibility is invalid")
    identity = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return PublicationAuthority(f"{release_id}:{identity}", bundle_id, snapshot, frozenset(events), frozenset(snapshots), bindings)
