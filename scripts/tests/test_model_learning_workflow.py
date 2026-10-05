from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _steps():
    return yaml.safe_load((ROOT / ".github/workflows/model-retrain.yml").read_text())["jobs"]["retrain"]["steps"]


def test_history_can_train_and_archive_before_any_live_forecast_gate():
    steps = _steps()
    pull = next(step for step in steps if step["name"] == "Pull verified production data from R2")
    assert pull["env"]["R2_HISTORICAL_TRAINING"] == "1"
    assert not any("check_duckdb_freshness.py" in step.get("run", "") for step in steps)
    assert not any("independent_model_evaluation.py prepare" in step.get("run", "") for step in steps)
    archive = next(i for i, step in enumerate(steps) if "archive_model_candidate.py" in step.get("run", ""))
    score = next(i for i, step in enumerate(steps) if step.get("id") == "shadow-score")
    assert archive < score
    assert "--retain-evidence" in steps[archive]["run"]
    assert "MODEL_BUNDLE_SIGNING_KEY" in steps[archive]["env"]


def test_activation_and_rollback_remain_bound_to_current_data():
    steps = _steps()
    gate = next(step for step in steps if step.get("id") == "activation-data")
    assert "verify_retrain_data_gate.py" in gate["run"]
    assert "--allow-hold" in gate["run"]
    for command in ("decide", "evaluate-outcomes"):
        step = next(step for step in steps if f"model_control_plane.py {command}" in step.get("run", ""))
        assert "--activation-gate-report" in step["run"]
    retained = next(step for step in steps if step.get("id") == "register-candidate")
    assert "evaluation_manifest" in retained["run"]
    push = next(step for step in steps if step["name"] == "Push activated models to R2")
    assert "promoted == 'true'" in push["if"]
    assert "--model-recovery" in push["run"]


def test_nightly_freeze_collects_bmo_shadow_evidence_under_publication_gate():
    steps = yaml.safe_load((ROOT / ".github/workflows/event-forecast-freeze.yml").read_text())["jobs"]["freeze"]["steps"]
    monitor = next(step for step in steps if step.get("id") == "model-publication")
    assert "--forecast-path data/validation/event-freeze-candidate.parquet" in monitor["run"]
    assert "MODEL_BUNDLE_SIGNING_KEY" in monitor["env"]
    promotion = next(step for step in steps if step["name"] == "Promote only event-cutoff-eligible rows")
    assert "can_publish_ml == 'true'" in promotion["if"]
    build = next(step for step in steps if step["name"] == "Rebuild public research from the eligible ledger")
    assert "--verify-model-publication" in build["run"]
