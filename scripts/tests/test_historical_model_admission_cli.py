from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/research/historical_model_admission.py"


def test_prepare_cli_rejects_forged_passed_source_without_immutable_source_release(tmp_path, monkeypatch):
    from apps.ml.tests.test_historical_admission import _install_fixture_trust
    _install_fixture_trust(monkeypatch, tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    dates = pd.date_range("2023-01-01", periods=1500)
    pd.DataFrame({"feature": .04, "straddle_pct": .05, "target": .03,
        "__symbol": "S", "__earnings_date": dates,
        "__snapshot_date": dates - pd.Timedelta(days=7),
        "__pre_price_date": dates - pd.Timedelta(days=1), "__post_price_date": dates,
        "__target_protocol": "quantiv.session-reaction.v2",
        "__label_available_at": dates + pd.Timedelta(days=1)}).to_parquet(source / "training_T7.parquet", index=False)
    (source / "metadata_T7.json").write_text(json.dumps({
        "feature_protocol": "quantiv.earnings-causal.v2", "target_protocol": "quantiv.session-reaction.v2"}))
    admission = tmp_path / "source_admission.json"
    admission.write_text(json.dumps({"schema": "quantiv.historical-training-admission.v1",
        "status": "passed", "release_id": "a" * 64, "earnings_calendar_sha256": "b" * 64,
        "calendar_source": {"path": "earnings_calendar.csv", "sha256": "b" * 64},
        "corporate_actions": {"receipt_id": "c" * 64}}))
    result = subprocess.run([sys.executable, str(CLI), "prepare",
        "--source-training-dir", str(source), "--training-dir", str(tmp_path / "dev"),
        "--staging-dir", str(tmp_path / "sealed"), "--horizons", "7", "--as-of", "2030-01-01",
        "--source-data-dir", str(tmp_path / "source_data"),
        "--reservation-registry", str(tmp_path / "registry"),
        "--source-admission-report", str(admission)],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "current_data_release.json" in result.stderr
    assert not (tmp_path / "dev/training_T7.parquet").exists()
