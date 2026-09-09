#!/usr/bin/env python3
"""Build, verify, and project immutable calendar-reference releases.

Calendar reference is deliberately independent of options/research publication.
It contains only event identity (ticker, date, normalized reporting session) for
Quantiv's existing displayed decision universe. Research metrics are joined by
the frontend only when ticker/date/known-session match exactly.

R2 layout expected by ``r2_push_calendar_reference.sh``::

    calendar-reference/releases/<release_id>.json
    calendar-reference/receipts/<receipt_id>.json
    calendar-reference/current.json

The release ID hashes semantic event content. Source observation timestamps live
in a separately content-addressed receipt so refetching unchanged events does not
mutate an immutable release object. ``current.json`` is promoted last.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CALENDAR = REPO_ROOT / "data" / "earnings_calendar.csv"
DEFAULT_BASELINE = REPO_ROOT / "data" / "validation" / "earnings_calendar_baseline.csv"
DEFAULT_DOLTHUB_META = REPO_ROOT / "data" / "dolthub_earnings_metadata.json"
DEFAULT_FINNHUB_META = REPO_ROOT / "data" / "finnhub_earnings_metadata.json"
DEFAULT_FMP_META = REPO_ROOT / "data" / "fmp_earnings_metadata.json"
DEFAULT_SYMBOLS_DIR = REPO_ROOT / "apps" / "frontend" / "public" / "symbols"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "calendar_reference"
DEFAULT_PUBLIC_FILE = REPO_ROOT / "apps" / "frontend" / "public" / "calendar-reference.json"
DEFAULT_PUBLIC_RECEIPT = REPO_ROOT / "apps" / "frontend" / "public" / "evidence" / "calendar-reference-receipt.json"
DEFAULT_INTEGRITY_SCRIPT = REPO_ROOT / "scripts" / "check_earnings_calendar_integrity.py"

RELEASE_SCHEMA = "quantiv.calendar-reference.v1"
RECEIPT_SCHEMA = "quantiv.calendar-reference-receipt.v1"
POINTER_SCHEMA = "quantiv.current-calendar-reference.v1"
KNOWN_SESSIONS = {"bmo", "amc", "dmh", "unknown"}
REQUIRED_COLUMNS = {"act_symbol", "date", "timing"}
MARKET_TZ = ZoneInfo("America/New_York")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _canonical_id(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _parse_timestamp(value: str, label: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeError(f"invalid {label} timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_timing(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"bmo", "before_market_open", "before_open"}:
        return "bmo"
    if raw in {"amc", "after_market_close", "after_close"}:
        return "amc"
    if raw in {"dmh", "during_market_hours", "during_market_hour"}:
        return "dmh"
    return "unknown"


def market_today(now: datetime | None = None) -> date:
    """Return today at the US equity-market date boundary."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(MARKET_TZ).date()


def monday_of(value: date) -> date:
    return value - timedelta(days=value.weekday())


def load_universe(symbols_dir: Path) -> tuple[list[str], str]:
    if not symbols_dir.is_dir():
        raise RuntimeError(f"calendar universe directory is missing: {symbols_dir}")
    symbols = sorted({path.stem.strip().upper() for path in symbols_dir.glob("*.json") if path.stem.strip()})
    if not symbols:
        raise RuntimeError(f"calendar universe is empty: {symbols_dir}")
    universe_id = _canonical_id({"symbols": symbols})
    return symbols, universe_id


def _actual_present(row: dict[str, str]) -> bool:
    for key in ("eps_actual", "revenue_actual"):
        value = str(row.get(key) or "").strip()
        if value and value.lower() not in {"nan", "none", "null"}:
            return True
    return False


def load_events(
    calendar: Path,
    universe: set[str],
    start: date,
    end: date,
) -> tuple[list[dict[str, str]], list[tuple[str, str, str]]]:
    if not calendar.is_file() or calendar.stat().st_size == 0:
        raise RuntimeError(f"earnings calendar is missing or empty: {calendar}")
    with calendar.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise RuntimeError(f"earnings calendar missing columns: {sorted(missing)}")
        candidates: dict[str, list[dict[str, str]]] = {}
        for row in reader:
            ticker = str(row.get("act_symbol") or "").strip().upper()
            if ticker not in universe:
                continue
            try:
                event_date = date.fromisoformat(str(row.get("date") or "")[:10])
            except ValueError:
                continue
            if event_date < start or event_date > end:
                continue
            normalized = dict(row)
            normalized["act_symbol"] = ticker
            normalized["date"] = event_date.isoformat()
            normalized["timing"] = normalize_timing(row.get("timing"))
            candidates.setdefault(ticker, []).append(normalized)

    events: list[dict[str, str]] = []
    dropped: list[tuple[str, str, str]] = []
    for ticker, rows in sorted(candidates.items()):
        # Same rule as frontend_data.payloads.collapse_duplicate_earnings:
        # reported actuals win; otherwise retain the latest revised date.
        ordered = sorted(
            rows,
            key=lambda row: (_actual_present(row), row["date"]),
            reverse=True,
        )
        kept = ordered[0]
        events.append(
            {
                "ticker": ticker,
                "earnings_date": kept["date"],
                "timing": kept["timing"],
            }
        )
        for row in ordered[1:]:
            dropped.append((ticker, row["date"], kept["date"]))

    events.sort(key=lambda item: (item["earnings_date"], item["ticker"]))
    identities = [(event["ticker"], event["earnings_date"]) for event in events]
    if len(identities) != len(set(identities)):
        raise RuntimeError("calendar reference contains duplicate ticker/date identities")
    if any(event["timing"] not in KNOWN_SESSIONS for event in events):
        raise RuntimeError("calendar reference contains an unsupported reporting session")
    return events, dropped


