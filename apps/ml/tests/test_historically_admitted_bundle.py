from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from apps.ml.tests.test_historical_admission import _candidate  # noqa: E402
from ml.historical_admission import evaluate_historical_admission  # noqa: E402
from ml.model_bundle import (  # noqa: E402
    ModelBundleError, _bundle_core, _sign, create_historically_admitted_bundle,
    verify_bundle_dir, verify_bundle_manifest,
)
from scripts.daily_score import load_models, score  # noqa: E402


@pytest.fixture
def admitted_bundle(tmp_path, monkeypatch):
    source, reservation, model_receipt, private, public = _candidate(tmp_path, monkeypatch)
    _, receipt = evaluate_historical_admission(source, reservation, model_receipt,
        tmp_path / "dev", tmp_path / "evidence", private_key=private, public_key=public,
        reservation_registry=tmp_path / "registry")
    destination, manifest = create_historically_admitted_bundle(source, tmp_path / "bundles",
        admission_receipt=receipt, source_revision="admitted", private_key=private)
    return source, destination, manifest, private, public


def test_derived_bundle_preserves_source_bytes_and_limits_live_cohorts(admitted_bundle):
    source, destination, manifest, _, public = admitted_bundle
    parent = verify_bundle_dir(source, public_key=public)
    verified = verify_bundle_dir(destination, public_key=public)
    assert verified["bundle_id"] != parent["bundle_id"]
    assert verified["receipt_id"] == parent["receipt_id"]
    assert verified["horizons"] == [7]
    assert verified["serving_capabilities"] == {"7": ["strict_options"]}
    assert verified["historical_admission"]["source_manifest"] == json.loads((source / "manifest.json").read_text())
    for artifact in manifest["artifacts"]:
        assert (destination / artifact["name"]).read_bytes() == (source / artifact["name"]).read_bytes()
    frame = pd.DataFrame({
        "act_symbol": ["STRICT", "OPTIONLESS"], "earnings_date": ["2026-12-01"] * 2,
        "lead_days": [7, 7], "spot_price": [100.] * 2, "timing": ["bmo"] * 2,
        "feature": [.05] * 2, "straddle_pct": [.07, None], "hist_move_med_4q": [.06] * 2,
    })
    assert score(frame, load_models(source))["act_symbol"].tolist() == ["STRICT", "OPTIONLESS"]
    assert score(frame, load_models(destination))["act_symbol"].tolist() == ["STRICT"]


@pytest.mark.parametrize("mutation", ["capability", "source", "receipt", "artifact", "nested"])
def test_even_resigned_derived_bundle_cannot_expand_or_change_admitted_source(admitted_bundle, mutation):
    _, _, manifest, private, public = admitted_bundle
    changed = copy.deepcopy(manifest)
    changed.pop("signature")
    if mutation == "capability":
        changed["serving_capabilities"]["7"] = ["strict_options", "optionless"]
    elif mutation == "source":
        changed["historical_admission"]["source_manifest"]["source_revision"] = "altered"
    elif mutation == "receipt":
        changed["historical_admission"]["receipt"]["admitted_cohorts"]["7"] = ["optionless"]
    elif mutation == "artifact":
        changed["artifacts"][0]["sha256"] = "a" * 64
    else:
        changed["historical_admission"]["source_manifest"] = manifest
    core = _bundle_core(**{key: changed[key] for key in (
        "artifacts", "receipt_id", "horizons", "source_revision", "created_at",
        "serving_capabilities", "historical_admission",
    )})
    with pytest.raises(ModelBundleError):
        verify_bundle_manifest(_sign(core, private), public_key=public)
