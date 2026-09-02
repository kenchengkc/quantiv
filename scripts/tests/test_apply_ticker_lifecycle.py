from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import apply_ticker_lifecycle as lifecycle  # noqa: E402

apply_lifecycle = lifecycle.apply_lifecycle


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
