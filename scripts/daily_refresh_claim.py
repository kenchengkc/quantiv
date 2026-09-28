#!/usr/bin/env python3
"""Choose exactly one normal daily-refresh workflow run per Eastern calendar day."""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
RECOVERY_MARKER = "provider-free-recovery"
NORMAL_EVENTS = {"schedule", "workflow_dispatch"}


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp must be timezone-aware: {value!r}")
    return parsed


def eastern_run_date(run: dict[str, Any]) -> str:
    return _parse_timestamp(str(run["created_at"])).astimezone(ET).date().isoformat()


def is_provider_free_recovery(run: dict[str, Any]) -> bool:
    title = str(run.get("display_title") or run.get("name") or "").lower()
    return RECOVERY_MARKER in title


def normal_runs_for_date(runs: list[dict[str, Any]], claim_date: str) -> list[dict[str, Any]]:
    eligible = []
    for run in runs:
        if run.get("event") not in NORMAL_EVENTS:
            continue
        if is_provider_free_recovery(run):
            continue
        try:
            if eastern_run_date(run) != claim_date:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        eligible.append(run)
    return eligible


def choose_owner_run_id(runs: list[dict[str, Any]], claim_date: str) -> int | None:
    eligible = normal_runs_for_date(runs, claim_date)
    if not eligible:
        return None
    owner = min(
        eligible,
        key=lambda run: (
            _parse_timestamp(str(run["created_at"])),
            int(run["id"]),
        ),
    )
    return int(owner["id"])


def evaluate_claim(
    *,
    current_run: dict[str, Any],
    workflow_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    run_id = int(current_run["id"])
    run_attempt = int(current_run.get("run_attempt") or 1)
    claim_date = eastern_run_date(current_run)

    if run_attempt > 1:
        return {
            "claimed": False,
            "claim_date": claim_date,
            "owner_run_id": run_id,
            "reason": "normal refresh reruns are disabled; use provider-free recovery",
        }

    candidates = list(workflow_runs)
    if not any(int(run.get("id") or 0) == run_id for run in candidates):
        candidates.append(current_run)

    owner_run_id = choose_owner_run_id(candidates, claim_date)
    if owner_run_id is None:
        owner_run_id = run_id

    claimed = owner_run_id == run_id
    return {
        "claimed": claimed,
        "claim_date": claim_date,
        "owner_run_id": owner_run_id,
        "reason": (
            "current run owns the daily refresh"
            if claimed
            else f"daily refresh already claimed by workflow run {owner_run_id}"
        ),
    }


def _github_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "quantiv-daily-refresh-claim",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _write_output(path: Path | None, result: dict[str, Any]) -> None:
    if path is None:
        return
    with path.open("a") as handle:
        handle.write(f"claimed={'true' if result['claimed'] else 'false'}\n")
        handle.write(f"claim_date={result['claim_date']}\n")
        handle.write(f"owner_run_id={result['owner_run_id']}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY"))
    parser.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID"))
    parser.add_argument("--workflow", default="data-refresh.yml")
    parser.add_argument("--github-output", type=Path, default=None)
    parser.add_argument("--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com"))
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required to claim the daily refresh")
    if not args.repository or not args.run_id:
        raise SystemExit("GITHUB_REPOSITORY and GITHUB_RUN_ID are required")

    repo = urllib.parse.quote(str(args.repository), safe="/")
    workflow = urllib.parse.quote(str(args.workflow), safe="")
    current = _github_json(
        f"{args.api_url}/repos/{repo}/actions/runs/{int(args.run_id)}",
        token,
    )
    listing = _github_json(
        f"{args.api_url}/repos/{repo}/actions/workflows/{workflow}/runs?per_page=100",
        token,
    )
    result = evaluate_claim(
        current_run=current,
        workflow_runs=list(listing.get("workflow_runs") or []),
    )
    _write_output(args.github_output, result)
    print(
        f"Daily refresh claim {result['claim_date']}: "
        f"{'owned' if result['claimed'] else 'skipped'}; {result['reason']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
