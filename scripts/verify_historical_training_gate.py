#!/usr/bin/env python3
"""Admit immutable historical inputs without granting production activation.

Training-cohort quality is checked after feature extraction by the mandatory
training validator. Current options reconciliation is an activation contract;
it must not stand in for historical source integrity or mature-label checks.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import pandas as pd

try:
    from data_release import MUTABLE_PARQUET_ALIASES, verify_release
except ModuleNotFoundError:
    from scripts.data_release import MUTABLE_PARQUET_ALIASES, verify_release


def verify_historical_corporate_actions(data_dir: Path, declared: set[str]) -> dict[str, Any]:
    """Verify the receipt's original universe, independently of today's options."""
    try:
        from delisted import canonical_ticker, is_delisted
        from sync_dolthub import _action_content_digest, _json_digest
    except ModuleNotFoundError:
        from scripts.delisted import canonical_ticker, is_delisted
        from scripts.sync_dolthub import _action_content_digest, _json_digest

    root = data_dir / "control/ingestion/corporate_actions"
    path = root / "latest.json"
    try:
        receipt = json.loads(path.read_text())
        identity = receipt["receipt_id"]
        if not re.fullmatch(r"[0-9a-f]{64}", identity) or identity != _json_digest({
            key: value for key, value in receipt.items() if key != "receipt_id"
        }):
            raise ValueError("receipt identity does not match its contents")
        if (receipt["schema"] != "quantiv.corporate-action-ingestion.v1"
                or receipt["source"] != "dolthub:post-no-preference/stocks"
                or receipt["replay_equivalence"] != "verified"):
            raise ValueError("receipt does not prove canonical replay-verified actions")
        source_date = date.fromisoformat(receipt["source_options_date"])
        query_start = date.fromisoformat(receipt["query_start"])
        query_end = date.fromisoformat(receipt["query_end"])
        if not query_start <= source_date <= query_end:
            raise ValueError("receipt query window does not cover historical normalization")
        immutable = root / f"receipts/{source_date}/{identity}.json"
        if json.loads(immutable.read_text()) != receipt:
            raise ValueError("immutable receipt differs from the selected receipt")

        def release_member(relative: str) -> Path:
            member = Path(relative)
            resolved = (data_dir / member).resolve()
            if (member.is_absolute() or ".." in member.parts
                    or member.as_posix() not in declared
                    or data_dir.resolve() not in resolved.parents
                    or (data_dir / member).is_symlink()):
                raise ValueError("source partition is not a member of the admitted immutable release")
            return resolved

        source_relative = f"parquet/options_chain/year={source_date.year}/month={source_date.month:02d}/{source_date}.parquet"
        source = release_member(source_relative)
        raw_symbols = pd.read_parquet(source, columns=["act_symbol"])["act_symbol"]
        symbols = sorted({canonical_ticker(symbol) for symbol in raw_symbols.dropna()
                          if not is_delisted(symbol)} - {""})
        universe = receipt["universe"]
        if (not symbols or universe["symbols"] != len(symbols)
                or universe["symbols_sha256"] != hashlib.sha256("\n".join(symbols).encode()).hexdigest()
                or universe["method"] != "latest_options_partition_excluding_retired_symbols"):
            raise ValueError("receipt universe does not match its immutable source snapshot")

        datasets = {}
        for name, values in (("splits", ["to_factor", "for_factor"]), ("dividends", ["amount"])):
            dataset = receipt["datasets"][name]
            partition = release_member(dataset["partition"])
            digest = hashlib.sha256(partition.read_bytes()).hexdigest()
            if digest != dataset["partition_sha256"]:
                raise ValueError(f"{name} partition digest differs from the receipt")
            columns = ["act_symbol", "ex_date", *values]
            frame = pd.read_parquet(partition, columns=columns)
            dates = pd.to_datetime(frame["ex_date"], errors="raise")
            numeric = frame[values].apply(pd.to_numeric, errors="raise")
            invalid_values = (numeric <= 0).any(axis=1) if name == "splits" else (numeric < 0).any(axis=1)
            if (frame.duplicated(["act_symbol", "ex_date"]).any()
                    or frame["act_symbol"].isna().any() or not frame["act_symbol"].isin(symbols).all()
                    or dates.isna().any() or (dates.dt.date < query_start).any() or (dates.dt.date > query_end).any()
                    or numeric.isna().any().any() or numeric.isin([float("inf"), float("-inf")]).any().any()
                    or invalid_values.any()):
                raise ValueError(f"{name} contains invalid or duplicate action observations")
            content = _action_content_digest(frame, columns)
            if len(frame) != dataset["rows"] or content != dataset["content_sha256"] or partition.stem != content:
                raise ValueError(f"{name} row count or content address differs from the receipt")
            batches = dataset["batches"]
            if (not batches or any(batch["completion"] != "short_page" or int(batch["pages"]) < 1
                                   or int(batch["rows"]) < 0 or int(batch["symbols"]) < 1 for batch in batches)
                    or sum(int(batch["rows"]) for batch in batches) != len(frame)
                    or sum(int(batch["symbols"]) for batch in batches) != len(symbols)):
                raise ValueError(f"{name} does not prove exhaustive source pagination")
            datasets[name] = {"partition": dataset["partition"], "partition_sha256": digest,
                              "content_sha256": content, "rows": len(frame)}
        return {"receipt_id": identity, "receipt_path": path.relative_to(data_dir).as_posix(),
                "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "immutable_receipt_path": immutable.relative_to(data_dir).as_posix(),
                "immutable_receipt_sha256": hashlib.sha256(immutable.read_bytes()).hexdigest(),
                "source_options_date": source_date.isoformat(),
                "universe_source": {"path": source_relative, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
                "universe": universe, "covered_symbols": symbols,
                "query_start": query_start.isoformat(), "query_end": query_end.isoformat(),
                "datasets": datasets}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise RuntimeError(f"historical corporate-action receipt verification failed: {exc}") from exc


def verify_historical_training_gate(*, data_dir: Path) -> dict[str, Any]:
    release = verify_release(data_dir)
    pointer = json.loads((data_dir / "control/current_data_release.json").read_text())
    manifest = json.loads((data_dir / pointer["manifest"]).read_text())
    declared = {item["path"] for item in manifest["files"]}
    observed = {
        path.relative_to(data_dir).as_posix()
        for path in (data_dir / "parquet").rglob("*.parquet")
    } - MUTABLE_PARQUET_ALIASES
    if observed != declared:
        raise RuntimeError("historical inputs contain unmanifested or missing partitions")
    if not any(path.startswith("parquet/ohlcv/") for path in declared):
        raise RuntimeError("verified release has no historical price source")
    calendar = data_dir / "earnings_calendar.csv"
    if not calendar.is_file():
        raise RuntimeError("canonical historical earnings calendar unavailable")
    derivative = data_dir / "earnings_calendar.parquet"

    def identities(frame: pd.DataFrame) -> set[tuple[str, str, str]]:
        if frame.empty or not {"act_symbol", "date", "timing"} <= set(frame):
            raise RuntimeError("historical calendar has no usable event identities")
        symbols = frame["act_symbol"].fillna("").astype(str).str.strip().str.upper()
        dates = pd.to_datetime(frame["date"], errors="raise").dt.date.astype(str)
        timing = frame["timing"].fillna("").astype(str).str.strip().str.lower()
        if symbols.eq("").any():
            raise RuntimeError("historical calendar has blank symbols")
        return set(zip(symbols, dates, timing))

    canonical_events = identities(pd.read_csv(calendar))
    selected = calendar
    if derivative.exists():
        if identities(pd.read_parquet(derivative)) != canonical_events:
            raise RuntimeError("historical calendar derivative disagrees with canonical CSV")
        selected = derivative
    actions = verify_historical_corporate_actions(data_dir, declared)
    return {
        "schema": "quantiv.historical-training-admission.v1",
        **release,
        "activation_allowed": False,
        "training_cohort_validation_required": True,
        "earnings_calendar_sha256": (
            hashlib.sha256(calendar.read_bytes()).hexdigest() if calendar.exists() else None
        ),
        "calendar_source": {"path": selected.name, "sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
                            "events": len(canonical_events)},
        "corporate_actions": actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify_historical_training_gate(data_dir=args.data_dir)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"Historical source integrity passed: release={result['release_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
