"""Immutable point-in-time forecast ledger and event cutoff policy.

The ledger is the audit trail for earnings forecasts.  A row is never made
"more pre-event" after the fact: eligibility is derived from the event timing,
canonical NYSE sessions, the feature snapshot timestamp, quote timestamps and
the model scoring timestamp.

Policy:
- BMO/unknown: features may use the previous NYSE session through its close.
  The prediction itself must be created before the earnings calendar day begins
  in New York. This supports normal nightly batch processing while excluding
  any event-morning computation.
- AMC: the research cutoff is five minutes before the event-day close.  A
  same-day row is eligible only when it carries timestamped intraday inputs that
  are demonstrably no later than that cutoff.  Current date-only EOD snapshots
  therefore fail closed to the latest prior-session forecast.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from market_sessions import EASTERN, is_us_market_session, us_market_close
except ModuleNotFoundError:  # imported as scripts.event_forecast_ledger in tests
    from scripts.market_sessions import EASTERN, is_us_market_session, us_market_close

LEDGER_NAME = "event_prediction_ledger.parquet"
PUBLICATION_LEDGER_NAME = "event_prediction_publications.parquet"
AMC_FREEZE_LEAD_MINUTES = 5


def _normalized_timing(value: Any) -> str:
    timing = str(value or "").strip().lower()
    if timing in {"amc", "after_market_close", "after_close"} or "after" in timing:
        return "amc"
    if timing in {"bmo", "before_market_open", "before_open"} or "before" in timing:
        return "bmo"
    return "unknown"


def previous_us_market_session(value: date) -> date:
    candidate = value - timedelta(days=1)
    while not is_us_market_session(candidate):
        candidate -= timedelta(days=1)
    return candidate


def _session_close_at(value: date) -> datetime:
    return datetime.combine(value, us_market_close(value), tzinfo=EASTERN)


def event_cutoffs(
    earnings_date: date,
    timing: Any,
) -> tuple[datetime, datetime]:
    """Return (feature_cutoff_at, prediction_deadline_at), both Eastern.

    The prediction deadline is deliberately stricter than "before the report"
    when no issuer timestamp is available. BMO rows must exist before midnight
    starting the earnings calendar day; AMC rows must already exist before the
    event-day close buffer.
    """
    normalized = _normalized_timing(timing)
    if normalized == "amc" and is_us_market_session(earnings_date):
        close_at = _session_close_at(earnings_date)
        cutoff = close_at - timedelta(minutes=AMC_FREEZE_LEAD_MINUTES)
        return cutoff, cutoff

    prior = previous_us_market_session(earnings_date)
    close_at = _session_close_at(prior)
    deadline = datetime.combine(earnings_date, time(0, 0), tzinfo=EASTERN)
    return close_at, deadline


def _as_aware_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        # Scorer timestamps historically had no timezone.  They were created on
        # GitHub-hosted Linux runners (UTC), so UTC is the only defensible legacy
        # interpretation.  New rows are always written explicitly in UTC.
        ts = ts.tz_localize("UTC")
    return ts.to_pydatetime()


def feature_snapshot_at(row: dict[str, Any]) -> datetime | None:
    explicit = _as_aware_datetime(row.get("feature_snapshot_at"))
    if explicit is not None:
        return explicit.astimezone(EASTERN)

    raw_date = row.get("snapshot_date")
    if raw_date is None:
        return None
    try:
        snapshot_day = pd.Timestamp(raw_date).date()
    except Exception:
        return None
    if not is_us_market_session(snapshot_day):
        return None
    # A date-only daily snapshot is conservatively treated as the official
    # session close.  It can never masquerade as a 15:55 same-day AMC snapshot.
    return _session_close_at(snapshot_day)


def _quote_timestamps(row: dict[str, Any]) -> list[datetime]:
    out: list[datetime] = []
    for key in ("call_quote_timestamp", "put_quote_timestamp"):
        parsed = _as_aware_datetime(row.get(key))
        if parsed is not None:
            out.append(parsed.astimezone(EASTERN))
    return out


def _feature_hash(value: Any) -> str:
    if isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _forecast_id(row: dict[str, Any], feature_hash: str) -> str:
    identity = "|".join(
        [
            str(row.get("act_symbol") or "").upper(),
            str(row.get("earnings_date") or "")[:10],
            str(row.get("scored_at") or ""),
            str(row.get("model_bundle_id") or ""),
            feature_hash,
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def audit_forecast_row(row: dict[str, Any]) -> dict[str, Any]:
    try:
        earnings_date = pd.Timestamp(row.get("earnings_date")).date()
    except Exception:
        return {
            "freeze_eligible": False,
            "freeze_ineligible_reason": "invalid_earnings_date",
        }

    cutoff_at, prediction_deadline_at = event_cutoffs(
        earnings_date, row.get("timing")
    )
    snapshot_at = feature_snapshot_at(row)
    scored_at = _as_aware_datetime(row.get("scored_at"))

    reason: str | None = None
    if snapshot_at is None:
        reason = "missing_feature_snapshot_timestamp"
    elif snapshot_at > cutoff_at:
        reason = "feature_snapshot_after_event_cutoff"
    elif scored_at is None:
        reason = "missing_scored_at"
    elif scored_at.astimezone(EASTERN) > prediction_deadline_at:
        reason = "prediction_created_after_deadline"
    else:
        quote_times = _quote_timestamps(row)
        if any(timestamp > cutoff_at for timestamp in quote_times):
            reason = "option_quote_after_event_cutoff"
        # A same-day AMC options row without timestamp precision cannot be
        # proven pre-cutoff, so fail closed rather than infer.
        elif (
            _normalized_timing(row.get("timing")) == "amc"
            and snapshot_at.date() == earnings_date
            and row.get("atm_iv") is not None
            and not quote_times
        ):
            reason = "same_day_amc_options_without_timestamp"

    feature_hash = _feature_hash(row.get("feature_vector"))
    return {
        "feature_cutoff_at": cutoff_at.astimezone(timezone.utc).isoformat(),
        "prediction_deadline_at": prediction_deadline_at.astimezone(timezone.utc).isoformat(),
        "feature_snapshot_at": (
            snapshot_at.astimezone(timezone.utc).isoformat()
            if snapshot_at is not None
            else None
        ),
        "feature_hash": feature_hash,
        "forecast_id": _forecast_id(row, feature_hash),
        "freeze_eligible": reason is None,
        "freeze_ineligible_reason": reason,
    }


def annotate_forecasts(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    rows = []
    for row in frame.to_dict(orient="records"):
        annotated = dict(row)
        annotated.update(audit_forecast_row(row))
        rows.append(annotated)
    return pd.DataFrame(rows)


def append_event_prediction_ledger(
    frame: pd.DataFrame,
    forecast_dir: Path,
) -> Path | None:
    """Append scored candidates to the immutable forecast ledger.

    Duplicate forecast IDs are ignored, but an existing row is never replaced.
    """
    if frame.empty:
        return None
    annotated = annotate_forecasts(frame)
    path = forecast_dir / LEDGER_NAME
    frames = []
    if path.exists():
        frames.append(pd.read_parquet(path))
    frames.append(annotated)
    ledger = pd.concat(frames, ignore_index=True, sort=False)
    if "forecast_id" not in ledger.columns:
        raise ValueError("event prediction ledger rows require forecast_id")
    ledger = ledger.drop_duplicates(subset=["forecast_id"], keep="first")
    forecast_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    ledger.to_parquet(temporary, index=False)
    temporary.replace(path)
    return path


def latest_eligible_predictions(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty or "freeze_eligible" not in ledger.columns:
        return ledger.iloc[0:0].copy()
    eligible = ledger[ledger["freeze_eligible"].fillna(False).astype(bool)].copy()
    if eligible.empty:
        return eligible

    eligible["_scored"] = pd.to_datetime(
        eligible["scored_at"], errors="coerce", utc=True
    )
    eligible["_snapshot"] = pd.to_datetime(
        eligible["feature_snapshot_at"], errors="coerce", utc=True
    )
    eligible = eligible.sort_values(
        ["act_symbol", "earnings_date", "_snapshot", "_scored"],
        ascending=[True, True, False, False],
        kind="mergesort",
    )
    return eligible.drop_duplicates(
        subset=["act_symbol", "earnings_date"], keep="first"
    ).drop(columns=["_scored", "_snapshot"])


def record_publications(
    forecast_ids: set[str],
    forecast_dir: Path,
    *,
    published_at: datetime | None = None,
) -> Path | None:
    """Append publication receipts without mutating the prediction ledger."""
    clean_ids = sorted({value for value in forecast_ids if value})
    if not clean_ids:
        return None
    at = (published_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows = pd.DataFrame(
        [{"forecast_id": value, "published_at": at.isoformat()} for value in clean_ids]
    )
    path = forecast_dir / PUBLICATION_LEDGER_NAME
    frames = []
    if path.exists():
        frames.append(pd.read_parquet(path))
    frames.append(rows)
    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined = combined.drop_duplicates(subset=["forecast_id"], keep="first")
    temporary = path.with_suffix(path.suffix + ".tmp")
    combined.to_parquet(temporary, index=False)
    temporary.replace(path)
    return path