def _load_current_run_source_evidence(
    paths: Iterable[tuple[str, Path]],
    not_before: datetime,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for provider, path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid {provider} earnings metadata: {path}") from exc
        observed_raw = payload.get("synced_at") or payload.get("fetched_at")
        if not isinstance(observed_raw, str) or not observed_raw.strip():
            continue
        observed = _parse_timestamp(observed_raw, f"{provider} source observation")
        # Restored cross-run metadata must never masquerade as current-run evidence.
        if observed < not_before:
            continue
        item: dict[str, Any] = {
            "provider": provider,
            "observed_at": observed.isoformat().replace("+00:00", "Z"),
            "metadata_sha256": _sha256_file(path),
        }
        for key in ("from", "to", "rows", "symbols", "date_min", "date_max"):
            if key in payload:
                item[key] = payload[key]
        evidence.append(item)
    evidence.sort(key=lambda item: (item["provider"], item["observed_at"]))
    if not any(item["provider"] == "dolthub" for item in evidence):
        raise RuntimeError(
            "current-run DoltHub earnings fetch evidence is required for calendar publication"
        )
    return evidence


def run_integrity_gate(script: Path, repo_root: Path, baseline: Path) -> str:
    if not script.is_file():
        raise RuntimeError(f"calendar integrity script is missing: {script}")
    completed = subprocess.run(
        [sys.executable, str(script), "--baseline", str(baseline), "--require-baseline"],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = completed.stdout or ""
    if completed.returncode != 0:
        raise RuntimeError("calendar integrity validation failed:\n" + output[-4000:])
    return output


def build_release(
    *,
    calendar: Path = DEFAULT_CALENDAR,
    baseline: Path = DEFAULT_BASELINE,
    symbols_dir: Path = DEFAULT_SYMBOLS_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    source_revision: str,
    not_before: datetime,
    today: date | None = None,
    integrity_script: Path = DEFAULT_INTEGRITY_SCRIPT,
    repo_root: Path = REPO_ROOT,
    source_metadata: Iterable[tuple[str, Path]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not baseline.is_file() or baseline.stat().st_size == 0:
        raise RuntimeError(f"prior calendar integrity baseline is missing: {baseline}")
    integrity_output = run_integrity_gate(integrity_script, repo_root, baseline)
    source_metadata = source_metadata or (
        ("dolthub", DEFAULT_DOLTHUB_META),
        ("finnhub", DEFAULT_FINNHUB_META),
        ("fmp", DEFAULT_FMP_META),
    )
    source_evidence = _load_current_run_source_evidence(source_metadata, not_before)

    anchor = today or market_today()
    current_monday = monday_of(anchor)
    start = current_monday - timedelta(days=7)
    end = current_monday + timedelta(days=18)  # Friday of offset +2.

    symbols, universe_id = load_universe(symbols_dir)
    events, dropped = load_events(calendar, set(symbols), start, end)
    if not events:
        raise RuntimeError("calendar reference has no events in the supported four-week window")

    source_sha = _sha256_file(calendar)
    baseline_sha = _sha256_file(baseline)
    release_core: dict[str, Any] = {
        "schema": RELEASE_SCHEMA,
        "universe": {
            "schema": "quantiv.frontend-symbol-universe.v1",
            "id": universe_id,
            "size": len(symbols),
        },
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "events": events,
    }
    release_id = _canonical_id(release_core)
    release = {"release_id": release_id, **release_core}

    observed_at = max(_parse_timestamp(item["observed_at"], "source observation") for item in source_evidence)
    receipt_core: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "release_id": release_id,
        "source_revision": source_revision,
        "source_file": {
            "path": "data/earnings_calendar.csv",
            "sha256": source_sha,
        },
        "integrity": {
            "status": "passed",
            "baseline_sha256": baseline_sha,
            "stdout_sha256": hashlib.sha256(integrity_output.encode()).hexdigest(),
        },
        "source_evidence": source_evidence,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "event_count": len(events),
        "deduplicated_event_count": len(dropped),
        "universe_id": universe_id,
    }
    receipt_id = _canonical_id(receipt_core)
    receipt = {"receipt_id": receipt_id, **receipt_core}
    pointer = {
        "schema": POINTER_SCHEMA,
        "release_id": release_id,
        "release": f"releases/{release_id}.json",
        "receipt_id": receipt_id,
        "receipt": f"receipts/{receipt_id}.json",
        "promoted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    release_path = output_dir / "releases" / f"{release_id}.json"
    receipt_path = output_dir / "receipts" / f"{receipt_id}.json"
    if release_path.exists() and json.loads(release_path.read_text()) != release:
        raise RuntimeError(f"calendar release identity collision: {release_path}")
    if receipt_path.exists() and json.loads(receipt_path.read_text()) != receipt:
        raise RuntimeError(f"calendar receipt identity collision: {receipt_path}")
    _atomic_json(release_path, release)
    _atomic_json(receipt_path, receipt)
    _atomic_json(output_dir / "current.json", pointer)
    return release, receipt, pointer


def verify_release(output_dir: Path = DEFAULT_OUTPUT_DIR) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    pointer_path = output_dir / "current.json"
    if not pointer_path.is_file():
        raise RuntimeError(f"calendar reference pointer is missing: {pointer_path}")
    pointer = json.loads(pointer_path.read_text())
    if pointer.get("schema") != POINTER_SCHEMA:
        raise RuntimeError("calendar reference pointer schema is unsupported")
    release_path = output_dir / str(pointer.get("release") or "")
    receipt_path = output_dir / str(pointer.get("receipt") or "")
    if not release_path.is_file() or not receipt_path.is_file():
        raise RuntimeError("calendar reference release/receipt is incomplete")
    release = json.loads(release_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    if release.get("schema") != RELEASE_SCHEMA or receipt.get("schema") != RECEIPT_SCHEMA:
        raise RuntimeError("calendar reference release/receipt schema is unsupported")
    release_core = {key: release[key] for key in ("schema", "universe", "window", "events")}
    if _canonical_id(release_core) != release.get("release_id"):
        raise RuntimeError("calendar reference release identity mismatch")
    receipt_core = {key: value for key, value in receipt.items() if key != "receipt_id"}
    if _canonical_id(receipt_core) != receipt.get("receipt_id"):
        raise RuntimeError("calendar reference receipt identity mismatch")
    if pointer.get("release_id") != release.get("release_id"):
        raise RuntimeError("calendar reference pointer/release mismatch")
    if pointer.get("receipt_id") != receipt.get("receipt_id"):
        raise RuntimeError("calendar reference pointer/receipt mismatch")
    if receipt.get("release_id") != release.get("release_id"):
        raise RuntimeError("calendar reference receipt references a different release")
    events = release.get("events") or []
    identities = [(item.get("ticker"), item.get("earnings_date")) for item in events]
    if len(identities) != len(set(identities)):
        raise RuntimeError("calendar reference release has duplicate event identities")
    return release, receipt, pointer


def project_public(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    public_file: Path = DEFAULT_PUBLIC_FILE,
    public_receipt: Path = DEFAULT_PUBLIC_RECEIPT,
) -> dict[str, Any]:
    release, receipt, _ = verify_release(output_dir)
    projection = {
        **release,
        "receipt_id": receipt["receipt_id"],
        "observed_at": receipt["observed_at"],
        "source_revision": receipt["source_revision"],
        "source_file_sha256": receipt["source_file"]["sha256"],
    }
    _atomic_json(public_file, projection)
    _atomic_json(public_receipt, receipt)
    return projection


def _git_revision(repo_root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--calendar", type=Path, default=DEFAULT_CALENDAR)
    build.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    build.add_argument("--symbols-dir", type=Path, default=DEFAULT_SYMBOLS_DIR)
    build.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    build.add_argument("--source-revision", default=None)
    build.add_argument("--not-before", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)

    project = sub.add_parser("project")
    project.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    project.add_argument("--public-file", type=Path, default=DEFAULT_PUBLIC_FILE)
    project.add_argument("--public-receipt", type=Path, default=DEFAULT_PUBLIC_RECEIPT)

    args = parser.parse_args()
    if args.command == "build":
        not_before = _parse_timestamp(args.not_before, "refresh start")
        revision = args.source_revision or _git_revision(REPO_ROOT)
        release, receipt, _ = build_release(
            calendar=args.calendar,
            baseline=args.baseline,
            symbols_dir=args.symbols_dir,
            output_dir=args.output_dir,
            source_revision=revision,
            not_before=not_before,
        )
        print(
            f"calendar reference {release['release_id']} — {len(release['events'])} events; "
            f"receipt {receipt['receipt_id']}; observed {receipt['observed_at']}"
        )
        return 0
    if args.command == "verify":
        release, receipt, _ = verify_release(args.output_dir)
        print(
            f"verified calendar reference {release['release_id']} — {len(release['events'])} events; "
            f"receipt {receipt['receipt_id']}"
        )
        return 0
    projection = project_public(args.output_dir, args.public_file, args.public_receipt)
    print(
        f"projected calendar reference {projection['release_id']} to {args.public_file}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
