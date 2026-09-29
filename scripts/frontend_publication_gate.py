#!/usr/bin/env python3
"""Decide whether a completed producer workflow should publish the frontend release."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

DAILY_REFRESH_PREFIX = "Daily data refresh"
DAILY_PRODUCER_JOBS = {"refresh", "recovery"}


def evaluate_publication(
    *,
    event_name: str,
    workflow_run: dict[str, Any] | None,
    jobs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if event_name == "workflow_dispatch":
        return {"should_publish": True, "reason": "manual frontend publication"}

    if event_name != "workflow_run" or not isinstance(workflow_run, dict):
        return {"should_publish": False, "reason": f"unsupported event {event_name!r}"}

    if workflow_run.get("conclusion") != "success":
        return {
            "should_publish": False,
            "reason": f"producer conclusion is {workflow_run.get('conclusion')!r}",
        }

    if workflow_run.get("head_branch") != "main":
        return {
            "should_publish": False,
            "reason": f"producer branch is {workflow_run.get('head_branch')!r}, not main",
        }

    name = str(workflow_run.get("name") or "")
    trigger_event = str(workflow_run.get("event") or "")

    if name == "CI" and trigger_event != "push":
        return {
            "should_publish": False,
            "reason": f"CI producer event is {trigger_event!r}, not push",
        }

    if name.startswith(DAILY_REFRESH_PREFIX):
        if jobs is None:
            raise ValueError("Daily data refresh publication requires producer job results")

        producer_states = {
            str(job.get("name")): str(job.get("conclusion"))
            for job in jobs
            if job.get("name") in DAILY_PRODUCER_JOBS
        }
        succeeded = sorted(
            name
            for name, conclusion in producer_states.items()
            if conclusion == "success"
        )
        if succeeded:
            return {
                "should_publish": True,
                "reason": "daily producer job succeeded: " + ", ".join(succeeded),
            }

        states = ", ".join(
            f"{name}={producer_states.get(name, 'missing')}"
            for name in sorted(DAILY_PRODUCER_JOBS)
        )
        return {
            "should_publish": False,
            "reason": f"no daily producer job succeeded ({states})",
        }

    return {
        "should_publish": True,
        "reason": f"successful main-branch producer {name!r}",
    }


def _github_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "quantiv-frontend-publication-gate",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub API response must be a JSON object")
    return payload


def _load_event(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub event payload must be a JSON object")
    return payload


def _write_output(path: Path | None, result: dict[str, Any]) -> None:
    if path is None:
        return
    with path.open("a") as handle:
        handle.write(
            f"should_publish={'true' if result['should_publish'] else 'false'}\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", default=os.getenv("GITHUB_EVENT_NAME", ""))
    parser.add_argument(
        "--event-path",
        type=Path,
        default=Path(os.environ["GITHUB_EVENT_PATH"])
        if os.getenv("GITHUB_EVENT_PATH")
        else None,
    )
    parser.add_argument("--github-output", type=Path, default=None)
    args = parser.parse_args()

    event: dict[str, Any] = {}
    if args.event_path is not None:
        event = _load_event(args.event_path)

    workflow_run = event.get("workflow_run")
    jobs = None

    if (
        args.event_name == "workflow_run"
        and isinstance(workflow_run, dict)
        and str(workflow_run.get("name") or "").startswith(DAILY_REFRESH_PREFIX)
    ):
        token = os.getenv("GITHUB_TOKEN")
        repository = os.getenv("GITHUB_REPOSITORY")
        api_url = os.getenv("GITHUB_API_URL", "https://api.github.com")
        run_id = workflow_run.get("id")
        if not token or not repository or not run_id:
            raise SystemExit(
                "Daily data refresh publication gate requires "
                "GITHUB_TOKEN, GITHUB_REPOSITORY, and workflow_run.id"
            )
        payload = _github_json(
            f"{api_url}/repos/{repository}/actions/runs/{int(run_id)}/jobs"
            "?filter=latest&per_page=100",
            token,
        )
        raw_jobs = payload.get("jobs")
        if not isinstance(raw_jobs, list):
            raise SystemExit("GitHub workflow jobs response is missing jobs[]")
        jobs = [job for job in raw_jobs if isinstance(job, dict)]

    result = evaluate_publication(
        event_name=args.event_name,
        workflow_run=workflow_run if isinstance(workflow_run, dict) else None,
        jobs=jobs,
    )
    _write_output(args.github_output, result)
    print(
        "Frontend publication gate: "
        f"{'publish' if result['should_publish'] else 'skip'}; {result['reason']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
