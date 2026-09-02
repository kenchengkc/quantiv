from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from sync_finnhub_earnings import (
    CallBudget,
    date_chunks,
    load_frontend_symbol_universe,
    normalize_existing,
)


def test_weekly_window_is_exactly_one_finnhub_call() -> None:
    assert date_chunks(date(2026, 7, 24), date(2026, 8, 23)) == [
        (date(2026, 7, 24), date(2026, 8, 23))
    ]


def test_frontend_universe_is_canonical_and_excludes_retired(tmp_path) -> None:
    for symbol in ("AAPL", "BK", "AMWD", "BRK.B"):
        (tmp_path / f"{symbol}.json").write_text("{}")

    assert load_frontend_symbol_universe(tmp_path) == ["AAPL", "BNY", "BRK.B"]


def test_provider_boundary_rewrites_renames_and_drops_delistings() -> None:
    frame = pd.DataFrame(
        {
            "act_symbol": ["BK", "AMWD", "AAPL"],
            "date": ["2026-08-24"] * 3,
            "timing": ["amc"] * 3,
        }
    )

    normalized = normalize_existing(frame)

    assert normalized["act_symbol"].tolist() == ["BNY", "AAPL"]


def test_call_budget_exhaustion_is_detectable() -> None:
    budget = CallBudget(1)
    budget.consume()
    with pytest.raises(RuntimeError, match="budget exhausted"):
        budget.consume()
