from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import apply_ticker_lifecycle as lifecycle  # noqa: E402

apply_lifecycle = lifecycle.apply_lifecycle


def test_psky_history_survives_in_both_artifacts_without_acquirer_history_mix(tmp_path):
    frame = pd.DataFrame([
        {"act_symbol": "PSKY", "date": "2026-08-04", "eps_actual": 0.42, "source": "dolthub"},
        {"act_symbol": "PSKY", "date": "2026-11-03", "eps_actual": 0.0, "source": "prior"},
        {"act_symbol": "SKYD", "date": "2026-11-03", "eps_actual": 0.5, "source": "fresh"},
        *[{"act_symbol": symbol, "date": "2026-08-04", "eps_actual": 99.0, "source": "dolthub"}
          for symbol in ("QRVO", "BLFS", "WBD")],
        *[{"act_symbol": symbol, "date": "2026-08-04", "eps_actual": 1.0, "source": "dolthub"}
          for symbol in ("SWKS", "RGEN")],
    ])
    csv_path = tmp_path / "earnings_calendar.csv"
    parquet_path = tmp_path / "earnings_calendar.parquet"
    frame.to_csv(csv_path, index=False)
    frame.to_parquet(parquet_path, index=False)

    counts = lifecycle.apply_artifacts(tmp_path)

    expected = pd.DataFrame([
        {"act_symbol": "RGEN", "date": "2026-08-04", "eps_actual": 1.0, "source": "dolthub"},
        {"act_symbol": "SKYD", "date": "2026-08-04", "eps_actual": 0.42, "source": "dolthub"},
        {"act_symbol": "SWKS", "date": "2026-08-04", "eps_actual": 1.0, "source": "dolthub"},
        {"act_symbol": "SKYD", "date": "2026-11-03", "eps_actual": 0.5, "source": "fresh"},
    ])
    pd.testing.assert_frame_equal(pd.read_csv(csv_path), expected)
    pd.testing.assert_frame_equal(pd.read_parquet(parquet_path), expected)
    assert counts == {kind: {"renamed_rows": 2, "retired_rows": 3, "deduplicated_rows": 1}
                      for kind in ("csv", "parquet")}


def test_artifact_mismatch_fails_before_either_file_is_written(tmp_path):
    frame = pd.DataFrame([{"act_symbol": "SKYD", "date": "2026-08-04"}])
    csv_path = tmp_path / "earnings_calendar.csv"
    parquet_path = tmp_path / "earnings_calendar.parquet"
    frame.to_csv(csv_path, index=False)
    frame.assign(date="2026-08-05").to_parquet(parquet_path, index=False)
    before = (csv_path.read_bytes(), parquet_path.read_bytes())
    with pytest.raises(ValueError, match="artifacts disagree"):
        lifecycle.apply_artifacts(tmp_path)
    assert (csv_path.read_bytes(), parquet_path.read_bytes()) == before


def test_lifecycle_rewrites_renames_removes_retired_and_deduplicates() -> None:
    frame = pd.DataFrame(
        [
            {"act_symbol": " old ", "date": "2026-09-01", "source": "prior"},
            {"act_symbol": "NEW", "date": "2026-09-01", "source": "fresh"},
            {"act_symbol": "LEG", "date": "2026-09-02", "source": "provider"},
            {"act_symbol": "KEEP", "date": "2026-09-03", "source": "provider"},
        ]
    )

    result, counts = apply_lifecycle(
        frame,
        renames={"OLD": "NEW"},
        retired=frozenset({"LEG"}),
    )

    assert result[["act_symbol", "date", "source"]].to_dict("records") == [
        {"act_symbol": "NEW", "date": "2026-09-01", "source": "fresh"},
        {"act_symbol": "KEEP", "date": "2026-09-03", "source": "provider"},
    ]
    assert counts == {
        "renamed_rows": 1,
        "retired_rows": 1,
        "deduplicated_rows": 1,
    }


def test_lifecycle_rejects_blank_symbols() -> None:
    frame = pd.DataFrame([{"act_symbol": "", "date": "2026-09-01"}])

    with pytest.raises(ValueError, match="blank ticker"):
        apply_lifecycle(frame, renames={}, retired=frozenset())


def test_artifacts_stay_aligned_after_same_run_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = pd.DataFrame(
        [
            {"act_symbol": "OLD", "date": "2026-09-01", "source": "prior"},
            {"act_symbol": "NEW", "date": "2026-09-01", "source": "fresh"},
            {"act_symbol": "LEG", "date": "2026-09-02", "source": "provider"},
        ]
    )
    frame.to_csv(tmp_path / "earnings_calendar.csv", index=False)
    frame.assign(date=pd.to_datetime(frame["date"]).dt.date).to_parquet(
        tmp_path / "earnings_calendar.parquet",
        index=False,
    )
    monkeypatch.setattr(lifecycle, "ticker_renames", lambda: {"OLD": "NEW"})
    monkeypatch.setattr(lifecycle, "delisted_tickers", lambda: frozenset({"LEG"}))

    results = lifecycle.apply_artifacts(tmp_path)

    csv_result = pd.read_csv(tmp_path / "earnings_calendar.csv")
    parquet_result = pd.read_parquet(tmp_path / "earnings_calendar.parquet")
    assert set(csv_result["act_symbol"]) == {"NEW"}
    assert set(parquet_result["act_symbol"]) == {"NEW"}
    assert lifecycle._event_keys(csv_result) == lifecycle._event_keys(parquet_result)
    assert results["csv"]["retired_rows"] == 1
    assert results["parquet"]["retired_rows"] == 1
