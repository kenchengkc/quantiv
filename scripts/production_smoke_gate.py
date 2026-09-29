#!/usr/bin/env python3
"""Decide whether a Production smoke workflow should execute its smoke job."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path
from typing import Any


def evaluate_smoke(
    *,
    event_name: str,
    workflow_run: dict[str, Any] | None,
    jobs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if event_name != "workflow_run":
        return {"should_smoke": True, "reason": f"direct {event_name} trigger"}

    if not isinstance(workflow_run, dict):
        return {"should_smoke": False, "reason": "workflow_run payload is missing"}

    if workflow_run.get("conclusion") != "success":
        return {
            "should_smoke": False,
            "reason": f"publication conclusion is {workflow_run.get('conclusion')!r}",
        }

    if workflow_run.get("head_branch") != "main":
        return {
            "should_smoke": False,
            "reason": f"publication branch is {workflow_run.get('head_branch')!r}, not main",
        }

    if jobs is None:
        raise ValueError("Production smoke workflow_run requires publication job results")

    publish_jobs = [
        job for job in jobs if job.get("name") == "publish"
    ]
    if any(job.get("conclusion") == "success" for job in publish_jobs):
        return {
            "should_smoke": True,
            "reason": "frontend publication publish job succeeded",
        }

    conclusion = (
        str(publish_jobs[0].get("conclusion"))
        if publish_jobs
        else "missing"
    )
    return {
        "should_smoke": False,
        "reason": f"frontend publication publish job did not succeed ({conclusion})",
    }


def _github_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "quantiv-production-smoke-gate",
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
            f"should_smoke={'true' if result['should_smoke'] else 'false'}\n"
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

    if args.event_name == "workflow_run" and isinstance(workflow_run, dict):
        token = os.getenv("GITHUB_TOKEN")
        repository = os.getenv("GITHUB_REPOSITORY")
        api_url = os.getenv("GITHUB_API_URL", "https://api.github.com")
        run_id = workflow_run.get("id")
        if not token or not repository or not run_id:
            raise SystemExit(
                "Production smoke gate requires GITHUB_TOKEN, "
                "GITHUB_REPOSITORY, and workflow_run.id"
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

    result = evaluate_smoke(
        event_name=args.event_name,
        workflow_run=workflow_run if isinstance(workflow_run, dict) else None,
        jobs=jobs,
    )
    _write_output(args.github_output, result)
    print(
        "Production smoke gate: "
        f"{'run' if result['should_smoke'] else 'skip'}; {result['reason']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
