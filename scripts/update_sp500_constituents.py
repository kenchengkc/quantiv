#!/usr/bin/env python3
"""Refresh Quantiv's checked-in S&P 500 constituent reference.

The upstream snapshot is deliberately treated as untrusted input. This script
validates its schema, constituent count, ticker uniqueness, GICS sectors and
membership churn before replacing the repository JSON.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "lib" / "data" / "sp500-constituents.json"
DEFAULT_SOURCE_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
    "main/data/constituents.csv"
)
EXPECTED_COLUMNS = {"Symbol", "Security", "GICS Sector", "GICS Sub-Industry"}
GICS_SECTORS = {
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
}
MIN_CONSTITUENT_ROWS = 500
MAX_CONSTITUENT_ROWS = 505
MAX_MEMBERSHIP_CHURN = 30


class SnapshotValidationError(ValueError):
    """Raised when an upstream constituent snapshot fails safety checks."""


def fetch_text(url: str, timeout: float = 30.0) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "quantiv-sp500-refresh/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        if getattr(response, "status", 200) != 200:
            raise RuntimeError(f"S&P 500 source returned HTTP {response.status}")
        return response.read().decode("utf-8-sig")


def parse_snapshot(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or [])
    missing = sorted(EXPECTED_COLUMNS - fields)
    if missing:
        raise SnapshotValidationError(
            f"upstream CSV is missing required columns: {', '.join(missing)}"
        )

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for line_number, source in enumerate(reader, start=2):
        symbol = (source.get("Symbol") or "").strip().upper()
        name = (source.get("Security") or "").strip()
        sector = (source.get("GICS Sector") or "").strip()
        industry = (source.get("GICS Sub-Industry") or "").strip()

        if not symbol or not name or not sector or not industry:
            raise SnapshotValidationError(
                f"line {line_number}: symbol/name/sector/industry must be non-empty"
            )
        if symbol in seen:
            raise SnapshotValidationError(f"duplicate constituent symbol: {symbol}")
        if sector not in GICS_SECTORS:
            raise SnapshotValidationError(
                f"line {line_number}: unknown GICS sector {sector!r} for {symbol}"
            )

        seen.add(symbol)
        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "sector": sector,
                "industry": industry,
            }
        )

    if not MIN_CONSTITUENT_ROWS <= len(rows) <= MAX_CONSTITUENT_ROWS:
        raise SnapshotValidationError(
            "unexpected S&P 500 constituent row count: "
            f"{len(rows)} (expected {MIN_CONSTITUENT_ROWS}-{MAX_CONSTITUENT_ROWS})"
        )
    return rows


def load_existing(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SnapshotValidationError(f"existing snapshot is not a JSON array: {path}")
    rows: list[dict[str, str]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SnapshotValidationError(
                f"existing snapshot row {index} is not a JSON object"
            )
        try:
            rows.append(
                {
                    "symbol": str(item["symbol"]),
                    "name": str(item["name"]),
                    "sector": str(item["sector"]),
                    "industry": str(item["industry"]),
                }
            )
        except KeyError as exc:
            raise SnapshotValidationError(
                f"existing snapshot row {index} is missing {exc.args[0]!r}"
            ) from exc
    return rows


def snapshot_diff(
    old_rows: list[dict[str, str]], new_rows: list[dict[str, str]]
) -> tuple[list[str], list[str], list[tuple[str, list[str]]]]:
    old = {row["symbol"]: row for row in old_rows}
    new = {row["symbol"]: row for row in new_rows}
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())

    changed: list[tuple[str, list[str]]] = []
    for symbol in sorted(old.keys() & new.keys()):
        fields = [
            field
            for field in ("name", "sector", "industry")
            if old[symbol][field] != new[symbol][field]
        ]
        if fields:
            changed.append((symbol, fields))
    return added, removed, changed


def validate_churn(
    old_rows: list[dict[str, str]],
    new_rows: list[dict[str, str]],
    *,
    allow_large_churn: bool = False,
) -> None:
    if not old_rows:
        return
    added, removed, _ = snapshot_diff(old_rows, new_rows)
    churn = len(added) + len(removed)
    if churn > MAX_MEMBERSHIP_CHURN and not allow_large_churn:
        raise SnapshotValidationError(
            "membership churn exceeds safety bound: "
            f"{len(added)} additions + {len(removed)} removals = {churn}; "
            "inspect upstream data and rerun with --allow-large-churn only if intentional"
        )


def render_summary(
    old_rows: list[dict[str, str]], new_rows: list[dict[str, str]]
) -> str:
    old = {row["symbol"]: row for row in old_rows}
    new = {row["symbol"]: row for row in new_rows}
    added, removed, changed = snapshot_diff(old_rows, new_rows)

    lines = [
        "## S&P 500 constituent refresh",
        "",
        f"- Previous rows: **{len(old_rows)}**",
        f"- New rows: **{len(new_rows)}**",
        f"- Added: **{len(added)}**",
        f"- Removed: **{len(removed)}**",
        f"- Reference-data changes: **{len(changed)}**",
    ]

    if added:
        lines.extend(["", "### Added"])
        lines.extend(f"- `{symbol}` — {new[symbol]['name']}" for symbol in added)
    if removed:
        lines.extend(["", "### Removed"])
        lines.extend(f"- `{symbol}` — {old[symbol]['name']}" for symbol in removed)
    if changed:
        lines.extend(["", "### Reference-data changes"])
        for symbol, fields in changed:
            details = ", ".join(fields)
            lines.append(f"- `{symbol}` — {details}")

    if not added and not removed and not changed:
        lines.extend(["", "No constituent or reference-data changes detected."])
    return "\n".join(lines) + "\n"


def write_snapshot(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    source.add_argument("--source-file", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument("--allow-large-churn", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.source_file:
            source_text = args.source_file.read_text(encoding="utf-8-sig")
        else:
            source_text = fetch_text(args.source_url)
        new_rows = parse_snapshot(source_text)
        old_rows = load_existing(args.output)
        validate_churn(
            old_rows,
            new_rows,
            allow_large_churn=args.allow_large_churn,
        )
        summary = render_summary(old_rows, new_rows)
        write_snapshot(args.output, new_rows)
        if args.summary_file:
            args.summary_file.parent.mkdir(parents=True, exist_ok=True)
            args.summary_file.write_text(summary, encoding="utf-8")
        sys.stdout.write(summary)
        return 0
    except (OSError, RuntimeError, SnapshotValidationError, json.JSONDecodeError) as exc:
        print(f"S&P 500 refresh failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
