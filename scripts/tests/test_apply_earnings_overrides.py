from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from apply_earnings_overrides import apply_overrides  # noqa: E402

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
