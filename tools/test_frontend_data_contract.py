"""Contract tests for the static JSON consumed by the Next.js application."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from build_frontend_data import (
    WEEK_OFFSETS,
    build_dashboard_evidence,
    build_screener_payload,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIR = REPO_ROOT / "apps" / "frontend" / "public"
REQUIRED_EVENT_KEYS = {
    "ticker",
    "earnings_date",
    "timing",
    "as_of_date",
}


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_build_screener_payload_deduplicates_and_preserves_calendar_order():
    monday = date(2026, 8, 17)
    earlier = monday + timedelta(days=7 * WEEK_OFFSETS[0])
    current = monday
    duplicate = {"ticker": "ACME", "earnings_date": "2026-08-18", "source": "first"}
    payloads = {
        earlier: {"events": [duplicate]},
        current: {
            "events": [
                {**duplicate, "source": "duplicate"},
                {"ticker": "BETA", "earnings_date": "2026-08-19"},
                {"ticker": "", "earnings_date": "2026-08-20"},
            ]
        },
    }

    result = build_screener_payload(date(2026, 8, 20), monday, payloads)

    assert result["metadata"]["version"] == "v1"
    assert result["metadata"]["as_of_date"] == "2026-08-20"
    assert result["metadata"]["event_count"] == 2
    assert len(result["metadata"]["week_starts"]) == len(WEEK_OFFSETS)
    assert result["events"] == [
        duplicate,
        {"ticker": "BETA", "earnings_date": "2026-08-19"},
    ]


def test_committed_screener_matches_public_contract():
    screener = _read_json(PUBLIC_DIR / "screener.json")
    metadata = screener["metadata"]
    events = screener["events"]

    assert metadata["version"] == "v1"
    assert metadata["event_count"] == len(events)
    assert len(metadata["week_starts"]) == len(WEEK_OFFSETS)
    assert events

    identities = [(event["ticker"], event["earnings_date"]) for event in events]
    assert len(identities) == len(set(identities))
    for event in events:
        assert REQUIRED_EVENT_KEYS <= event.keys()
        assert event["ticker"] == event["ticker"].upper()


def test_week_manifest_references_valid_payloads_with_matching_counts():
    manifest = _read_json(PUBLIC_DIR / "weeks" / "manifest.json")

    assert manifest["current_week"]
    assert len(manifest["weeks"]) == len(WEEK_OFFSETS)
    assert manifest["current_week"] in {week["start"] for week in manifest["weeks"]}

    for week in manifest["weeks"]:
        payload = _read_json(PUBLIC_DIR / "weeks" / f"{week['start']}.json")
        assert payload["metadata"]["version"] == "v4_multi_week"
        assert payload["window"] == {"start": week["start"], "end": week["end"]}
        assert len(payload["events"]) == week["count"]
        for event in payload["events"]:
            assert REQUIRED_EVENT_KEYS <= event.keys()


def test_dashboard_evidence_is_one_compact_run_level_manifest():
    receipt = {
        "schema": "quantiv.evidence-receipt.v1",
        "receipt_id": "sha256:" + "a" * 64,
        "receipt_file": "forecasts_2026-08-22.aaaaaaaaaaaa.receipt.json",
        "validated_at": "2026-08-22T20:00:00+00:00",
        "quality": {"status": "passed", "issue_count": 0, "issue_codes": []},
        "horizons": [3, 7],
        "artifacts": [
            {
                "name": "model_bundle",
                "producer": "apps/ml/model_trainer.py",
                "member_count": 14,
                "bytes": 1234,
                "sha256": "b" * 64,
                "members": [{"path": "must-not-ship-to-dashboard.json"}],
            }
        ],
        "reconciliation": {
            "forecasts": {
                "rows": 12,
                "symbols": 4,
                "events": 4,
                "horizons": [3, 7],
                "data_window": {"snapshot_max": "2026-08-21"},
                "reconciliation": {
                    "duplicate_serving_keys": 0,
                    "quantile_crossings": 0,
                },
            }
        },
    }

    evidence = build_dashboard_evidence(receipt)

    assert evidence["schema"] == "quantiv.dashboard-evidence.v1"
    assert evidence["coverage"] == {
        "rows": 12,
        "symbols": 4,
        "events": 4,
        "horizons": [3, 7],
    }
    assert evidence["controls"]["evaluated"] == 2
    assert evidence["controls"]["exceptions"] == 0
    assert evidence["artifact_bundles"][0]["sha256"] == "b" * 64
    assert "members" not in evidence["artifact_bundles"][0]
