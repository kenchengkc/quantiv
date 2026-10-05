from pathlib import Path

import pytest

from scripts.archive_model_candidate import archive_candidate


def test_archive_verifies_bundle_before_any_remote_mutation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("scripts.archive_model_candidate.subprocess.run", lambda *a, **k: calls.append(a))
    with pytest.raises(Exception, match="manifest"):
        archive_candidate(bundle_dir=tmp_path, remote="r2:test")
    assert calls == []


def test_archive_only_copies_verified_immutable_bundle_and_checks_readback(tmp_path, monkeypatch):
    calls = []
    bundle = tmp_path / ("a" * 64)
    bundle.mkdir()
    monkeypatch.setattr("scripts.archive_model_candidate.verify_bundle_dir", lambda path: {"bundle_id": "a" * 64})
    monkeypatch.setattr("scripts.archive_model_candidate.subprocess.run", lambda argv, **kwargs: calls.append(argv))
    result = archive_candidate(bundle_dir=bundle, remote="r2:test")
    assert result["status"] == "passed"
    assert calls[0][:2] == ["rclone", "copy"]
    assert "--immutable" in calls[0]
    assert calls[1][:2] == ["rclone", "check"]
    assert calls[1][3] == f"r2:test/models/bundles/{'a' * 64}"
    assert all("sync" not in call and "champion.json" not in str(call) for call in calls)


def test_daily_upload_cannot_delete_candidates_or_restore_stale_model_pointers():
    text = (Path(__file__).resolve().parents[1] / "r2_push.sh").read_text()
    block = text.split('elif [ "$MODE" = "--skip-forecasts" ]; then')[1].split("\nelse\n")[0]
    assert "push_models" not in block
    assert "promote_model_champion" not in block
    model_block = text.split("push_models() {")[1].split("\n}\n")[0]
    assert "rclone sync" not in model_block
    assert '--exclude "/candidates/**"' in model_block
    assert '--exclude "/evaluations/**"' in model_block
