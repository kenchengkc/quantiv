from __future__ import annotations

import json
from pathlib import Path

from scripts.options_snapshot_resilience import finalize_snapshot


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _published_release(data_dir: Path, options_date: str) -> None:
    manifest_rel = "control/releases/test-release.json"
    _write(
        data_dir / manifest_rel,
        json.dumps(
            {
                "files": [
                    {
                        "path": (
                            "parquet/options_chain/"
                            f"year={options_date[:4]}/month={options_date[5:7]}/"
                            f"{options_date}.parquet"
                        )
                    }
                ]
            }
        ),
    )
    _write(
        data_dir / "control/current_data_release.json",
        json.dumps({"manifest": manifest_rel, "release_id": "test-release"}),
    )


def _manifest(source_date: str) -> dict:
    critical_codes = ["options_stale", "option_quote_quality_below_limit"]
    return {
        "manifest_id": "sha256:test-manifest",
        "schema": "quantiv.data-reconciliation.v2",
        "generated_at": "2026-09-17T12:01:00+00:00",
        "quality": {
            "decision_safe": False,
            "critical_exceptions": len(critical_codes),
        },
        "source_reconciliation": {"source_date": source_date},
        "quote_quality": {"source_date": source_date},
        "exceptions": [
            {"code": code, "severity": "critical", "summary": code}
            for code in critical_codes
        ],
    }


def test_verified_published_fallback_allows_refresh_scoring(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    published_date = "2026-09-14"
    candidate_date = "2026-09-16"
    published = (
        data_dir
        / "parquet/options_chain/year=2026/month=09/2026-09-14.parquet"
    )
    candidate = (
        data_dir
        / "parquet/options_chain/year=2026/month=09/2026-09-16.parquet"
    )
    _write(published, "published")
    _write(candidate, "candidate")
    _published_release(data_dir, published_date)
    manifest_path = data_dir / "validation/data_reconciliation.json"
    _write(manifest_path, json.dumps(_manifest(candidate_date)))

    result = finalize_snapshot(manifest_path=manifest_path, data_dir=data_dir)

    assert result.state == "fallback"
    assert result.can_score is False
    assert result.can_refresh is True
    assert result.active_source_date == published_date
    assert not candidate.exists()
    status = json.loads(
        (data_dir / "validation/options_snapshot_status.json").read_text()
    )
    assert status["policy"]["strict_options_candidate_accepted"] is False
    assert status["policy"]["refresh_scoring_allowed"] is True
