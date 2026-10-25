"""Reuse exact published optionless observations while their source session is fresh.

The deployed frontend inventory authenticates the receipt ID and displayed rows;
the receipt authenticates the entire immutable Parquet file, including outputs.
This module supplies candidates, never a publication or monitoring exemption.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import time

import duckdb
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "apps/ml") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps/ml"))

from ml.evidence_receipt import verify_evidence_receipt  # noqa: E402
from ml.live_forecast_validation import STRICT_OPTION_EVIDENCE_COLUMNS  # noqa: E402
from ml.model_artifact import sha256_file  # noqa: E402
from ml.model_bundle import verify_bundle_dir, verify_control_pointer  # noqa: E402
from ml.model_control import prospective_event_mask, prospective_snapshot_mask  # noqa: E402
from ml.source_coverage import filter_source_covered_snapshots  # noqa: E402
from event_forecast_eligibility import verify_candidate_identity  # noqa: E402
from frontend_release import PUBLIC_MANIFEST, DEPLOYMENT_POINTER_SCHEMA, RELEASE_SCHEMA, _canonical_id  # noqa: E402
from sync_finnhub_earnings import normalize_timing  # noqa: E402


LOG = logging.getLogger(__name__)
NUMERIC_PROJECTION = ("em_ml_pct", "em_ml_abs", "p10", "p25", "p50", "p75", "p90")


def capture_deployed_publication(*, data_dir: Path, public_dir: Path, deployment_pointer: Path, fetcher=None) -> None:
    """Read two bounded public files outside provider-free scoring execution."""
    destination = data_dir / ".forecast_retention/publication"
    shutil.rmtree(destination, ignore_errors=True)
    members = _verified_public_members(public_dir, deployment_pointer)

    def download(name):
        deadline = time.monotonic() + 20
        with requests.get(f"https://usequantiv.com/{name}", allow_redirects=False,
                          timeout=(5, 10), stream=True, headers={"Cache-Control": "no-cache"}) as response:
            if response.status_code != 200:
                raise ValueError(f"deployed publication returned HTTP {response.status_code}")
            chunks, total = [], 0
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > 16 * 1024 * 1024 or time.monotonic() > deadline:
                    raise ValueError("deployed publication exceeded bounded download")
                chunks.append(chunk)
            return b"".join(chunks)

    get = fetcher or download
    manifest = get(PUBLIC_MANIFEST)
    if manifest != (public_dir / PUBLIC_MANIFEST).read_bytes():
        raise ValueError("production frontend does not match the pinned publication")
    screener = get("screener.json")
    expected = members["screener.json"]
    if len(screener) != expected["bytes"] or hashlib.sha256(screener).hexdigest() != expected["sha256"]:
        raise ValueError("production screener does not match the pinned publication")
    destination.mkdir(parents=True)
    (destination / PUBLIC_MANIFEST).write_bytes(manifest)
    (destination / "screener.json").write_bytes(screener)


def _snapshot_member(receipt: dict) -> dict:
    verified = verify_evidence_receipt(receipt, expected_scope="forecasts")
    if verified["quality"].get("status") != "passed":
        raise ValueError("prior forecast receipt did not pass validation")
    bundles = [bundle for bundle in verified["artifacts"] if bundle.get("name") == "forecast_snapshot"]
    if len(bundles) != 1 or len(bundles[0].get("members", [])) != 1:
        raise ValueError("prior receipt must bind one exact forecast snapshot")
    member = bundles[0]["members"][0]
    if (not re.fullmatch(r"[0-9a-f]{64}", str(member.get("sha256")))
            or type(member.get("bytes")) is not int or member["bytes"] <= 0):
        raise ValueError("invalid forecast snapshot digest or size")
    return member


def preserve_published_snapshot(forecast_path: Path, receipt: dict, *, forecasts_dir: Path) -> Path:
    """Save exact eligible bytes before publication; display proof is checked later."""
    member = _snapshot_member(receipt)
    if forecast_path.stat().st_size != member["bytes"] or sha256_file(forecast_path) != member["sha256"]:
        raise ValueError("eligible snapshot differs from its publication receipt")
    destination = forecasts_dir / "published_snapshots" / f"{member['sha256']}.parquet"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(destination) != member["sha256"]:
            raise ValueError("immutable published snapshot collision")
    else:
        temporary = destination.with_suffix(".tmp")
        shutil.copyfile(forecast_path, temporary)
        if sha256_file(temporary) != member["sha256"]:
            temporary.unlink()
            raise ValueError("forecast snapshot changed while being preserved")
        temporary.replace(destination)
    return destination


def _verified_public_members(public_dir: Path, deployment_pointer: Path) -> dict:
    pointer = json.loads(deployment_pointer.read_text())
    manifest_path = public_dir / PUBLIC_MANIFEST
    payload = manifest_path.read_bytes()
    pinned = pointer.get("manifest", {})
    if (pointer.get("schema") != DEPLOYMENT_POINTER_SCHEMA
            or len(payload) != pinned.get("bytes")
            or hashlib.sha256(payload).hexdigest() != pinned.get("sha256")):
        raise ValueError("prior frontend manifest is not the Git-pinned publication")
    manifest = json.loads(payload)
    identity = {key: manifest.get(key) for key in ("schema", "files", "file_count", "total_bytes")}
    release_id = _canonical_id(identity)
    if (manifest.get("schema") != RELEASE_SCHEMA or release_id != pointer.get("release_id")
            or release_id != manifest.get("release_id")
            or pinned.get("path") != f"manifests/{release_id}.json"
            or pointer.get("archive") != manifest.get("archive")):
        raise ValueError("prior frontend release identity differs from its deployment pointer")
    members = {member["path"]: member for member in manifest["files"]}
    if len(members) != len(manifest["files"]):
        raise ValueError("duplicate frontend inventory members")
    return members


def _read_public(public_dir: Path, members: dict, name: str):
    member = members.get(name)
    if member is None or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
        raise ValueError("publication evidence is absent from the verified frontend inventory")
    payload = (public_dir / name).read_bytes()
    if len(payload) != member["bytes"] or hashlib.sha256(payload).hexdigest() != member["sha256"]:
        raise ValueError(f"prior published file has changed: {name}")
    return json.loads(payload)


def _displayed_rows(node, symbol=""):
    if isinstance(node, list):
        for child in node:
            yield from _displayed_rows(child, symbol)
    elif isinstance(node, dict):
        symbol = str(node.get("ticker") or node.get("symbol") or symbol).strip().upper()
        method = node.get("display_forecast_method")
        if (node.get("forecast_id") and node.get("em_ml_pct") is not None
                and (method == "ml" or method is None and node.get("em_method") == "ml_lightgbm")):
            yield {**node, "ticker": symbol}
        for child in node.values():
            if isinstance(child, (dict, list)):
                yield from _displayed_rows(child, symbol)


def _check_public_projection(public: dict, row: dict) -> None:
    expected = {
        "ticker": str(row["act_symbol"]).strip().upper(),
        "forecast_model_bundle_id": row["model_bundle_id"],
        "forecast_feature_hash": row["feature_hash"],
        "model_horizon": row["model_horizon"],
    }
    if any(public.get(key) != value for key, value in expected.items()):
        raise ValueError("published forecast identity differs from authenticated row")
    if (str(public.get("earnings_date") or public.get("date"))[:10] != str(row["earnings_date"])[:10]
            or str(public.get("ml_snapshot_date"))[:10] != str(row["snapshot_date"])[:10]
            or normalize_timing(public.get("timing")) != normalize_timing(row["timing"])
            or pd.Timestamp(public.get("forecast_scored_at")) != pd.Timestamp(row["scored_at"])):
        raise ValueError("published forecast dates or session differ from authenticated row")
    if (public.get("display_forecast_pct") != round(float(row["em_ml_pct"]), 6)
            or str(public.get("display_forecast_as_of"))[:10] != str(row["snapshot_date"])[:10]
            or public.get("ml_status") not in {None, "available"}):
        raise ValueError("displayed ML headline differs from authenticated forecast")
    for key in NUMERIC_PROJECTION:
        value = float(row[key])
        if not np.isfinite(value) or public.get(key) != round(value, 6):
            raise ValueError("published forecast output differs from authenticated row")


def load_published_forecasts(*, data_dir: Path, public_dir: Path, deployment_pointer: Path) -> pd.DataFrame:
    """Authenticate exact values and intersect them with actually displayed ML."""
    members = _verified_public_members(public_dir, deployment_pointer)
    production = data_dir / ".forecast_retention/publication"
    if (production / PUBLIC_MANIFEST).read_bytes() != (public_dir / PUBLIC_MANIFEST).read_bytes():
        raise ValueError("production publication proof does not match the pinned release")
    evidence = _read_public(public_dir, members, "evidence/forecast.json")
    filename = str(evidence.get("receipt_file") or "")
    if Path(filename).name != filename or not filename.endswith(".receipt.json"):
        raise ValueError("invalid immutable publication receipt filename")
    receipt = json.loads((data_dir / "forecasts/receipts" / filename).read_text())
    member = _snapshot_member(receipt)
    if receipt["receipt_id"] != evidence.get("receipt_id"):
        raise ValueError("forecast receipt is not authenticated by the published frontend")
    snapshot = data_dir / "forecasts/published_snapshots" / f"{member['sha256']}.parquet"
    if snapshot.stat().st_size != member["bytes"] or sha256_file(snapshot) != member["sha256"]:
        raise ValueError("published snapshot values differ from the authenticated receipt")
    frame = pd.read_parquet(snapshot)
    if "forecast_id" not in frame or frame["forecast_id"].duplicated().any():
        raise ValueError("published snapshot has missing or duplicate forecast identities")
    rows = {row["forecast_id"]: row for row in frame.to_dict("records")}
    displayed = set()
    # The production screener contains the whole published calendar universe.
    # Its bytes were fetched from production, not merely built or uploaded.
    public_screener = _read_public(public_dir, members, "screener.json")
    if _read_public(production, members, "screener.json") != public_screener:
        raise ValueError("production forecast display proof differs from the verified public corpus")
    for public in _displayed_rows(public_screener):
        identity = public["forecast_id"]
        if identity in rows:
            _check_public_projection(public, rows[identity])
            displayed.add(identity)
    return frame.loc[frame["forecast_id"].isin(displayed)].copy()


def retain_published_forecasts(
    current: pd.DataFrame, *, data_dir: Path, public_dir: Path, deployment_pointer: Path,
    bundle_dir: Path, now: datetime, conn, source_features: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Append only absent events; unchanged monitoring still admits every row."""
    counts = {}
    def unchanged():
        LOG.info("Published forecast retention: retained=0, checks=%s", counts)
        return current
    try:
        prior = load_published_forecasts(data_dir=data_dir, public_dir=public_dir,
                                         deployment_pointer=deployment_pointer)
        counts["authenticated_displayed"] = len(prior)
        pointer = verify_control_pointer(json.loads((data_dir / "models/control/champion.json").read_text()))
        manifest = verify_bundle_dir(bundle_dir)
        if pointer["champion_bundle_id"] != manifest["bundle_id"] or bundle_dir.name != manifest["bundle_id"]:
            raise ValueError("retention requires the current authenticated champion")
        # This repair covers immutable optionless predictions. Old quote-bearing
        # observations continue through the existing two-session rescoring path.
        prior = prior.loc[prior["model_bundle_id"].eq(manifest["bundle_id"])
                          & prior["em_math_pct"].isna()].copy()
        option_columns = [column for column in STRICT_OPTION_EVIDENCE_COLUMNS if column in prior]
        prior = prior.loc[prior[option_columns].isna().all(axis=1)].copy()
        counts["same_champion_optionless"] = len(prior)
        if prior.empty:
            return unchanged()
        prior = verify_candidate_identity(prior, bundle_dir, manifest["bundle_id"], manifest["horizons"],
                                          require_admitted_cohorts=True)
        freshness = prior.assign(recorded_at=now.isoformat())
        prior = prior.loc[prospective_snapshot_mask(freshness) & prospective_event_mask(freshness)
                          & pd.to_datetime(prior["scored_at"], utc=True).le(pd.Timestamp(now))].copy()
        counts["fresh_before_deadline"] = len(prior)
        covered = filter_source_covered_snapshots(conn, prior, require_coverage=True)
        prior = prior.loc[prior["forecast_id"].isin(covered["forecast_id"])].copy()
        counts["action_covered"] = len(prior)
        if source_features is None or source_features.empty:
            counts["current_source_features"] = 0
            return unchanged()
        observed = source_features.copy()
        observed["log_spot"] = np.log(observed["spot_price"].clip(lower=1))
        observed["timing_bmo"] = observed["timing"].eq("bmo").astype(float)
        observed["timing_amc"] = observed["timing"].eq("amc").astype(float)
        observed["earnings_month"] = pd.to_datetime(observed["earnings_date"]).dt.month
        observed["earnings_dow"] = pd.to_datetime(observed["earnings_date"]).dt.dayofweek
        def key(row):
            return (row["act_symbol"], str(row["earnings_date"])[:10], str(row["snapshot_date"])[:10],
                    row.get("_retention_feature_protocol", row.get("feature_protocol")))
        inputs = {key(row): row for row in observed.to_dict("records")}
        def same_observed_inputs(row):
            source = inputs.get(key(row), {})
            vector = json.loads(row["feature_vector"])
            if any(vector.get(name) is not None for name in
                   ("atm_iv", "straddle_pct", "em_iv_pct", "dte", "event_move_implied",
                    "iv_crush_pct", "front_iv", "back_iv", "event_vol_fraction")):
                return False
            # Original missing inputs remain missing. Every originally observed
            # feature, including IV history/macros, must still agree with sources.
            return bool(source) and all(
                value is None or name in source and pd.notna(source[name])
                and np.isclose(float(value), float(source[name]), rtol=1e-10, atol=1e-12)
                for name, value in vector.items()
            )
        prior = prior.loc[[same_observed_inputs(row) for row in prior.to_dict("records")]].copy()
        counts["same_observed_inputs"] = len(prior)
        if prior.empty:
            return unchanged()
        conn.register("_prior_published_candidates", prior)
        try:
            valid = conn.execute("""
                SELECT p.forecast_id FROM _prior_published_candidates p
                WHERE p.timing IN ('bmo', 'amc')
                AND EXISTS (SELECT 1 FROM v_earnings e WHERE e.act_symbol=p.act_symbol
                    AND CAST(e.date AS DATE)=CAST(p.earnings_date AS DATE) AND e.timing=p.timing)
                AND (SELECT COUNT(*) FROM v_ohlcv px WHERE px.act_symbol=p.act_symbol
                    AND CAST(px.date AS DATE)=CAST(p.snapshot_date AS DATE))=1
                AND EXISTS (SELECT 1 FROM v_ohlcv px WHERE px.act_symbol=p.act_symbol
                    AND CAST(px.date AS DATE)=CAST(p.snapshot_date AS DATE)
                    AND isfinite(px.close) AND px.close > 0
                    AND ABS(px.close-p.spot_price)<=GREATEST(1e-8, ABS(px.close)*1e-6))
            """).fetchall()
        finally:
            conn.unregister("_prior_published_candidates")
        prior = prior.loc[prior["forecast_id"].isin({row[0] for row in valid})].copy()
        counts["same_canonical_event_and_close"] = len(prior)
        existing = {(str(row.act_symbol), str(row.earnings_date)[:10]) for row in current.itertuples()}
        prior = prior.loc[[(str(row.act_symbol), str(row.earnings_date)[:10]) not in existing
                           for row in prior.itertuples()]]
        prior = prior.sort_values(["snapshot_date", "scored_at"], ascending=False, kind="stable")
        prior = prior.drop_duplicates(["act_symbol", "earnings_date"], keep="first")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, OverflowError, duckdb.Error) as exc:
        LOG.warning("Prior published forecasts unavailable for retention: %s", exc)
        return current
    if prior.empty:
        return unchanged()
    LOG.info("Published forecast retention: retained=%d, checks=%s", len(prior), counts)
    return prior.reset_index(drop=True) if current.empty else pd.concat([current, prior], ignore_index=True, sort=False)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["verify-production"])
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--public-dir", type=Path, default=ROOT / "apps/frontend/public")
    parser.add_argument("--deployment-pointer", type=Path, default=ROOT / "apps/frontend/frontend-release.json")
    arguments = parser.parse_args()
    try:
        capture_deployed_publication(data_dir=arguments.data_dir, public_dir=arguments.public_dir,
                                     deployment_pointer=arguments.deployment_pointer)
        print("Verified production publication for fresh-forecast retention")
    except (OSError, ValueError, RuntimeError, KeyError, requests.RequestException) as error:
        # Retention is optional; fresh scoring remains available when prior proof
        # cannot be established. No prior rows are admitted on this path.
        print(f"Prior forecast retention unavailable: {error}")
