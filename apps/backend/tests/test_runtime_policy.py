from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_railway_root_exception_and_image_provenance_are_explicit() -> None:
    dockerfile = (REPO_ROOT / "apps" / "backend" / "Dockerfile").read_text()
    policy = (REPO_ROOT / "docs" / "RAILWAY_RUNTIME_SECURITY.md").read_text()
    assert "FROM python:3.11-slim-bookworm" in dockerfile
    assert "USER 0:0" in dockerfile
    assert "chmod -R a-w /app" in dockerfile
    assert "Railway" in policy and "/data" in policy
    assert "monthly" in policy.lower()
    assert "critical" in policy.lower()
