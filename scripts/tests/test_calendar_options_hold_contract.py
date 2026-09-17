from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_calendar_publication_is_outside_scoring_gate() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/data-refresh.yml").read_text())
    steps = workflow["jobs"]["refresh"]["steps"]

    fallback = next(
        step
        for step in steps
        if step["name"] == "Restore validated fallback after rejected options candidate"
    )
    promotion = next(
        step for step in steps if step["name"].startswith("Promote reconciled parquet")
    )
    score = next(step for step in steps if step["name"] == "Score upcoming earnings")
    validate = next(
        step for step in steps if step["name"].startswith("Gate — validate scored")
    )
    neon = next(
        step for step in steps if step["name"] == "Import recent forecasts to Neon"
    )
    forecast_push = next(
        step for step in steps if step["name"] == "Push forecasts to R2"
    )
    frontend = next(step for step in steps if step["name"] == "Build frontend JSON")

    # The reconciled-data promotion is reached after an accepted candidate or a
    # verified fallback and is deliberately not conditional on strict can_score.
    assert "if" not in promotion
    assert "bash scripts/r2_push.sh --skip-forecasts" in promotion["run"]
    assert "steps.options_gate.outputs.options_state == 'fallback'" in str(
        fallback.get("if", "")
    )

    # Downstream refresh is allowed for either a strict accepted candidate or
    # a restored published fallback. can_score keeps the narrower options-only
    # decision-safe meaning.
    expected_gate = "steps.options_gate.outputs.can_refresh == 'true'"
    for step in (score, validate, neon, forecast_push, frontend):
        assert expected_gate in str(step.get("if", ""))

    assert steps.index(fallback) < steps.index(promotion) < steps.index(score)
    assert steps.index(score) < steps.index(frontend)


def test_skip_forecasts_path_publishes_only_timestamped_daily_calendar() -> None:
    script = (ROOT / "scripts/r2_push.sh").read_text()
    skip_block = script.split('elif [ "$MODE" = "--skip-forecasts" ]; then', 1)[1].split(
        "\nelse\n", 1
    )[0]

    assert 'if [ -n "${REFRESH_STARTED_AT:-}" ]; then' in skip_block
    assert 'CALENDAR_REFERENCE_NOT_BEFORE="$REFRESH_STARTED_AT"' in skip_block
    assert "bash scripts/r2_push_calendar_reference.sh" in skip_block
    assert "$SHELL" not in skip_block
