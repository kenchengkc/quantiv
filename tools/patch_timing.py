#!/usr/bin/env python3
"""Backfill timing (bmo/amc) into pre-generated weekly.json / weeks/*.json / symbols/*.json
from data/earnings_calendar.csv. Run after updating the CSV source but before a full rebuild."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "earnings_calendar.csv"
PUBLIC = ROOT / "apps" / "frontend" / "public"

TIMING_MAP = {"bmo": "before_market_open", "amc": "after_market_close"}


def load_lookup() -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    with CSV_PATH.open(newline="") as fh:
        for row in csv.DictReader(fh):
            t = (row.get("timing") or "").strip().lower()
            mapped = TIMING_MAP.get(t, "unknown")
            out[(row["act_symbol"], row["date"])] = mapped
    return out


def patch_events(events, lookup):
    changed = 0
    for ev in events:
        key = (ev.get("ticker"), (ev.get("earnings_date") or "")[:10])
        new_t = lookup.get(key)
        if new_t and ev.get("timing") != new_t:
            ev["timing"] = new_t
            changed += 1
    return changed


def patch_file(path: Path, lookup):
    data = json.loads(path.read_text())
    changed = 0
    if isinstance(data, dict) and "events" in data:
        changed = patch_events(data["events"], lookup)
    elif isinstance(data, dict) and "next_earnings" in data:
        ticker = data.get("symbol")
        nxt = data.get("next_earnings")
        if ticker and nxt:
            key = (ticker, nxt[:10])
            new_t = lookup.get(key)
            if new_t:
                if data.get("next_earnings_timing") != new_t:
                    data["next_earnings_timing"] = new_t
                    changed += 1
                em = data.get("expected_move")
                if isinstance(em, dict) and em.get("timing") != new_t:
                    em["timing"] = new_t
                    changed += 1
    if changed:
        path.write_text(json.dumps(data, indent=2))
    return changed


def main():
    lookup = load_lookup()
    targets: list[Path] = []
    if (PUBLIC / "weekly.json").exists():
        targets.append(PUBLIC / "weekly.json")
    targets += sorted((PUBLIC / "weeks").glob("*.json"))
    targets += sorted((PUBLIC / "symbols").glob("*.json"))

    total_files = 0
    total_events = 0
    for p in targets:
        if p.name == "manifest.json":
            continue
        c = patch_file(p, lookup)
        if c:
            total_files += 1
            total_events += c
    print(f"patched {total_events} events across {total_files} files")


if __name__ == "__main__":
    main()
