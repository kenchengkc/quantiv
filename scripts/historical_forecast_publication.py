"""Explicit recovery from a prior, independently verified production smoke run."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "kenchengkc/quantiv"
TRUSTED_FILES = (".github/workflows/production-smoke.yml", "scripts/production_smoke.mjs",
                 "scripts/verify_frontend_publication.mjs")
POINTER = "apps/frontend/frontend-release.json"


def _command(arguments, *, repo_root=ROOT, env=None, timeout=30):
    return subprocess.run(arguments, cwd=repo_root, env=env, check=True, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def _github_api(path: str, *, repo_root: Path = ROOT) -> bytes:
    command = ["gh", "api", path]
    try:
        return _command(command, repo_root=repo_root)
    except subprocess.CalledProcessError as error:
        # Newer CLI versions reject ANSI in Actions logs even with stdout piped.
        # Retry only that known guard for the fixed job-log endpoint. These bytes
        # are captured for exact-line verification and never printed to a terminal.
        if (re.fullmatch(rf"repos/{re.escape(REPOSITORY)}/actions/jobs/[1-9][0-9]*/logs", path)
                and (error.stderr or b"").strip() ==
                b"the response contains terminal escape sequences; pass --allow-escape-sequences to output it anyway"):
            return _command([*command, "--allow-escape-sequences"], repo_root=repo_root)
        raise


def _git_pointer(revision: str, repo_root: Path) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("invalid historical smoke checkout revision")
    _command(["git", "merge-base", "--is-ancestor", revision, "HEAD"], repo_root=repo_root)
    # A success line is meaningful only with the known verifier and production
    # endpoint configuration, not arbitrary code that prints the same message.
    for path in TRUSTED_FILES:
        if _command(["git", "show", f"{revision}:{path}"], repo_root=repo_root) != (repo_root / path).read_bytes():
            raise ValueError("historical smoke used a different publication verifier")
    return json.loads(_command(["git", "show", f"{revision}:{POINTER}"], repo_root=repo_root))


def authenticate_smoke_publication(smoke_run_id: str, *, repo_root: Path = ROOT, github_get=None) -> dict:
    """Authenticate one explicitly requested successful run; never search archives."""
    if not re.fullmatch(r"[1-9][0-9]{0,19}", str(smoke_run_id)):
        raise ValueError("historical smoke run ID must be a positive integer")

    def get(path):
        payload = github_get(path) if github_get else _github_api(path, repo_root=repo_root)
        if len(payload) > 4 * 1024 * 1024:
            raise ValueError("historical smoke evidence exceeds bounded response")
        return payload

    base = f"repos/{REPOSITORY}/actions"
    run = json.loads(get(f"{base}/runs/{smoke_run_id}"))
    if (run.get("id") != int(smoke_run_id) or run.get("status") != "completed"
            or run.get("conclusion") != "success" or run.get("path") != TRUSTED_FILES[0]
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("head_repository", {}).get("full_name") != REPOSITORY):
        raise ValueError("historical run is not a successful production smoke in this repository")
    response = json.loads(get(f"{base}/runs/{smoke_run_id}/jobs?per_page=100"))
    jobs = [job for job in response.get("jobs", []) if job.get("name") == "Public frontend and API"]
    if len(jobs) != 1 or jobs[0].get("conclusion") != "success":
        raise ValueError("historical production smoke job did not succeed")
    job = jobs[0]
    steps = [step for step in job.get("steps", []) if step.get("name") == "Run node scripts/production_smoke.mjs"]
    if len(steps) != 1 or steps[0].get("conclusion") != "success" or type(job.get("id")) is not int:
        raise ValueError("historical production verification step did not succeed")
    log = get(f"{base}/jobs/{job['id']}/logs").decode("utf-8")
    # workflow_run checks out main, so the API's head_sha alone is insufficient.
    lines = [re.sub(r"^\d{4}-\d\d-\d\dT\S+\s+", "", line) for line in log.splitlines()]
    revisions = [lines[i + 1].strip() for i, line in enumerate(lines[:-1])
                 if re.fullmatch(r"\[command\].*/git log -1 --format=%H", line)]
    if len(revisions) != 1:
        raise ValueError("historical smoke has no unique verified checkout revision")
    pointer = _git_pointer(revisions[0], repo_root)
    release = pointer.get("release_id", "")
    if not re.fullmatch(r"[0-9a-f]{64}", release):
        raise ValueError("historical frontend pointer has invalid release identity")
    if (f"release {release}: 6 public files verified" not in lines
            or "ok: Git-pinned frontend publication" not in lines):
        raise ValueError("historical smoke did not verify the exact frontend publication")
    return {"run_id": int(smoke_run_id), "job_id": job["id"], "checkout_revision": revisions[0],
            "release_id": release, "deployment_pointer": pointer}


def prepare_historical_publication(smoke_run_id: str, *, data_dir: Path, repo_root: Path = ROOT) -> None:
    """Materialize only authenticated historical proof, separately from live data."""
    from published_forecast_retention import _verified_public_members, _read_public
    destination = data_dir.resolve() / ".forecast_retention/historical"
    shutil.rmtree(destination, ignore_errors=True)
    proof = authenticate_smoke_publication(smoke_run_id, repo_root=repo_root)
    destination.mkdir(parents=True)
    pointer = destination / "deployment.json"
    pointer.write_text(json.dumps(proof["deployment_pointer"]))
    public = destination / "public"
    env = {**os.environ, "PUBLIC_DIR": str(public), "FRONTEND_DEPLOYMENT_POINTER": str(pointer),
           "FRONTEND_RELEASE_REQUIRED": "1", "RCLONE_BIN": os.getenv("RCLONE_BIN", "rclone")}
    _command(["bash", "scripts/materialize_frontend_release.sh"], repo_root=repo_root, env=env, timeout=180)
    members = _verified_public_members(public, pointer)
    _read_public(public, members, "screener.json")
    _read_public(public, members, "evidence/forecast.json")
    # This ephemeral file is created only after the authenticated API/log check.
    # It is never an R2 archive selector or a claim about current production.
    (destination / "verified-smoke.json").write_text(json.dumps(proof))


def historical_publication_paths(data_dir: Path, *, repo_root: Path = ROOT):
    root = data_dir / ".forecast_retention/historical"
    proof_path = root / "verified-smoke.json"
    if not proof_path.exists():
        return None
    proof = json.loads(proof_path.read_text())
    pointer = _git_pointer(proof["checkout_revision"], repo_root)
    if pointer != proof["deployment_pointer"] or pointer != json.loads((root / "deployment.json").read_text()):
        raise ValueError("historical production proof differs from its Git-pinned release")
    return root / "public", root / "deployment.json"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-run-id", required=True)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    prepare_historical_publication(args.smoke_run_id, data_dir=args.data_dir)
    print(f"Verified historical production publication from smoke run {args.smoke_run_id}")
