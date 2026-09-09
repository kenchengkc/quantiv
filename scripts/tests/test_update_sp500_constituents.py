from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

import pytest

from scripts import update_sp500_constituents as sp500


def make_csv(rows: list[dict[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(
        out,
        fieldnames=[
            "Symbol",
            "Security",
            "GICS Sector",
            "GICS Sub-Industry",
            "Date added",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def valid_rows(count: int = sp500.MIN_CONSTITUENT_ROWS) -> list[dict[str, str]]:
    return [
        {
            "Symbol": f"T{i:03d}",
            "Security": f"Company {i}",
            "GICS Sector": "Industrials",
            "GICS Sub-Industry": "Industrial Conglomerates",
            "Date added": "2020-01-01",
        }
        for i in range(count)
    ]


def test_parse_snapshot_normalizes_and_preserves_contract() -> None:
    rows = valid_rows()
    rows[0] = {
        "Symbol": "brk.b",
        "Security": "Berkshire Hathaway",
        "GICS Sector": "Financials",
        "GICS Sub-Industry": "Multi-Sector Holdings",
        "Date added": "2010-02-16",
    }

    parsed = sp500.parse_snapshot(make_csv(rows), as_of=date(2026, 9, 9))

    assert parsed[0] == {
        "symbol": "BRK.B",
        "name": "Berkshire Hathaway",
        "sector": "Financials",
        "industry": "Multi-Sector Holdings",
    }
    assert len(parsed) == sp500.MIN_CONSTITUENT_ROWS


def test_parse_snapshot_rejects_duplicate_symbols() -> None:
    rows = valid_rows()
    rows[1]["Symbol"] = rows[0]["Symbol"]

    with pytest.raises(sp500.SnapshotValidationError, match="duplicate constituent symbol"):
        sp500.parse_snapshot(make_csv(rows), as_of=date(2026, 9, 9))


def test_parse_snapshot_rejects_unknown_sector() -> None:
    rows = valid_rows()
    rows[0]["GICS Sector"] = "Crypto"

    with pytest.raises(sp500.SnapshotValidationError, match="unknown GICS sector"):
        sp500.parse_snapshot(make_csv(rows), as_of=date(2026, 9, 9))


def test_parse_snapshot_rejects_future_effective_membership() -> None:
    rows = valid_rows()
    rows[0]["Symbol"] = "FUTR"
    rows[0]["Date added"] = "2026-09-21"

    with pytest.raises(sp500.SnapshotValidationError, match="future effective date"):
        sp500.parse_snapshot(make_csv(rows), as_of=date(2026, 9, 9))


def test_parse_snapshot_rejects_implausible_row_count() -> None:
    rows = valid_rows(sp500.MIN_CONSTITUENT_ROWS - 1)

    with pytest.raises(sp500.SnapshotValidationError, match="unexpected S&P 500 constituent row count"):
        sp500.parse_snapshot(make_csv(rows), as_of=date(2026, 9, 9))


def test_large_membership_churn_fails_closed() -> None:
    old = [
        {
            "symbol": f"O{i:03d}",
            "name": f"Old {i}",
            "sector": "Industrials",
            "industry": "Industrial Conglomerates",
        }
        for i in range(sp500.MIN_CONSTITUENT_ROWS)
    ]
    new = [
        {
            "symbol": f"N{i:03d}",
            "name": f"New {i}",
            "sector": "Industrials",
            "industry": "Industrial Conglomerates",
        }
        for i in range(sp500.MIN_CONSTITUENT_ROWS)
    ]

    with pytest.raises(sp500.SnapshotValidationError, match="membership churn exceeds safety bound"):
        sp500.validate_churn(old, new)


def test_summary_reports_membership_and_reference_changes() -> None:
    old = [
        {
            "symbol": "AAA",
            "name": "Alpha",
            "sector": "Industrials",
            "industry": "Industrial Conglomerates",
        },
        {
            "symbol": "BBB",
            "name": "Beta",
            "sector": "Industrials",
            "industry": "Industrial Conglomerates",
        },
    ]
    new = [
        {
            "symbol": "AAA",
            "name": "Alpha Inc.",
            "sector": "Industrials",
            "industry": "Industrial Conglomerates",
        },
        {
            "symbol": "CCC",
            "name": "Gamma",
            "sector": "Financials",
            "industry": "Consumer Finance",
        },
    ]

    summary = sp500.render_summary(old, new)

    assert "`CCC` — Gamma" in summary
    assert "`BBB` — Beta" in summary
    assert "`AAA` — name" in summary


def test_main_writes_deterministic_json_and_summary(tmp_path: Path) -> None:
    source = tmp_path / "constituents.csv"
    output = tmp_path / "sp500.json"
    summary = tmp_path / "summary.md"
    source.write_text(make_csv(valid_rows()), encoding="utf-8")

    assert sp500.main(
        [
            "--source-file",
            str(source),
            "--output",
            str(output),
            "--summary-file",
            str(summary),
        ]
    ) == 0

    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert len(parsed) == sp500.MIN_CONSTITUENT_ROWS
    assert output.read_text(encoding="utf-8").endswith("\n")
    assert "New rows" in summary.read_text(encoding="utf-8")
