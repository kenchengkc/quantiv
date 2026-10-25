"""Spend limited announcement requests across unresolved exact-date events."""

from datetime import date, datetime, timezone
import json

import pytest

import calendar_announcement_state as state
import runtime_state


TODAY = date(2026, 10, 25)
PROVIDER = "twelvedata_press_releases"


def attempted(previous, symbol, *, day="2026-11-02", hour=7, provider=PROVIDER):
    return state.update_state(
        previous, provider=provider, symbol=symbol, event_date=day,
        observed_at=datetime(2026, 10, 25, hour, tzinfo=timezone.utc), status="ok",
    )


def test_saved_attempts_rotate_next_budget_to_untried_events(tmp_path):
    history = attempted(attempted({}, "FIRST"), "SECOND")
    path = tmp_path / "calendar_announcement_state.json"
    state.write_state(path, history, today=TODAY)
    restored = state.load_state(path, today=TODAY)

    ordered = state.prioritize_symbols(
        ["FIRST", "SECOND", "THIRD", "FOURTH"], state=restored,
        provider=PROVIDER,
        candidate_dates={name: "2026-11-02" for name in ["FIRST", "SECOND", "THIRD", "FOURTH"]},
    )
    assert ordered == ["THIRD", "FOURTH", "FIRST", "SECOND"]


def test_oldest_attempt_precedes_recent_attempt_and_equal_times_keep_original_rank():
    history = attempted(attempted(attempted({}, "FIRST", hour=8), "SECOND"), "THIRD")
    assert state.prioritize_symbols(
        ["FIRST", "THIRD", "SECOND"], state=history, provider=PROVIDER,
        candidate_dates=dict.fromkeys(["FIRST", "SECOND", "THIRD"], "2026-11-02"),
    ) == ["THIRD", "SECOND", "FIRST"]


def test_changed_event_date_and_other_provider_do_not_inherit_attempt_penalty():
    history = attempted(attempted(attempted({}, "FIRST"), "SECOND"), "THIRD",
                        provider="alphavantage_news")
    assert state.prioritize_symbols(
        ["FIRST", "SECOND", "THIRD"], state=history, provider=PROVIDER,
        candidate_dates={"FIRST": "2026-11-03", "SECOND": "2026-11-02", "THIRD": "2026-11-02"},
    ) == ["FIRST", "THIRD", "SECOND"]


@pytest.mark.parametrize("status", ["blocked", "rate_limited"])
def test_public_release_failures_rotate_without_retaining_response_content(tmp_path, status):
    history = state.update_state(
        {}, provider="public_press_releases", symbol="FIRST",
        event_date="2026-11-02", observed_at="2026-10-25T07:00:00Z", status=status,
    )
    path = tmp_path / "state.json"
    state.write_state(path, history, today=TODAY)
    restored = state.load_state(path, today=TODAY)
    assert state.prioritize_symbols(
        ["FIRST", "SECOND"], state=restored, provider="public_press_releases",
        candidate_dates={"FIRST": "2026-11-02", "SECOND": "2026-11-02"},
    ) == ["SECOND", "FIRST"]
    assert next(iter(restored.values())) == {
        "observed_at": "2026-10-25T07:00:00+00:00", "status": status,
    }


@pytest.mark.parametrize("contents", ["not json", "[]", '{"schema":"old","attempts":{}}'])
def test_unreadable_state_does_not_block_fresh_lookups(tmp_path, contents):
    path = tmp_path / "state.json"
    path.write_text(contents)
    with pytest.warns(RuntimeWarning):
        assert state.load_state(path, today=TODAY) == {}


def test_missing_state_bootstraps_without_failure(tmp_path):
    assert state.load_state(tmp_path / "missing.json", today=TODAY) == {}


def test_persistence_discards_past_events_and_stale_attempts_without_storing_extra_data(tmp_path):
    history = attempted({}, "CURRENT")
    history = attempted(history, "PAST", day="2026-10-24")
    history = attempted(history, "FAR", day="2027-10-25")
    history = state.update_state(
        history, provider=PROVIDER, symbol="STALE", event_date="2026-11-02",
        observed_at=datetime(2026, 6, 1, tzinfo=timezone.utc), status="error",
    )
    key = f"{PROVIDER}|CURRENT|2026-11-02"
    history[key]["article_body"] = "must not survive"
    path = tmp_path / "state.json"
    state.write_state(path, history, today=TODAY)

    assert state.load_state(path, today=TODAY) == {
        key: {"observed_at": "2026-10-25T07:00:00+00:00", "status": "ok"},
    }
    assert "article_body" not in path.read_text()


def test_invalid_record_cannot_hide_an_untried_event(tmp_path):
    path = tmp_path / "state.json"
    state.write_state(path, attempted({}, "VALID"), today=TODAY)
    payload = json.loads(path.read_text())
    payload["attempts"][f"{PROVIDER}|BAD|2026-11-02"] = {
        "observed_at": "2099-01-01T00:00:00+00:00", "status": "ok",
    }
    payload["attempts"][f"{PROVIDER}|BROKEN|2026-11-02"] = {
        "observed_at": "yesterday", "status": "ok",
    }
    path.write_text(json.dumps(payload))
    with pytest.warns(RuntimeWarning):
        restored = state.load_state(path, today=TODAY)
    assert state.prioritize_symbols(
        ["VALID", "BAD", "BROKEN"], state=restored, provider=PROVIDER,
        candidate_dates=dict.fromkeys(["VALID", "BAD", "BROKEN"], "2026-11-02"),
    ) == ["BAD", "BROKEN", "VALID"]


def test_failed_atomic_replace_keeps_previous_attempt_history(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    state.write_state(path, attempted({}, "FIRST"), today=TODAY)
    original = path.read_bytes()

    def fail_replace(*args):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(state.os, "replace", fail_replace)
    with pytest.raises(OSError):
        state.write_state(path, attempted({}, "SECOND"), today=TODAY)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_runtime_release_restores_attempt_history_for_next_runner(tmp_path):
    source = tmp_path / "source"
    history = attempted({}, "FIRST")
    state.write_state(source / "calendar_announcement_state.json", history, today=TODAY)
    runtime_state.build_release(source, tmp_path / "release")
    runtime_state.materialize_release(tmp_path / "target", tmp_path / "release")
    restored = state.load_state(tmp_path / "target/calendar_announcement_state.json", today=TODAY)
    assert restored == history
