from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from apply_earnings_overrides import apply_override_artifacts, apply_overrides  # noqa: E402

FIELDS = ["act_symbol", "date", "timing", "fiscal_year", "fiscal_q", "source"]


def _row(sym, dt, timing, fy, fq, src):
    return {"act_symbol": sym, "date": dt, "timing": timing,
            "fiscal_year": fy, "fiscal_q": fq, "source": src}


def test_set_matches_on_stable_fiscal_identity_and_tags_source():
    rows = [_row("PVH", "2026-06-02", "unknown", "2027", "Q1", "finnhub")]
    out, _ = apply_overrides(rows, FIELDS, [{
        "symbol": "PVH",
        "match": {"fiscal_year": 2027, "fiscal_q": "Q1"},
        "set": {"date": "2026-06-03", "timing": "amc"},
    }])
    assert out[0]["date"] == "2026-06-03"
    assert out[0]["timing"] == "amc"
    assert out[0]["source"] == "finnhub+override"


def test_set_is_idempotent_source_tag_not_duplicated():
    rows = [_row("PVH", "2026-06-03", "amc", "2027", "Q1", "finnhub+override")]
    out, _ = apply_overrides(rows, FIELDS, [{
        "symbol": "PVH",
        "match": {"fiscal_year": 2027, "fiscal_q": "Q1"},
        "set": {"date": "2026-06-03", "timing": "amc"},
    }])
    assert out[0]["source"] == "finnhub+override"


def test_remove_drops_phantom_row():
    rows = [_row("UVV", "2026-06-04", "unknown", "2026", "Q2", "dolthub"),
            _row("UVV", "2026-05-25", "amc", "2026", "Q4", "finnhub")]
    out, _ = apply_overrides(rows, FIELDS, [{
        "symbol": "UVV",
        "match": {"date": "2026-06-04", "fiscal_q": "Q2"},
        "action": "remove",
    }])
    assert len(out) == 1
    assert out[0]["fiscal_q"] == "Q4"


@pytest.mark.parametrize("correct_row_present", [True, False])
def test_stem_reschedule_replaces_original_and_replay_dates(correct_row_present):
    overrides = json.loads((REPO_ROOT / "config/earnings_overrides.json").read_text())["overrides"]
    prior = _row("STEM", "2026-08-12", "amc", "2026", "Q2", "finnhub")
    original = {**_row("STEM", "2026-11-05", "amc", "2026", "Q3", "finnhub"),
                "eps_estimate": "-1.6269"}
    replay = _row("STEM", "2026-12-05", "amc", "2026", "Q3", "reconciled")
    rows = [original, replay, prior]
    if correct_row_present:
        rows.append({**_row("STEM", "2026-11-16", "amc", "2026", "Q3", "finnhub"),
                     "eps_estimate": "-1.57"})
    out, _ = apply_overrides(rows, [*FIELDS, "eps_estimate"], overrides)

    assert [(row["date"], row["timing"], row.get("eps_estimate")) for row in out if row is not prior] == [
        ("2026-11-16", "amc", "-1.57" if correct_row_present else "-1.6269"),
    ]
    assert prior in out
    assert apply_overrides(out, FIELDS, overrides)[0] == out


def test_add_appends_new_event():
    rows = [_row("AAA", "2026-01-01", "amc", "2026", "Q1", "finnhub")]
    out, _ = apply_overrides(rows, FIELDS, [{
        "symbol": "BBB",
        "action": "add",
        "set": {"date": "2026-07-01", "timing": "bmo", "fiscal_year": 2026, "fiscal_q": "Q2"},
    }])
    assert len(out) == 2
    added = out[-1]
    assert added["act_symbol"] == "BBB" and added["date"] == "2026-07-01"
    assert "override" in added["source"]


def test_unmatched_set_warns_and_is_skipped():
    rows = [_row("PVH", "2026-06-02", "unknown", "2027", "Q1", "finnhub")]
    out, log = apply_overrides(rows, FIELDS, [{
        "symbol": "ZZZ",
        "match": {"fiscal_year": 1999, "fiscal_q": "Q1"},
        "set": {"date": "1999-01-01"},
    }])
    assert out == rows  # unchanged
    assert any(line.startswith("WARN") for line in log)



