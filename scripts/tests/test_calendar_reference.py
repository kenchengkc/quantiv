from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from scripts.calendar_reference import (
    build_release,
    load_events,
    market_today,
    project_public,
    verify_release,
)


def _write_calendar(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "act_symbol",
        "date",
        "timing",
        "fiscal_year",
        "fiscal_q",
        "eps_actual",
        "eps_estimate",
        "revenue_actual",
        "revenue_estimate",
        "source",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _row(symbol: str, event_date: date, timing: str = "amc", *, actual: str = "") -> dict[str, str]:
    return {
        "act_symbol": symbol,
        "date": event_date.isoformat(),
        "timing": timing,
        "fiscal_year": str(event_date.year),
        "fiscal_q": "Q3",
        "eps_actual": actual,
        "eps_estimate": "1.0",
        "revenue_actual": "",
        "revenue_estimate": "100",
        "source": "dolthub+finnhub",
    }


def _fixture(tmp_path: Path, today: date) -> dict[str, Path]:
    monday = today - timedelta(days=today.weekday())
    calendar = tmp_path / "data" / "earnings_calendar.csv"
    baseline = tmp_path / "data" / "validation" / "earnings_calendar_baseline.csv"
    rows = [
        _row("AAA", monday + timedelta(days=1), "Before market open"),
        _row("BBB", monday + timedelta(days=2), "unknown"),
        _row("BBB", monday + timedelta(days=4), "after_market_close"),
        _row("CCC", monday + timedelta(days=3), "amc", actual="2.0"),
        _row("CCC", monday + timedelta(days=10), "amc"),
        _row("ZZZ", monday + timedelta(days=2), "amc"),
    ]
    _write_calendar(calendar, rows)
    _write_calendar(baseline, rows)

    symbols = tmp_path / "apps" / "frontend" / "public" / "symbols"
    symbols.mkdir(parents=True)
    for symbol in ("AAA", "BBB", "CCC"):
        (symbols / f"{symbol}.json").write_text("{}\n")

    metadata = tmp_path / "data" / "dolthub_earnings_metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "synced_at": "2026-09-09T11:00:00Z",
                "rows": len(rows),
                "symbols": 4,
                "date_min": min(row["date"] for row in rows),
                "date_max": max(row["date"] for row in rows),
            }
        )
    )
    integrity = tmp_path / "fake_integrity.py"
    integrity.write_text("print('calendar integrity passed')\n")
    return {
        "calendar": calendar,
        "baseline": baseline,
        "symbols": symbols,
        "metadata": metadata,
        "integrity": integrity,
        "output": tmp_path / "data" / "calendar_reference",
    }


def _build(paths: dict[str, Path], today: date) -> tuple[dict, dict, dict]:
    return build_release(
        calendar=paths["calendar"],
        baseline=paths["baseline"],
        symbols_dir=paths["symbols"],
        output_dir=paths["output"],
        source_revision="abc123",
        not_before=datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc),
        today=today,
        integrity_script=paths["integrity"],
        repo_root=paths["calendar"].parents[1],
        source_metadata=(("dolthub", paths["metadata"]),),
    )


def test_reference_collapses_revisions_using_existing_rule(tmp_path: Path) -> None:
    today = date(2026, 9, 9)
    paths = _fixture(tmp_path, today)
    monday = today - timedelta(days=today.weekday())
    events, dropped = load_events(
        paths["calendar"], {"AAA", "BBB", "CCC"},
        monday - timedelta(days=7), monday + timedelta(days=18),
    )
    by_ticker = {event["ticker"]: event for event in events}
    assert by_ticker["AAA"]["timing"] == "bmo"
    assert by_ticker["BBB"]["earnings_date"] == (monday + timedelta(days=4)).isoformat()
    assert by_ticker["BBB"]["timing"] == "amc"
    assert by_ticker["CCC"]["earnings_date"] == (monday + timedelta(days=3)).isoformat()
    assert len(dropped) == 2


