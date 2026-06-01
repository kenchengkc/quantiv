#!/usr/bin/env python3
"""Apply manual earnings-calendar overrides on top of provider-synced data.

The nightly refresh re-pulls earnings dates from DoltHub / Finnhub / FMP, which
would otherwise revert any manual correction. This step runs *after* those syncs
and *before* the build, re-applying a small, version-controlled set of overrides
to ``data/earnings_calendar.csv`` so manual fixes are durable.

Override file: ``config/earnings_overrides.json``

    {
      "overrides": [
        {
          "symbol": "PVH",
          "match": { "fiscal_year": 2027, "fiscal_q": "Q1" },
          "set":   { "date": "2026-06-03", "timing": "amc" },
          "note":  "manual move +1 trading day, AMC"
        },
        {
          "symbol": "UVV",
          "match":  { "date": "2026-06-04", "fiscal_q": "Q2" },
          "action": "remove",
          "note":   "phantom DoltHub calendar-ordinal row; real report was Q4 on ~05-29"
        }
      ]
    }

Matching:
  * ``match`` compares the listed columns (``date``/``timing``/``fiscal_year``/
    ``fiscal_q``) against each row for ``symbol``. Prefer matching on
    ``fiscal_year`` + ``fiscal_q`` (stable identity) so the override still binds
    after a provider re-asserts a different date.
Actions:
  * ``set`` (default): overwrite the matched rows' ``set`` columns.
  * ``remove``: drop the matched rows.
  * ``add``: append a new row built from ``set`` (must include at least ``date``).
Matched/added rows get ``override`` appended to their ``source`` for traceability.

Run: ``python scripts/apply_earnings_overrides.py`` (``npm run data:earnings:overrides``)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OVERRIDES = REPO_ROOT / "config" / "earnings_overrides.json"
DEFAULT_CSV = REPO_ROOT / "data" / "earnings_calendar.csv"

MATCHABLE = ("date", "timing", "fiscal_year", "fiscal_q")
SETTABLE = ("date", "timing", "fiscal_year", "fiscal_q", "eps_estimate", "revenue_estimate")


def _norm(value: Any) -> str:
    """Normalize a cell/criterion for comparison (handles 2027 vs '2027')."""
    if value is None:
        return ""
    s = str(value).strip()
    # Compare fiscal_year-style numerics without trailing .0 mismatches.
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s.upper()


def _tag_source(row: dict[str, str]) -> None:
    src = (row.get("source") or "").strip()
    parts = [p for p in src.split("+") if p]
    if "override" not in parts:
        parts.append("override")
    row["source"] = "+".join(parts) if parts else "override"


def _matches(row: dict[str, str], symbol: str, match: dict[str, Any]) -> bool:
    if _norm(row.get("act_symbol")) != _norm(symbol):
        return False
    for key, want in match.items():
        if key not in MATCHABLE:
            raise ValueError(f"unsupported match key '{key}' (allowed: {MATCHABLE})")
        if _norm(row.get(key)) != _norm(want):
            return False
    return True


def apply_overrides(rows: list[dict[str, str]], fieldnames: list[str],
                    overrides: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[str]]:
    """Return (new_rows, log_lines). Pure function for testability."""
    log: list[str] = []
    out = list(rows)
    for i, ov in enumerate(overrides):
        symbol = ov.get("symbol")
        if not symbol:
            raise ValueError(f"override #{i} missing 'symbol'")
        action = (ov.get("action") or "set").lower()
        match = ov.get("match") or {}
        sets = ov.get("set") or {}
        note = ov.get("note", "")

        if action == "add":
            if "date" not in sets:
                raise ValueError(f"override #{i} ({symbol}) action=add requires set.date")
            new_row = {fn: "" for fn in fieldnames}
            new_row["act_symbol"] = symbol
            for k, v in sets.items():
                if k not in SETTABLE:
                    raise ValueError(f"unsupported set key '{k}' (allowed: {SETTABLE})")
                new_row[k] = "" if v is None else str(v)
            _tag_source(new_row)
            out.append(new_row)
            log.append(f"add    {symbol} {new_row.get('date')} {new_row.get('timing')}  — {note}")
            continue

        matched = [r for r in out if _matches(r, symbol, match)]
        if not matched:
            log.append(f"WARN   {symbol} matched 0 rows for {match}  — {note} (override skipped)")
            continue

        if action == "remove":
            keep = [r for r in out if not _matches(r, symbol, match)]
            log.append(f"remove {symbol} x{len(matched)} for {match}  — {note}")
            out = keep
            continue

        if action != "set":
            raise ValueError(f"override #{i} ({symbol}) unknown action '{action}'")

        for k in sets:
            if k not in SETTABLE:
                raise ValueError(f"unsupported set key '{k}' (allowed: {SETTABLE})")
        for r in matched:
            before = f"{r.get('date')}/{r.get('timing')}"
            for k, v in sets.items():
                r[k] = "" if v is None else str(v)
            _tag_source(r)
            log.append(f"set    {symbol} {before} -> {r.get('date')}/{r.get('timing')}  — {note}")

    return out, log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-missing", action="store_true",
                        help="Exit 0 (no-op) if the overrides file is absent.")
    args = parser.parse_args()

    if not args.overrides.exists():
        msg = f"no overrides file at {args.overrides}"
        if args.allow_missing:
            print(f"{msg}; nothing to apply")
            return 0
        print(msg, file=sys.stderr)
        return 1

    spec = json.loads(args.overrides.read_text())
    overrides = spec.get("overrides", []) if isinstance(spec, dict) else spec
    if not isinstance(overrides, list):
        print("overrides file must contain an 'overrides' list", file=sys.stderr)
        return 1
    if not overrides:
        print("overrides list is empty; nothing to apply")
        return 0

    if not args.csv.exists():
        print(f"earnings CSV not found: {args.csv}", file=sys.stderr)
        return 1

    with args.csv.open(newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    if "act_symbol" not in fieldnames:
        print("earnings CSV missing 'act_symbol' column", file=sys.stderr)
        return 1

    new_rows, log = apply_overrides(rows, fieldnames, overrides)
    for line in log:
        print(("  " if not line.startswith("WARN") else "") + line)
    print(f"overrides: {len(overrides)} rule(s), {len(rows)} -> {len(new_rows)} rows")

    if args.dry_run:
        print("dry run: no file written")
        return 0

    tmp = args.csv.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as fh:
        # lineterminator="\n" preserves the file's existing LF endings (csv's
        # default is CRLF, which would rewrite every line).
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(new_rows)
    tmp.replace(args.csv)
    print(f"wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