def test_artifact_override_keeps_csv_and_parquet_event_keys_aligned(tmp_path) -> None:
    import pandas as pd

    csv_path = tmp_path / "earnings_calendar.csv"
    parquet_path = tmp_path / "earnings_calendar.parquet"
    frame = pd.DataFrame(
        [
            {
                "act_symbol": "AIR",
                "date": "2026-09-22",
                "timing": "unknown",
                "fiscal_year": 2027,
                "fiscal_q": "Q1",
                "eps_actual": None,
                "eps_estimate": 1.0,
                "revenue_actual": None,
                "revenue_estimate": None,
                "source": "dolthub",
            }
        ]
    )
    frame.to_csv(csv_path, index=False)
    frame.to_parquet(parquet_path, index=False)

    logs = apply_override_artifacts(
        csv_path,
        parquet_path,
        [
            {
                "symbol": "AIR",
                "match": {"date": "2026-09-22"},
                "set": {"date": "2026-09-29", "timing": "amc"},
            }
        ],
    )

    csv_frame = pd.read_csv(csv_path, keep_default_na=False)
    parquet_frame = pd.read_parquet(parquet_path)

    assert csv_frame["date"].astype(str).tolist() == ["2026-09-29"]
    assert parquet_frame["date"].astype(str).str[:10].tolist() == ["2026-09-29"]
    assert csv_frame["timing"].tolist() == ["amc"]
    assert parquet_frame["timing"].tolist() == ["amc"]
    assert any("AIR" in line for line in logs)


def test_alphabet_override_corrects_reasserted_estimate_without_touching_sibling():
    overrides = json.loads((REPO_ROOT / "config/earnings_overrides.json").read_text())["overrides"]
    goog = _row("GOOG", "2026-11-04", "unknown", "2026", "Q4", "dolthub")
    googl = {**_row("GOOGL", "2026-10-28", "amc", "2026", "Q3", "finnhub"), "eps_actual": "2.12"}
    prior = _row("GOOG", "2026-07-22", "amc", "2026", "Q2", "dolthub")
    out, _ = apply_overrides([goog, googl.copy(), prior.copy()], FIELDS, overrides)
    corrected = next(row for row in out if row["act_symbol"] == "GOOG" and row["date"] == "2026-10-28")
    assert corrected["timing"] == "amc"
    assert corrected["fiscal_q"] == "Q3"
    assert "override" in corrected["source"]
    assert not any(row["date"] == "2026-11-04" for row in out)
    assert next(row for row in out if row["act_symbol"] == "GOOGL")["eps_actual"] == googl["eps_actual"]
    assert prior in out
    assert apply_overrides(out, FIELDS, overrides)[0] == out


@pytest.mark.parametrize("symbol", ["GOOG", "GOOGL"])
def test_alphabet_verified_session_survives_provider_date_correction(symbol):
    overrides = json.loads((REPO_ROOT / "config/earnings_overrides.json").read_text())["overrides"]
    row = {**_row(symbol, "2026-10-28", "unknown", "2026", "Q4", "finnhub+reconciled"),
           "eps_actual": "2.12", "eps_estimate": "3.1057"}
    out, _ = apply_overrides([row], [*FIELDS, "eps_actual", "eps_estimate"], overrides)
    assert out[0]["date"] == "2026-10-28"
    assert out[0]["timing"] == "amc"
    assert out[0]["fiscal_q"] == "Q3"
    assert out[0]["eps_actual"] == "2.12"
    assert out[0]["eps_estimate"] == "3.1057"


@pytest.mark.parametrize("reported_first", [True, False])
def test_date_correction_preserves_already_reported_target_actuals(tmp_path, reported_first):
    import pandas as pd

    actual = {**_row("GOOG", "2026-10-28", "unknown", "2026", "Q4", "fmp"),
              "eps_actual": 2.12, "revenue_actual": 127577000000.0}
    estimate = _row("GOOG", "2026-11-04", "unknown", "2026", "Q4", "dolthub")
    frame = pd.DataFrame([actual, estimate] if reported_first else [estimate, actual])
    csv_path, parquet_path = tmp_path / "calendar.csv", tmp_path / "calendar.parquet"
    frame.to_csv(csv_path, index=False)
    frame.to_parquet(parquet_path, index=False)
    overrides = json.loads((REPO_ROOT / "config/earnings_overrides.json").read_text())["overrides"]
    apply_override_artifacts(csv_path, parquet_path, overrides)
    corrected = pd.read_parquet(parquet_path)
    assert len(corrected) == 1
    assert str(corrected.iloc[0]["date"]) == "2026-10-28"
    assert corrected.iloc[0]["eps_actual"] == 2.12
    assert corrected.iloc[0]["revenue_actual"] == 127577000000.0
    assert corrected.iloc[0]["timing"] == "amc"
    assert corrected.iloc[0]["fiscal_q"] == "Q3"
    assert "override" in corrected.iloc[0]["source"]
