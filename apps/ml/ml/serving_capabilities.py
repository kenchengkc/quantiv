"""The exact model head/cohort combinations authorized for live forecasts."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


COHORTS = frozenset({"strict_options", "optionless"})


def _cohorts(value: Any) -> tuple[str, ...]:
    if (not isinstance(value, (list, tuple)) or not value
            or any(not isinstance(item, str) or item not in COHORTS for item in value)
            or len(value) != len(set(value))):
        raise ValueError("serving cohorts must be a nonempty, unique known cohort list")
    return tuple(sorted(value))


def validate_serving_capabilities(
    capabilities: Any, horizons: Sequence[int],
) -> dict[str, list[str]]:
    if not isinstance(capabilities, Mapping) or set(capabilities) != {str(h) for h in horizons}:
        raise ValueError("serving capabilities must declare every exact bundle horizon")
    return {str(h): list(_cohorts(capabilities[str(h)])) for h in sorted(horizons)}


def allowed_cohorts(
    manifest: Mapping[str, Any] | None, horizon: int, metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    """Intersect authenticated admission with the model's own feature contract.

    Existing bundles retain their declared cohorts; pre-cohort metadata remains
    strict-options only. A capability may narrow that declaration, never expand it.
    """
    supported = _cohorts(metadata.get("supported_cohorts", ["strict_options"]))
    if manifest is None:
        return supported
    horizons = manifest.get("horizons") or []
    if horizon not in horizons:
        return ()
    if "serving_capabilities" not in manifest:
        return supported
    capabilities = validate_serving_capabilities(manifest["serving_capabilities"], horizons)
    admitted = tuple(capabilities[str(horizon)])
    if not set(admitted).issubset(supported):
        raise ValueError("serving capability exceeds the model's supported cohorts")
    return admitted


def assert_forecast_capability(
    manifest: Mapping[str, Any] | None, horizon: int,
    metadata: Mapping[str, Any], cohort: str,
) -> None:
    if cohort not in allowed_cohorts(manifest, horizon, metadata):
        raise ValueError(f"T-{horizon} cohort {cohort!r} is not admitted for serving")
