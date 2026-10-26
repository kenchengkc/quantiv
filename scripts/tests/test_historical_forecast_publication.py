"""A prior forecast needs successful production verification of its exact release."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from scripts.historical_forecast_publication import authenticate_smoke_publication


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "40231566942"
JOB_ID = 121284257939
RELEASE_ID = "a" * 64
BASE = "repos/kenchengkc/quantiv/actions"
VERIFIERS = (
    ".github/workflows/production-smoke.yml",
    "scripts/production_smoke.mjs",
    "scripts/verify_frontend_publication.mjs",
)
POINTER = "apps/frontend/frontend-release.json"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


@pytest.fixture
def publication(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    for relative in VERIFIERS:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    pointer = {"schema": "quantiv.frontend-deployment.v1", "release_id": RELEASE_ID}
    _write_json(repo / POINTER, pointer)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "Publish first fixture release")
    revision = _git(repo, "rev-parse", "HEAD")
    # The API's head SHA and the current deployment are deliberately newer.
    # Only the checkout logged by the successful smoke identifies its release.
    _write_json(repo / POINTER, {**pointer, "release_id": "b" * 64})
    _git(repo, "commit", "-qam", "Publish later fixture release")
    run = {
        "id": int(RUN_ID), "status": "completed", "conclusion": "success",
        "path": ".github/workflows/production-smoke.yml",
        "repository": {"full_name": "kenchengkc/quantiv"},
        "head_repository": {"full_name": "kenchengkc/quantiv"},
        "head_sha": _git(repo, "rev-parse", "HEAD"),
    }
    job = {
        "id": JOB_ID, "name": "Public frontend and API", "status": "completed",
        "conclusion": "success", "steps": [{
            "name": "Run node scripts/production_smoke.mjs",
            "status": "completed", "conclusion": "success",
        }],
    }
    log = "\n".join([
        "2026-10-25T12:00:00.0000000Z [command]/usr/bin/git log -1 --format=%H",
        f"2026-10-25T12:00:00.0000001Z {revision}",
        f"2026-10-25T12:00:01.0000000Z release {RELEASE_ID}: 6 public files verified",
        "2026-10-25T12:00:01.0000001Z ok: Git-pinned frontend publication",
    ])
    state = SimpleNamespace(repo=repo, pointer=pointer, revision=revision,
                            run=run, jobs={"jobs": [job]}, log=log, calls=[])

    def github_get(path):
        state.calls.append(path)
        responses = {
            f"{BASE}/runs/{RUN_ID}": json.dumps(state.run).encode(),
            f"{BASE}/runs/{RUN_ID}/jobs?per_page=100": json.dumps(state.jobs).encode(),
            f"{BASE}/jobs/{JOB_ID}/logs": state.log.encode(),
        }
        return responses[path]

    state.github_get = github_get
    return state


def _authenticate(publication):
    return authenticate_smoke_publication(
        RUN_ID, repo_root=publication.repo, github_get=publication.github_get,
    )


def test_success_authenticates_the_logged_ancestor_release_not_the_api_head(publication):
    proof = _authenticate(publication)

    assert proof == {
        "run_id": 40231566942, "job_id": 121284257939,
        "checkout_revision": publication.revision, "release_id": "a" * 64,
        "deployment_pointer": publication.pointer,
    }
    assert proof["checkout_revision"] != publication.run["head_sha"]


@pytest.mark.parametrize("run_id", ["", "0", "-1", "1/../../jobs", "1; echo unsafe", "1" * 21])
def test_malformed_run_id_is_rejected_before_requesting_evidence(publication, run_id):
    with pytest.raises(ValueError):
        authenticate_smoke_publication(
            run_id, repo_root=publication.repo, github_get=publication.github_get,
        )
    assert publication.calls == []


@pytest.mark.parametrize("change", [
    {"id": 1}, {"status": "in_progress"}, {"conclusion": "failure"},
    {"path": ".github/workflows/ci.yml"},
    {"repository": {"full_name": "other/quantiv"}},
    {"head_repository": {"full_name": "other/quantiv"}},
])
def test_unrelated_or_unsuccessful_run_cannot_prove_publication(publication, change):
    publication.run.update(change)
    with pytest.raises(ValueError):
        _authenticate(publication)


@pytest.mark.parametrize("change", [
    {"conclusion": "failure"}, {"conclusion": "cancelled"},
    {"name": "Unrelated successful job"}, {"id": str(JOB_ID)},
    {"steps": []},
    {"steps": [{"name": "Run node scripts/production_smoke.mjs", "conclusion": "skipped"}]},
])
def test_failed_or_missing_verification_job_or_step_is_rejected(publication, change):
    publication.jobs["jobs"][0].update(change)
    with pytest.raises(ValueError):
        _authenticate(publication)


def test_duplicate_verification_jobs_are_ambiguous(publication):
    publication.jobs["jobs"].append(deepcopy(publication.jobs["jobs"][0]))
    with pytest.raises(ValueError):
        _authenticate(publication)


@pytest.mark.parametrize("old, replacement", [
    (f"release {RELEASE_ID}: 6 public files verified", "release missing"),
    (f"release {RELEASE_ID}: 6 public files verified", f"release {'b' * 64}: 6 public files verified"),
    (f"release {RELEASE_ID}: 6 public files verified", f"release {RELEASE_ID}: 6 public files verified (failed)"),
    ("ok: Git-pinned frontend publication", "ok: another check"),
    ("[command]/usr/bin/git log -1 --format=%H", "unverified checkout output"),
])
def test_only_exact_checkout_and_release_success_lines_count(publication, old, replacement):
    publication.log = publication.log.replace(old, replacement)
    with pytest.raises(ValueError):
        _authenticate(publication)


def test_checkout_pointer_must_match_the_release_verified_in_logs(publication):
    publication.log = publication.log.replace(publication.revision, publication.run["head_sha"])
    with pytest.raises(ValueError):
        _authenticate(publication)


def test_duplicate_checkout_revisions_are_rejected(publication):
    publication.log += (
        "\n[command]/usr/bin/git log -1 --format=%H\n" + publication.revision
    )
    with pytest.raises(ValueError):
        _authenticate(publication)


@pytest.mark.parametrize("relative", VERIFIERS)
def test_old_success_cannot_authenticate_a_changed_verifier(publication, relative):
    path = publication.repo / relative
    path.write_bytes(path.read_bytes() + b"\n# changed verifier\n")
    with pytest.raises(ValueError):
        _authenticate(publication)


def test_checkout_outside_current_history_cannot_authenticate_a_release(publication):
    _git(publication.repo, "checkout", "-q", "--orphan", "unrelated")
    _git(publication.repo, "add", ".")
    _git(publication.repo, "commit", "-q", "-m", "Unrelated publication history")
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        _authenticate(publication)
