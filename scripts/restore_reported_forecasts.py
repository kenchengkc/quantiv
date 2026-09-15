#!/usr/bin/env python3
"""Recover published expected moves for events that eroded out of a week bundle.

`compute_em_math` only answers for a pre-event observation, so a rebuild cannot
reprice an earnings event once it has reported. Until the reported-event
preservation in `build_frontend_data.py` was fixed, each daily refresh dropped
the reporters that had just passed, so a week bundle shrank as the week went on
(Sep 7-11 went 13 events on Tuesday → 9 on Wednesday → 5 on Thursday). The
calendar keeps showing those events, because `calendar-reference.json` is
authoritative for membership, but with no forecast attached.

The forecasts are not gone: every daily refresh is a commit, so the last version
of the bundle that still contained a row holds the numbers as they were
published before the print. This walks the git history of each bundle and
restores those rows.

Two guards keep the recovery honest:

  * only events that have already reported are restored. Upcoming events churn
    between refreshes as model forecasts come and go, and reinstating a stale
    row for one would publish a forecast the current model does not stand
    behind.
  * an event is only restored if `calendar-reference.json` lists that exact
    (ticker, earnings_date). The reference is the authority on which events are
    real, so a revised date that the dedup collapsed can never come back as a
    duplicate of the canonical event.

Dry-run by default; pass --apply to write the bundles.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIR = REPO_ROOT / "apps" / "frontend" / "public"
WEEKS_DIR = PUBLIC_DIR / "weeks"
CALENDAR_REFERENCE = PUBLIC_DIR / "calendar-reference.json"

if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
from frontend_data.payloads import build_screener_payload  # noqa: E402

EventKey = tuple[str, str]


def _git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout


def _load_reference_membership() -> tuple[set[EventKey], dict[str, str]]:
    """Return the authoritative (ticker, date) set and the reference window."""
    payload = json.loads(CALENDAR_REFERENCE.read_text(encoding="utf-8"))
    events = payload.get("events") or []
    membership = {(e["ticker"], e["earnings_date"]) for e in events}
    return membership, payload["window"]


def _history(path: Path) -> list[str]:
    rel = path.relative_to(REPO_ROOT).as_posix()
    return _git("log", "--format=%H", "--", rel).decode().split()


def _bundle_at(commit: str, path: Path) -> dict | None:
    rel = path.relative_to(REPO_ROOT).as_posix()
    try:
        return json.loads(_git("show", f"{commit}:{rel}"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def _summary(events: list[dict]) -> dict:
    straddle = [e["em_straddle_pct"] for e in events if e.get("em_straddle_pct") is not None]
    iv = [e["em_iv_pct"] for e in events if e.get("em_iv_pct") is not None]
    return {
        "total_events": len(events),
        "avg_em_straddle_pct": sum(straddle) / len(straddle) if straddle else 0,
        "avg_em_iv_pct": sum(iv) / len(iv) if iv else 0,
    }


def recover_week(
    path: Path,
    membership: set[EventKey],
    today: date,
    apply: bool,
) -> list[EventKey]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    events = bundle.get("events") or []
    present = {(e["ticker"], e["earnings_date"]) for e in events}

    # Only reported events inside the reference window are recoverable.
    wanted = {
        key
        for key in membership
        if key not in present
        and key[1] <= today.isoformat()
        and bundle["window"]["start"] <= key[1] <= bundle["window"]["end"]
    }
    if not wanted:
        return []

    # Newest commit first, so the row is taken from the last refresh that still
    # published it — the closest observation to the print, and the one most
    # likely to already carry a realized move and reported actuals.
    recovered: dict[EventKey, dict] = {}
    for commit in _history(path):
        if not wanted:
            break
        historical = _bundle_at(commit, path)
        if not historical:
            continue
        for event in historical.get("events") or []:
            key = (event["ticker"], event["earnings_date"])
            if key in wanted:
                recovered[key] = event
                wanted.discard(key)

    if not recovered:
        return []

    merged = sorted(
        [*events, *recovered.values()], key=lambda e: (e["earnings_date"], e["ticker"])
    )
    bundle["events"] = merged
    if "summary" in bundle:
        bundle["summary"] = {**bundle["summary"], **_summary(merged)}

    if apply:
        path.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
    return sorted(recovered)


def _republish_manifest_and_screener() -> None:
    """Keep the derived publications in step with the recovered bundles.

    weeks/manifest.json carries a per-week event count the contract tests pin
    against the bundle, and screener.json is a flattened view of the same weeks,
    so both have to be rewritten or the recovered events would be visible on the
    calendar but missing from /screener.
    """
    manifest_path = WEEKS_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    week_payloads: dict[date, dict] = {}
    for week in manifest["weeks"]:
        payload = json.loads((WEEKS_DIR / f"{week['start']}.json").read_text(encoding="utf-8"))
        week_payloads[date.fromisoformat(week["start"])] = payload
        week["count"] = len(payload.get("events") or [])
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    as_of_date = date.fromisoformat(manifest["as_of_date"])
    this_monday = date.fromisoformat(manifest["current_week"])
    screener = build_screener_payload(as_of_date, this_monday, week_payloads)
    screener_path = PUBLIC_DIR / "screener.json"
    screener_path.write_text(json.dumps(screener, indent=2, default=str), encoding="utf-8")
    print(
        f"  republished weeks/manifest.json and screener.json "
        f"({screener['metadata']['event_count']} events)"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the bundles (default: dry run)")
    ap.add_argument("--today", help="override the reported-vs-upcoming cutoff (YYYY-MM-DD)")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    membership, window = _load_reference_membership()
    print(
        f"calendar-reference window {window['start']}..{window['end']} "
        f"({len(membership)} events), reported cutoff {today.isoformat()}"
    )

    total = 0
    for path in sorted(WEEKS_DIR.glob("*.json")):
        if path.name == "manifest.json":
            continue
        restored = recover_week(path, membership, today, args.apply)
        if restored:
            total += len(restored)
            print(f"  {path.name}: restored {len(restored)} reported events")
            for ticker, earnings_date in restored:
                print(f"    {earnings_date}  {ticker}")

    if not total:
        print("nothing to restore")
        return 0
    if not args.apply:
        print(f"\ndry run — {total} events would be restored; re-run with --apply")
        return 0

    _republish_manifest_and_screener()
    print(f"\nrestored {total} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
