#!/usr/bin/env python3
"""Validate Quantiv's committed public research contracts without extra deps."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC = REPO_ROOT / "apps" / "frontend" / "public"
SCHEMAS = REPO_ROOT / "schemas"
SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
SOURCE_UNIVERSE_SCHEMA = "quantiv.historical-event-universe.v1"
PREVIEW_UNIVERSE_SCHEMA = "quantiv.historical-event-universe.preview.v1"
DISPLAY_FORECAST_METHODS = {
    "ml",
    "options_math",
    "options_indicative",
    "historical",
    "historical_prior",
}


class ContractError(ValueError):
    pass


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON {path}: {exc}") from exc


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{label} must be an array")
    return value


def _required(obj: dict[str, Any], label: str, *keys: str) -> None:
    missing = [key for key in keys if key not in obj]
    if missing:
        raise ContractError(f"{label} missing required keys: {', '.join(missing)}")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{label} must be a non-empty string")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f"{label} must be a finite number")
    return float(value)


def _date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ContractError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{label} must be an ISO date") from exc


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_schema_documents() -> None:
    files = sorted(SCHEMAS.glob("*.schema.json"))
    if not files:
        raise ContractError("no public JSON Schema files found")
    for path in files:
        schema = _object(_read(path), str(path))
        _required(schema, str(path), "$schema", "$id", "title", "type")
        if schema["$schema"] != "https://json-schema.org/draft/2020-12/schema":
            raise ContractError(f"{path} must use JSON Schema draft 2020-12")
        if schema["type"] != "object":
            raise ContractError(f"{path} top-level type must be object")


def validate_screener() -> None:
    path = PUBLIC / "screener.json"
    payload = _object(_read(path), str(path))
    _required(payload, "screener", "metadata", "events")
    metadata = _object(payload["metadata"], "screener.metadata")
    _required(metadata, "screener.metadata", "version", "as_of_date", "generated_at", "event_count")
    if metadata["version"] != "v1":
        raise ContractError("screener.metadata.version must be v1")
    events = _list(payload["events"], "screener.events")
    if metadata["event_count"] != len(events):
        raise ContractError("screener.metadata.event_count must equal len(events)")
    for index, event_raw in enumerate(events):
        event = _object(event_raw, f"screener.events[{index}]")
        _required(event, f"screener.events[{index}]", "ticker", "earnings_date", "as_of_date", "em_method")
        symbol = _string(event["ticker"], f"screener.events[{index}].ticker")
        if not SYMBOL_RE.fullmatch(symbol):
            raise ContractError(f"invalid ticker in screener row: {symbol}")


def validate_symbol_payloads() -> None:
    paths = sorted((PUBLIC / "symbols").glob("*.json"))
    if not paths:
        raise ContractError("no symbol research payloads found")
    for path in paths:
        payload = _object(_read(path), str(path))
        _required(payload, path.name, "symbol", "as_of_date", "straddle_features", "earnings_history")
        symbol = _string(payload["symbol"], f"{path.name}.symbol")
        if symbol != path.stem or not SYMBOL_RE.fullmatch(symbol):
            raise ContractError(f"symbol payload/path mismatch: {path.name} -> {symbol}")
        _list(payload["straddle_features"], f"{path.name}.straddle_features")
        _list(payload["earnings_history"], f"{path.name}.earnings_history")



def _positive_optional(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _resolved_forecast_signature(
    payload: dict[str, Any] | None,
    *,
    historical_fallback: float | None = None,
) -> tuple[str, float] | None:
    """Python mirror of frontend resolveDisplayForecastCompat()."""

    explicit_historical = _positive_optional(historical_fallback)
    if not payload:
        return ("historical", explicit_historical) if explicit_historical else None

    ml = _positive_optional(payload.get("em_ml_pct"))
    if ml is not None:
        return ("ml", ml)

    canonical = _positive_optional(payload.get("display_forecast_pct"))
    canonical_method = payload.get("display_forecast_method")
    if canonical is not None and canonical_method == "ml":
        return ("ml", canonical)

    iv = _positive_optional(payload.get("em_iv_pct"))
    if iv is None:
        iv = _positive_optional(payload.get("iv_pct"))
    if iv is not None:
        return ("options_math", iv)

    straddle = _positive_optional(payload.get("em_straddle_pct"))
    if straddle is None:
        straddle = _positive_optional(payload.get("straddle_pct"))
    if straddle is not None:
        return ("options_math", straddle)

    if canonical is not None and canonical_method in {
        "options_math",
        "options_indicative",
    }:
        return (str(canonical_method), canonical)

    canonical_historical = (
        canonical
        if canonical is not None
        and canonical_method in {"historical", "historical_prior"}
        else None
    )
    historical = (
        explicit_historical
        or canonical_historical
        or _positive_optional(payload.get("hist_move_med_4q"))
        or _positive_optional(payload.get("hist_move_avg_4q"))
    )
    if historical is not None:
        method = (
            "historical"
            if explicit_historical is not None
            else str(canonical_method or "historical")
            if canonical_historical is not None
            else "historical"
        )
        return (method, historical)

    if canonical is not None:
        return (str(canonical_method), canonical) if canonical_method else None
    return None


def _symbol_historical_median(
    payload: dict[str, Any],
    event_date: str,
) -> tuple[float | None, int]:
    realized: list[tuple[str, float]] = []
    for row in payload.get("earnings_history") or []:
        if not isinstance(row, dict):
            continue
        row_date = str(row.get("date") or "")[:10]
        actual_raw = row.get("actual")
        actual = None
        if isinstance(actual_raw, (int, float)) and not isinstance(actual_raw, bool):
            number = float(actual_raw)
            if math.isfinite(number):
                actual = abs(number)
        if row_date and actual is not None:
            realized.append((row_date, actual))

    realized.sort(key=lambda item: item[0])
    prior = [value for row_date, value in realized[-12:] if row_date < event_date][-4:]
    if len(prior) < 2:
        return None, len(prior)
    ordered = sorted(prior)
    middle = len(ordered) // 2
    median_value = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2.0
    )
    return median_value, len(prior)


def _symbol_page_components(
    payload: dict[str, Any],
    event_date: str,
) -> dict[str, float | None]:
    expected = payload.get("expected_move")
    expected = expected if isinstance(expected, dict) else {}
    expected_matches = str(expected.get("earnings_date") or "")[:10] == event_date
    active_expected = expected if expected_matches else {}

    history_row = next(
        (
            row
            for row in payload.get("earnings_history") or []
            if isinstance(row, dict)
            and str(row.get("date") or "")[:10] == event_date
        ),
        None,
    )

    history_iv = None
    history_straddle = None
    history_ml = None
    if isinstance(history_row, dict):
        timing = str(history_row.get("timing") or "").lower()
        after_close = (
            timing in {"amc", "after_market_close", "after_close"}
            or "after" in timing
        )
        implied_as_of = str(history_row.get("implied_as_of") or "")[:10]
        quality = history_row.get("implied_quality_status")
        option_point_in_time = bool(
            implied_as_of
            and (implied_as_of <= event_date if after_close else implied_as_of < event_date)
            and quality in {None, "decision_eligible_eod"}
        )
        atm_iv = _positive_optional(history_row.get("implied_atm_iv"))
        dte = _positive_optional(history_row.get("implied_dte"))
        if option_point_in_time and atm_iv is not None and dte is not None:
            history_iv = atm_iv * math.sqrt(dte / 365.0)
        if option_point_in_time:
            history_straddle = _positive_optional(history_row.get("implied"))

        ml_snapshot = str(history_row.get("ml_snapshot_date") or "")[:10]
        if ml_snapshot and ml_snapshot < event_date:
            history_ml = _positive_optional(history_row.get("em_ml_pct"))

    historical, _count = _symbol_historical_median(payload, event_date)
    return {
        "ml": _positive_optional(active_expected.get("em_ml_pct")) or history_ml,
        "iv": _positive_optional(active_expected.get("iv_pct")) or history_iv,
        "straddle": _positive_optional(active_expected.get("straddle_pct")) or history_straddle,
        "historical": historical,
    }


def _symbol_page_signature(
    payload: dict[str, Any],
    event_date: str,
) -> tuple[tuple[str, float] | None, dict[str, float | None]]:
    expected = payload.get("expected_move")
    expected = expected if isinstance(expected, dict) else {}
    active_expected = (
        expected
        if str(expected.get("earnings_date") or "")[:10] == event_date
        else {}
    )
    components = _symbol_page_components(payload, event_date)
    headline_fields = {
        **active_expected,
        "em_ml_pct": components["ml"],
        "iv_pct": components["iv"],
        "straddle_pct": components["straddle"],
    }
    return (
        _resolved_forecast_signature(
            headline_fields,
            historical_fallback=components["historical"],
        ),
        components,
    )


def validate_forecast_surface_parity() -> None:
    """Require the calendar to render the exact ticker-page forecast hierarchy."""

    reference_start: str | None = None
    reference_end: str | None = None
    reference_path = PUBLIC / "calendar-reference.json"
    if reference_path.is_file():
        reference = _object(_read(reference_path), str(reference_path))
        window = reference.get("window")
        if isinstance(window, dict):
            reference_start = str(window.get("start") or "")[:10] or None
            reference_end = str(window.get("end") or "")[:10] or None

    week_events: dict[
        tuple[str, str],
        tuple[tuple[str, float] | None, str, dict[str, Any], str],
    ] = {}
    weeks_dir = PUBLIC / "weeks"
    for path in sorted(weeks_dir.glob("*.json")):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", path.name) is None:
            continue
        payload = _object(_read(path), str(path))
        metadata = payload.get("metadata")
        artifact_as_of = (
            str(metadata.get("as_of_date") or "")[:10]
            if isinstance(metadata, dict)
            else ""
        )
        window = payload.get("window")
        if (
            reference_start
            and reference_end
            and isinstance(window, dict)
            and (
                str(window.get("end") or "")[:10] < reference_start
                or str(window.get("start") or "")[:10] > reference_end
            )
        ):
            continue

        for index, event_raw in enumerate(
            _list(payload.get("events"), f"{path.name}.events")
        ):
            event = _object(event_raw, f"{path.name}.events[{index}]")
            ticker = event.get("ticker")
            earnings_date = event.get("earnings_date")
            if not isinstance(ticker, str) or not isinstance(earnings_date, str):
                continue
            identity = (ticker.upper(), earnings_date[:10])
            signature = _resolved_forecast_signature(event)
            event_as_of = artifact_as_of or str(event.get("as_of_date") or "")[:10]
            if not event_as_of:
                raise ContractError(
                    f"{path.name} is missing a deterministic as-of date for "
                    f"{identity[0]} {identity[1]}"
                )
            existing = week_events.get(identity)
            if existing is not None and existing[0] != signature:
                raise ContractError(
                    f"calendar forecast disagrees across week artifacts for "
                    f"{identity[0]} {identity[1]}"
                )
            week_events[identity] = (signature, path.name, event, event_as_of)
    component_fields = {
        "ml": "em_ml_pct",
        "iv": "em_iv_pct",
        "straddle": "em_straddle_pct",
        "historical": "hist_move_med_4q",
    }

    for identity, (
        calendar_signature,
        calendar_path,
        calendar_event,
        artifact_as_of,
    ) in week_events.items():
        ticker, event_date = identity
        symbol_path = PUBLIC / "symbols" / f"{ticker}.json"
        if not symbol_path.is_file():
            continue
        symbol_payload = _object(_read(symbol_path), str(symbol_path))
        symbol_signature, components = _symbol_page_signature(
            symbol_payload,
            event_date,
        )

        if calendar_signature is None and symbol_signature is not None:
            raise ContractError(
                f"calendar is missing resolved headline forecast for "
                f"{ticker} {event_date} while {symbol_path.name} renders one"
            )
        if calendar_signature is not None and symbol_signature is None:
            raise ContractError(
                f"{symbol_path.name} cannot resolve a headline forecast for "
                f"{ticker} {event_date} while {calendar_path} renders one"
            )
        if calendar_signature is not None and symbol_signature is not None:
            if (
                calendar_signature[0] != symbol_signature[0]
                or not math.isclose(
                    calendar_signature[1],
                    symbol_signature[1],
                    rel_tol=1e-6,
                    abs_tol=1e-6,
                )
            ):
                raise ContractError(
                    f"calendar/symbol resolved headline mismatch for "
                    f"{ticker} {event_date}: "
                    f"{calendar_signature} != {symbol_signature}"
                )

        # Rows that were already reported at the artifact's own snapshot must
        # also carry every ticker-page component needed by the homepage hover
        # card. Use the committed artifact as-of date, never the CI runner's
        # wall clock: otherwise an unchanged pre-event snapshot begins failing
        # simply because midnight passes and the event date arrives.
        if event_date > artifact_as_of:
            continue
        for component, field in component_fields.items():
            expected_value = components.get(component)
            if expected_value is None:
                continue
            calendar_value = _positive_optional(calendar_event.get(field))
            if calendar_value is None or not math.isclose(
                calendar_value,
                expected_value,
                rel_tol=1e-6,
                abs_tol=1e-6,
            ):
                raise ContractError(
                    f"calendar hover component mismatch for {ticker} {event_date} "
                    f"{component}: {calendar_value} != {expected_value}"
                )



def validate_dashboard_evidence() -> None:
    path = PUBLIC / "evidence" / "forecast.json"
    payload = _object(_read(path), str(path))
    _required(payload, "forecast evidence", "schema", "receipt_id", "validated_at", "quality", "coverage", "controls", "artifact_bundles")
    if payload["schema"] != "quantiv.dashboard-evidence.v1":
        raise ContractError("forecast evidence schema discriminator changed")
    receipt = _string(payload["receipt_id"], "forecast evidence.receipt_id")
    if not SHA256_RE.fullmatch(receipt):
        raise ContractError("forecast evidence receipt_id must be SHA-256")
    quality = _object(payload["quality"], "forecast evidence.quality")
    _required(quality, "forecast evidence.quality", "status", "issue_count", "issue_codes")
    coverage = _object(payload["coverage"], "forecast evidence.coverage")
    _required(coverage, "forecast evidence.coverage", "rows", "symbols", "events", "horizons")


def validate_control_plane() -> None:
    path = PUBLIC / "control-plane.json"
    payload = _object(_read(path), str(path))
    _required(payload, "control plane", "schema", "generated_at", "status", "publication_eligible", "data", "model", "release", "exceptions")
    if payload["schema"] != "quantiv.control-plane.v2":
        raise ContractError("control-plane schema discriminator changed")
    if not isinstance(payload["publication_eligible"], bool):
        raise ContractError("control-plane publication_eligible must be boolean")
    _object(payload["data"], "control-plane.data")
    _object(payload["model"], "control-plane.model")
    _object(payload["release"], "control-plane.release")
    _list(payload["exceptions"], "control-plane.exceptions")


def validate_model_validation() -> None:
    path = PUBLIC / "evidence" / "model-validation.json"
    payload = _object(_read(path), str(path))
    _required(payload, "model validation", "schema", "generated_at", "model_source", "summary", "horizons", "validation_protocol", "current_evidence")
    if payload["schema"] != "quantiv.public-model-validation.v1":
        raise ContractError("model-validation schema discriminator changed")
    source = _object(payload["model_source"], "model-validation.model_source")
    _required(source, "model-validation.model_source", "kind", "bundle_id", "artifact_sha256")
    if source["kind"] not in {"signed_champion", "baked_fallback"}:
        raise ContractError("model-validation model source kind is unsupported")
    summary = _object(payload["summary"], "model-validation.summary")
    _required(summary, "model-validation.summary", "supported_horizons", "validation_row_observations", "weighted_model_mae", "weighted_straddle_mae", "weighted_relative_mae_improvement", "weighted_coverage")
    horizons = _list(payload["horizons"], "model-validation.horizons")
    expected = list(summary["supported_horizons"])
    actual = [row.get("horizon_days") for row in horizons if isinstance(row, dict)]
    if actual != expected:
        raise ContractError(f"model-validation horizon rows {actual} do not match summary {expected}")
    protocol = _object(payload["validation_protocol"], "model-validation.validation_protocol")
    if protocol.get("decision_scope") != "end_of_day_research" or protocol.get("live_trading_eligible") is not False:
        raise ContractError("model-validation decision scope must remain EOD research-only")


def _timing_bucket(value: str) -> str:
    normalized = value.lower()
    if "before" in normalized or normalized == "bmo":
        return "bmo"
    if "after" in normalized or normalized == "amc":
        return "amc"
    return "other"


def _validate_preview_research_history(payload: dict[str, Any]) -> None:
    source = _object(payload.get("source"), "research-history.source")
    if source.get("kind") != "display_payload_fallback":
        raise ContractError("research-history preview must identify display_payload_fallback")
    if source.get("completeness") != "display_limited":
        raise ContractError("research-history preview must be marked display_limited")
    if payload.get("decision_scope") != "end_of_day_research":
        raise ContractError("historical research preview must remain end_of_day_research")
    if payload.get("live_trading_eligible") is not False:
        raise ContractError("historical research preview must never be live-trading eligible")
    events = _list(payload.get("events"), "research-history.events")
    if payload.get("event_count") != len(events):
        raise ContractError("research-history.event_count must equal len(events)")


def _validate_retired_membership(source: dict[str, Any]) -> None:
    source_as_of = _date(source.get("as_of_date"), "research-history.source.as_of_date")
    membership = _object(source.get("retired_membership"), "research-history.source.retired_membership")
    _required(
        membership,
        "research-history.source.retired_membership",
        "status",
        "method",
        "configured_tickers",
        "earnings",
        "corporate_actions",
        "missing_earnings_tickers",
        "installed_event_rows",
        "corporate_action_control",
    )
    if membership.get("status") != "verified":
        raise ContractError("retired membership must be verified")
    if membership.get("method") != "bounded_provider_query_for_explicit_retirement_ledger":
        raise ContractError("retired membership method is unsupported")
    configured = _list(membership["configured_tickers"], "retired membership configured_tickers")
    if len(configured) != len(set(configured)) or any(
        not isinstance(ticker, str) or not SYMBOL_RE.fullmatch(ticker) for ticker in configured
    ):
        raise ContractError("retired membership configured_tickers are invalid")

    def source_set(raw: Any, label: str) -> list[Any]:
        evidence = _object(raw, label)
        _required(evidence, label, "rows", "row_count", "pages", "sha256")
        rows = _list(evidence["rows"], f"{label}.rows")
        if not isinstance(evidence["row_count"], int) or evidence["row_count"] != len(rows):
            raise ContractError(f"{label} row_count does not match rows")
        if not isinstance(evidence["pages"], int) or evidence["pages"] < 0:
            raise ContractError(f"{label}.pages must be nonnegative")
        digest = _string(evidence["sha256"], f"{label}.sha256")
        if digest != _canonical_digest(rows):
            raise ContractError(f"{label} digest does not match retained rows")
        return rows

    earnings = source_set(membership["earnings"], "retired membership earnings")
    actions = _object(membership["corporate_actions"], "retired membership corporate_actions")
    _required(actions, "retired membership corporate_actions", "splits", "dividends")
    splits = source_set(actions["splits"], "retired membership splits")
    dividends = source_set(actions["dividends"], "retired membership dividends")

    configured_set = set(configured)
    returned_tickers: set[str] = set()
    for index, raw in enumerate(earnings):
        row = _object(raw, f"retired membership earnings.rows[{index}]")
        _required(row, f"retired membership earnings.rows[{index}]", "ticker", "date", "timing")
        ticker = _string(row["ticker"], f"retired membership earnings.rows[{index}].ticker")
        if ticker not in configured_set:
            raise ContractError("retired earnings source contains a ticker outside the retirement ledger")
        event_date = _date(row["date"], f"retired membership earnings.rows[{index}].date")
        if event_date >= source_as_of:
            raise ContractError("retired earnings source contains an event on/after source as-of date")
        if row["timing"] not in {"before_market_open", "after_market_close", "unknown"}:
            raise ContractError("retired earnings source contains unsupported timing")
        returned_tickers.add(ticker)

    for label, rows, value_keys in (
        ("split", splits, ("to_factor", "for_factor")),
        ("dividend", dividends, ("amount",)),
    ):
        for index, raw in enumerate(rows):
            row = _object(raw, f"retired membership {label}[{index}]")
            ticker = _string(row.get("ticker"), f"retired membership {label}[{index}].ticker")
            if ticker not in configured_set:
                raise ContractError(f"retired {label} source contains a ticker outside the retirement ledger")
            action_date = _date(row.get("ex_date"), f"retired membership {label}[{index}].ex_date")
            if action_date > source_as_of:
                raise ContractError(f"retired {label} source contains an action after source as-of date")
            for key in value_keys:
                value = _finite(row.get(key), f"retired membership {label}[{index}].{key}")
                if value < 0 or (label == "split" and value <= 0):
                    raise ContractError(f"retired {label} source contains invalid {key}")

    missing = _list(membership["missing_earnings_tickers"], "retired membership missing_earnings_tickers")
    if set(missing) != configured_set - returned_tickers:
        raise ContractError("retired membership missing-ticker audit does not reconcile")
    installed = membership["installed_event_rows"]
    if not isinstance(installed, int) or installed < 0 or installed > len(earnings):
        raise ContractError("retired membership installed_event_rows is invalid")
    control = _object(membership["corporate_action_control"], "retired membership corporate_action_control")
    _required(
        control,
        "retired membership corporate_action_control",
        "receipt_id",
        "source_options_date",
        "split_rows",
        "dividend_rows",
        "retired_split_rows",
        "retired_dividend_rows",
    )
    _string(control["receipt_id"], "retired membership corporate_action_control.receipt_id")
    action_source_date = _date(
        control["source_options_date"],
        "retired membership corporate_action_control.source_options_date",
    )
    if action_source_date > source_as_of:
        raise ContractError("corporate-action source date is after research source as-of date")
    for key in ("split_rows", "dividend_rows", "retired_split_rows", "retired_dividend_rows"):
        if not isinstance(control[key], int) or control[key] < 0:
            raise ContractError(f"retired membership corporate_action_control.{key} must be nonnegative")
    if control["retired_split_rows"] != len(splits) or control["retired_dividend_rows"] != len(dividends):
        raise ContractError("retired corporate-action counts do not match retained source rows")
    if control["split_rows"] < control["retired_split_rows"] or control["dividend_rows"] < control["retired_dividend_rows"]:
        raise ContractError("combined corporate-action counts cannot be smaller than retired source rows")


def validate_research_history() -> None:
    path = PUBLIC / "research-history.json"
    payload = _object(_read(path), str(path))
    schema = payload.get("schema")
    source = _object(payload.get("source"), "research-history.source")

    if schema == PREVIEW_UNIVERSE_SCHEMA:
        _validate_preview_research_history(payload)
        return

    # The checked-in corpus predates the explicit preview discriminator. Permit
    # that one migration shape in repository validation only; frontend prebuild
    # rewrites it to PREVIEW_UNIVERSE_SCHEMA unless a source-level release has
    # already been materialized. It can never satisfy the source-level branch.
    if (
        schema == SOURCE_UNIVERSE_SCHEMA
        and source.get("kind") is None
        and "symbol_payloads" in source
        and payload.get("universe_id") is None
    ):
        if payload.get("decision_scope") != "end_of_day_research" or payload.get("live_trading_eligible") is not False:
            raise ContractError("legacy historical research must remain EOD research-only")
        events = _list(payload.get("events"), "research-history.events")
        if payload.get("event_count") != len(events):
            raise ContractError("legacy research-history event_count must equal len(events)")
        return

    if schema != SOURCE_UNIVERSE_SCHEMA:
        raise ContractError("research-history schema discriminator changed")
    if source.get("kind") != "analytical_duckdb" or source.get("completeness") != "source_level":
        raise ContractError("source-level research history must identify analytical_duckdb/source_level")
    _validate_retired_membership(source)
    if payload.get("decision_scope") != "end_of_day_research":
        raise ContractError("historical research must remain end_of_day_research")
    if payload.get("live_trading_eligible") is not False:
        raise ContractError("historical research must never be live-trading eligible")

    events = _list(payload.get("events"), "research-history.events")
    if payload.get("event_count") != len(events):
        raise ContractError("research-history.event_count must equal len(events)")
    universe_id = _string(payload.get("universe_id"), "research-history.universe_id")
    if not SHA256_RE.fullmatch(universe_id):
        raise ContractError("research-history universe_id must be SHA-256")
    audit = _object(payload.get("audit"), "research-history.audit")
    _required(audit, "research-history.audit", "candidate_event_count", "eligible_event_count", "excluded_event_count", "exclusion_counts", "exclusions")
    exclusions = _list(audit["exclusions"], "research-history.audit.exclusions")
    if audit["eligible_event_count"] != len(events):
        raise ContractError("research-history eligible count does not match events")
    if audit["excluded_event_count"] != len(exclusions):
        raise ContractError("research-history excluded count does not match exclusions")
    if audit["candidate_event_count"] != len(events) + len(exclusions):
        raise ContractError("research-history candidate/eligible/excluded counts do not reconcile")

    seen: set[tuple[str, str]] = set()
    source_as_of = _date(source.get("as_of_date"), "research-history.source.as_of_date")
    for index, raw in enumerate(events):
        event = _object(raw, f"research-history.events[{index}]")
        _required(
            event,
            f"research-history.events[{index}]",
            "ticker", "date", "timing", "actual", "realized_abs", "implied",
            "implied_as_of", "implied_expiration", "implied_quality_status",
            "edge", "ratio", "outside_implied", "realized_window",
        )
        ticker = _string(event["ticker"], f"research-history.events[{index}].ticker")
        if not SYMBOL_RE.fullmatch(ticker):
            raise ContractError(f"invalid ticker in historical research row: {ticker}")
        event_date = _date(event["date"], f"research-history.events[{index}].date")
        if event_date >= source_as_of:
            raise ContractError("historical research contains an event on/after source as-of date")
        identity = (ticker, event["date"])
        if identity in seen:
            raise ContractError(f"duplicate historical research event: {ticker} {event['date']}")
        seen.add(identity)

        actual = _finite(event["actual"], f"research-history.events[{index}].actual")
        realized_abs = _finite(event["realized_abs"], f"research-history.events[{index}].realized_abs")
        implied = _finite(event["implied"], f"research-history.events[{index}].implied")
        edge = _finite(event["edge"], f"research-history.events[{index}].edge")
        ratio = _finite(event["ratio"], f"research-history.events[{index}].ratio")
        if implied <= 0 or realized_abs < 0 or ratio < 0:
            raise ContractError("historical research contains invalid move magnitudes")
        if not math.isclose(realized_abs, abs(actual), rel_tol=1e-12, abs_tol=1e-12):
            raise ContractError("historical realized_abs does not equal abs(actual)")
        if not math.isclose(edge, realized_abs - implied, rel_tol=1e-12, abs_tol=1e-12):
            raise ContractError("historical edge arithmetic mismatch")
        if not math.isclose(ratio, realized_abs / implied, rel_tol=1e-12, abs_tol=1e-12):
            raise ContractError("historical ratio arithmetic mismatch")
        if event["outside_implied"] != (realized_abs > implied):
            raise ContractError("historical outside_implied arithmetic mismatch")
        if event["implied_quality_status"] != "decision_eligible_eod":
            raise ContractError("historical option evidence bypassed decision-eligible quality")

        implied_as_of = _date(event["implied_as_of"], f"research-history.events[{index}].implied_as_of")
        expiry = _date(event["implied_expiration"], f"research-history.events[{index}].implied_expiration")
        bucket = _timing_bucket(str(event["timing"]))
        if bucket == "amc":
            if implied_as_of > event_date or expiry <= event_date:
                raise ContractError("AMC historical option window violates session eligibility")
        elif implied_as_of >= event_date or expiry < event_date:
            raise ContractError("non-AMC historical option window violates session eligibility")

        window = _object(event["realized_window"], f"research-history.events[{index}].realized_window")
        pre_date = _date(window.get("pre_date"), f"research-history.events[{index}].realized_window.pre_date")
        post_date = _date(window.get("post_date"), f"research-history.events[{index}].realized_window.post_date")
        pre_price = _finite(window.get("pre_price"), f"research-history.events[{index}].realized_window.pre_price")
        post_adjusted = _finite(window.get("post_price_adjusted"), f"research-history.events[{index}].realized_window.post_price_adjusted")
        if pre_price <= 0 or post_adjusted <= 0:
            raise ContractError("historical realized price window contains nonpositive prices")
        if post_date > source_as_of:
            raise ContractError("historical realized price window extends after source as-of date")
        if bucket == "bmo":
            valid_window = pre_date < event_date and post_date >= event_date
        elif bucket == "amc":
            valid_window = pre_date <= event_date and post_date > event_date
        else:
            valid_window = pre_date < event_date and post_date > event_date
        if not valid_window:
            raise ContractError("historical realized price window violates session eligibility")
        expected_actual = post_adjusted / pre_price - 1.0
        if not math.isclose(actual, expected_actual, rel_tol=1e-12, abs_tol=1e-12):
            raise ContractError("historical realized move does not match adjusted price endpoints")

    identity = {
        key: value
        for key, value in payload.items()
        if key not in {"universe_id", "generated_at"}
    }
    expected_universe_id = _canonical_digest(identity)
    if universe_id != expected_universe_id:
        raise ContractError("research-history universe_id does not match canonical content")


def validate_repo() -> list[str]:
    checks: list[tuple[str, Callable[[], None]]] = [
        ("schema documents", validate_schema_documents),
        ("screener", validate_screener),
        ("symbol payloads", validate_symbol_payloads),
        ("forecast surface parity", validate_forecast_surface_parity),
        ("forecast evidence", validate_dashboard_evidence),
        ("control plane", validate_control_plane),
        ("model validation", validate_model_validation),
    ]

    # research-history.json is generated/materialized state rather than a
    # guaranteed member of a clean source checkout. Validate it strictly when
    # present, but do not make a source-only frontend publication impossible
    # when the artifact is intentionally absent. Frontend prebuild preserves a
    # source-level artifact when available or constructs the explicit
    # display-limited preview fallback otherwise.
    if (PUBLIC / "research-history.json").is_file():
        checks.append(("research history", validate_research_history))

    passed: list[str] = []
    for name, check in checks:
        check()
        passed.append(name)
    return passed


def main() -> int:
    try:
        passed = validate_repo()
    except ContractError as exc:
        print(f"public contract validation failed: {exc}")
        return 1
    print("public contracts passed: " + ", ".join(passed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
