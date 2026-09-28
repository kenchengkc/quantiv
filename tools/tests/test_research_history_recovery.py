import json
import sys

import pytest

import build_research_history as builder
from validate_public_contracts import ContractError


def _run_preserved(monkeypatch, path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_research_history.py",
            "--preserve-existing",
            "--output",
            str(path),
        ],
    )

    def unexpected_provider(*args, **kwargs):
        pytest.fail("Recovery attempted to fetch research provider data")

    monkeypatch.setattr(
        builder,
        "fetch_retired_research_sources",
        unexpected_provider,
    )
    monkeypatch.setattr(builder, "query", unexpected_provider)
    return builder.main()


def test_recovery_validates_and_preserves_existing_history_without_providers(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "research-history.json"
    original = (builder.PUBLIC_DIR / "research-history.json").read_bytes()
    path.write_bytes(original)

    assert _run_preserved(monkeypatch, path) == 0
    assert path.read_bytes() == original


def test_recovery_rejects_invalid_existing_history(monkeypatch, tmp_path):
    path = tmp_path / "research-history.json"
    payload = json.loads(
        (builder.PUBLIC_DIR / "research-history.json").read_text()
    )
    payload["event_count"] += 1
    path.write_text(json.dumps(payload))

    with pytest.raises(ContractError, match="event_count"):
        _run_preserved(monkeypatch, path)


def test_recovery_requires_existing_history(monkeypatch, tmp_path):
    with pytest.raises((ContractError, FileNotFoundError)):
        _run_preserved(monkeypatch, tmp_path / "missing.json")