def test_build_is_content_addressed_and_excludes_outside_universe(tmp_path: Path) -> None:
    today = date(2026, 9, 9)
    paths = _fixture(tmp_path, today)
    kwargs = dict(
        calendar=paths["calendar"], baseline=paths["baseline"], symbols_dir=paths["symbols"],
        output_dir=paths["output"], source_revision="abc123",
        not_before=datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc), today=today,
        integrity_script=paths["integrity"], repo_root=tmp_path,
        source_metadata=(("dolthub", paths["metadata"]),),
    )
    first_release, first_receipt, _ = build_release(**kwargs)
    second_release, second_receipt, _ = build_release(**kwargs)
    assert first_release == second_release
    assert first_receipt == second_receipt
    assert {event["ticker"] for event in first_release["events"]} == {"AAA", "BBB", "CCC"}
    assert first_release["release_id"] == second_release["release_id"]
    assert first_receipt["receipt_id"] == second_receipt["receipt_id"]
    assert first_receipt["integrity"]["status"] == "passed"
    verify_release(paths["output"])


def test_current_run_dolthub_evidence_is_required(tmp_path: Path) -> None:
    today = date(2026, 9, 9)
    paths = _fixture(tmp_path, today)
    with pytest.raises(RuntimeError, match="current-run DoltHub"):
        build_release(
            calendar=paths["calendar"], baseline=paths["baseline"], symbols_dir=paths["symbols"],
            output_dir=paths["output"], source_revision="abc123",
            not_before=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc), today=today,
            integrity_script=paths["integrity"], repo_root=tmp_path,
            source_metadata=(("dolthub", paths["metadata"]),),
        )


def test_tampered_release_is_rejected(tmp_path: Path) -> None:
    today = date(2026, 9, 9)
    paths = _fixture(tmp_path, today)
    release, _, pointer = _build(paths, today)
    release_path = paths["output"] / pointer["release"]
    payload = json.loads(release_path.read_text())
    payload["events"][0]["earnings_date"] = "2026-09-30"
    release_path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="identity mismatch"):
        verify_release(paths["output"])
    assert release["release_id"] == pointer["release_id"]


def test_public_projection_carries_observation_receipt(tmp_path: Path) -> None:
    today = date(2026, 9, 9)
    paths = _fixture(tmp_path, today)
    release, receipt, _ = _build(paths, today)
    public = tmp_path / "public" / "calendar-reference.json"
    public_receipt = tmp_path / "public" / "evidence" / "calendar-reference-receipt.json"
    projection = project_public(paths["output"], public, public_receipt)
    assert projection["release_id"] == release["release_id"]
    assert projection["receipt_id"] == receipt["receipt_id"]
    assert projection["observed_at"] == "2026-09-09T11:00:00Z"
    assert json.loads(public_receipt.read_text())["receipt_id"] == receipt["receipt_id"]


def test_default_calendar_date_uses_new_york_not_runner_utc() -> None:
    assert market_today(datetime(2026, 9, 10, 2, 30, tzinfo=timezone.utc)) == date(2026, 9, 9)
    assert market_today(datetime(2026, 9, 10, 5, 30, tzinfo=timezone.utc)) == date(2026, 9, 10)


def test_calendar_projection_does_not_mutate_retained_research_bytes(tmp_path: Path) -> None:
    today = date(2026, 9, 9)
    paths = _fixture(tmp_path, today)
    _build(paths, today)
    public = tmp_path / "apps" / "frontend" / "public"
    retained = {
        public / "weekly.json": b'{"research":"weekly","value":1}\n',
        public / "weeks" / "2026-09-07.json": b'{"research":"week","value":2}\n',
        public / "screener.json": b'{"research":"screener","value":3}\n',
        public / "symbols" / "AAA.json": b'{"research":"symbol","value":4}\n',
        public / "evidence" / "forecast.json": b'{"research":"receipt","value":5}\n',
    }
    for path, payload in retained.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    before = {path: path.read_bytes() for path in retained}
    project_public(
        paths["output"], public / "calendar-reference.json",
        public / "evidence" / "calendar-reference-receipt.json",
    )
    assert {path: path.read_bytes() for path in retained} == before
