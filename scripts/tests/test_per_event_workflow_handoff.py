"""Exercise workflow shell boundaries so raw rows cannot reach publication sinks."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("job", ["refresh", "recovery", "freeze"])
@pytest.mark.parametrize("eligible", [True, False])
def test_workflow_validates_and_imports_only_monitored_eligible_rows(tmp_path, job, eligible):
    """Wrong ordering or candidate-path reuse reaches a rejecting real shell probe."""
    workflow_path = ROOT / ".github/workflows" / (
        "event-forecast-freeze.yml" if job == "freeze" else "data-refresh.yml"
    )
    steps = yaml.safe_load(workflow_path.read_text())["jobs"][job]["steps"]
    selected = [step for step in steps if (
        step["name"].startswith(("Score upcoming", "Score a candidate", "Monitor champion",
                                 "Record signed champion", "Gate — validate scored",
                                 "Gate — validate recovered", "Validate candidate",
                                 "Import recent forecasts", "Import recovered forecasts",
                                 "Promote only event-cutoff-eligible"))
    )]
    probe = tmp_path / "bin/python"
    probe.parent.mkdir()
    probe.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
args = sys.argv[1:]
if args and args[0] == '-u':
    args = args[1:]
script = args[0]
def option(name):
    return args[args.index(name)+1]
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
raw = pathlib.Path(os.environ['RAW_PATH'])
subset = pathlib.Path(os.environ['ELIGIBLE_PATH'])
has_rows = os.environ['ELIGIBLE_ROWS'] == '1'
if script.endswith('daily_score.py'):
    raw.write_text('raw includes held rows')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'forecast_path={raw}\\nhas_forecasts=true\\n')
elif script.endswith('model_publication.py') and 'monitor' in args:
    if option('--forecast-path') != str(raw):
        sys.exit('monitor must authenticate the raw scoring candidate')
    subset.write_text('eligible' if has_rows else 'empty')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'eligible_forecast_path={subset}\\nhas_forecasts={str(has_rows).lower()}\\ncan_publish_ml={str(has_rows).lower()}\\n')
elif script.endswith('validate_ml_pipeline.py'):
    if not has_rows or option('--forecast-path') != str(subset) or not subset.exists():
        sys.exit('validation consumed unselected rows or ran before signed selection')
elif script.endswith('import_recent_to_postgres.py'):
    if not has_rows or option('--file') != str(subset):
        sys.exit('Neon import consumed raw or held rows')
elif script.endswith('promote_event_freeze.py'):
    if not has_rows or args[1] != str(subset):
        sys.exit('event publication consumed raw or held rows')
''')
    probe.chmod(0o755)
    outputs = {"options_gate.can_refresh": "true", "input_gate.ready": "true"}
    log = tmp_path / "calls.jsonl"
    env = {**os.environ, "PATH": f"{probe.parent}:{os.environ['PATH']}",
           "CALL_LOG": str(log), "RAW_PATH": str(tmp_path / "raw.parquet"),
           "ELIGIBLE_PATH": str(tmp_path / "eligible.parquet"),
           "ELIGIBLE_ROWS": "1" if eligible else "0"}

    def resolve(value):
        return re.sub(r"\$\{\{\s*steps\.([\w-]+)\.outputs\.([\w-]+)\s*\}\}",
                      lambda match: outputs.get(f"{match[1]}.{match[2]}", ""), value)

    for index, step in enumerate(selected):
        condition = step.get("if", "")
        guards = re.findall(r"steps\.([\w-]+)\.outputs\.([\w-]+) == 'true'", condition)
        if any(outputs.get(f"{step_id}.{key}") != "true" for step_id, key in guards):
            continue
        output_path = tmp_path / f"output-{index}"
        result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", resolve(step["run"])],
                                cwd=ROOT, env={**env, "GITHUB_OUTPUT": str(output_path)},
                                capture_output=True, text=True)
        assert result.returncode == 0, f"{step['name']}: {result.stderr}"
        if step.get("id") and output_path.exists():
            for line in output_path.read_text().splitlines():
                key, value = line.split("=", 1)
                outputs[f"{step['id']}.{key}"] = value

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    validations = [args for args in calls if args[0].endswith("validate_ml_pipeline.py")]
    imports = [args for args in calls if args[0].endswith(("import_recent_to_postgres.py", "promote_event_freeze.py"))]
    assert len(validations) == (1 if eligible else 0)
    assert len(imports) == (1 if eligible else 0)
