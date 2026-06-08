from datetime import datetime
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))

from workers.quote_worker import (  # noqa: E402
    CHECKPOINT_CURSOR_SCRIPT,
    FLUSH_QUOTES_SCRIPT,
    LEASE_KEY,
    LEASE_PROTOCOL_KEY,
    LEASE_PROTOCOL_TTL_S,
    RENEW_LEASE_SCRIPT,
    PREVIOUS_CLOSE_CACHE_MAX_AGE_S,
    QuoteWorker,
    QuoteWorkerState,
    WorkerConfig,
    WRITE_QUOTE_SCRIPT,
    cached_previous_close,
    is_quote_window,
    monday_iso_for,
    normalize_symbol,
    reset_previous_close_session,
    score_week_events,
)


ET = ZoneInfo("America/New_York")


def test_normalize_symbol_rejects_bad_values():
    assert normalize_symbol(" crm ") == "CRM"
    assert normalize_symbol("BRK.B") == "BRK.B"
    assert normalize_symbol("bad/value") is None
    assert normalize_symbol("") is None


def test_quote_window_respects_weekends_and_holidays():
    assert is_quote_window(
        datetime(2026, 5, 26, 10, 0, tzinfo=ET),
        holidays=set(),
    )
    assert not is_quote_window(
        datetime(2026, 5, 26, 8, 0, tzinfo=ET),
        holidays=set(),
    )
    assert not is_quote_window(
        datetime(2026, 5, 30, 10, 0, tzinfo=ET),
        holidays=set(),
    )
    assert not is_quote_window(
        datetime(2026, 5, 25, 10, 0, tzinfo=ET),
        holidays={"2026-05-25"},
    )


def test_monday_iso_for_week_dates():
    assert monday_iso_for("2026-05-26") == "2026-05-25"
    assert monday_iso_for("2026-05-31") == "2026-05-25"


def test_score_week_events_prioritizes_today_and_tomorrow():
    scores: dict[str, float] = {}
    score_week_events(
        scores,
        [
            {"ticker": "CRM", "earnings_date": "2026-05-26"},
            {"ticker": "NVDA", "earnings_date": "2026-05-27"},
            {"ticker": "AAPL", "earnings_date": "2026-05-29"},
        ],
        today_iso="2026-05-26",
        weight=55,
    )
    assert scores["CRM"] > scores["NVDA"] > scores["AAPL"]


def test_cached_previous_close_only_uses_recent_rest_quotes():
    now_ms = 1_800_000
    assert (
        cached_previous_close(
            '{"at": 1200000, "transport": "rest", "tick": {"previousClose": 295.19}}',
            now_ms=now_ms,
            session_date="2026-05-26",
        )
        == 295.19
    )
    assert (
        cached_previous_close(
            '{"at": 1200000, "transport": "rest", "sessionDate": "2026-05-22", "tick": {"previousClose": 252.8}}',
            now_ms=now_ms,
            session_date="2026-05-26",
        )
        is None
    )
    assert (
        cached_previous_close(
            '{"at": 1200000, "transport": "websocket", "tick": {"previousClose": 252.8}}',
            now_ms=now_ms,
            session_date="2026-05-26",
        )
        is None
    )
    stale_ms = now_ms - (PREVIOUS_CLOSE_CACHE_MAX_AGE_S + 1) * 1000
    assert (
        cached_previous_close(
            f'{{"at": {stale_ms}, "transport": "rest", "tick": {{"previousClose": 295.19}}}}',
            now_ms=now_ms,
            session_date="2026-05-26",
        )
        is None
    )


def test_previous_close_session_reset_clears_stale_values():
    state = QuoteWorkerState(
        previous_close={"DELL": 252.8},
        previous_close_session_date="2026-05-22",
        missing_previous_close_cursor=4,
    )

    assert reset_previous_close_session(state, "2026-05-26")
    assert state.previous_close == {}
    assert state.previous_close_session_date == "2026-05-26"
    assert state.missing_previous_close_cursor == 0

    state.previous_close["DELL"] = 295.19
    assert not reset_previous_close_session(state, "2026-05-26")
    assert state.previous_close == {"DELL": 295.19}


class _FakeRedis:
    def __init__(self):
        self.set_calls = []
        self.eval_calls = []
        self.mset_calls = []
        self.fail_mset = False

    async def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))
        return True

    async def get(self, _key):
        return "7"

    async def eval(self, *args):
        self.eval_calls.append(args)
        if args[0] == FLUSH_QUOTES_SCRIPT:
            if self.fail_mset:
                raise RuntimeError("temporary Redis failure")
            numkeys = args[1]
            keys = args[3 : 2 + numkeys]
            values = args[3 + numkeys :]
            self.mset_calls.append(dict(zip(keys, values, strict=True)))
            return numkeys - 1
        return 1

    async def mset(self, values):
        self.mset_calls.append(dict(values))
        if self.fail_mset:
            raise RuntimeError("temporary Redis failure")
        return True


def _worker(*, batch_writes=False) -> QuoteWorker:
    worker = QuoteWorker(
        WorkerConfig(
            finnhub_api_key="test",
            redis_url="redis://localhost:6379",
            database_url=None,
            batch_writes=batch_writes,
        )
    )
    worker.redis = _FakeRedis()
    return worker


async def _close_test_worker(worker: QuoteWorker) -> None:
    await worker.http.aclose()


@pytest.mark.asyncio
async def test_worker_acquires_single_writer_lease_and_protocol_marker():
    worker = _worker()
    try:
        assert await worker.acquire_lease()
        assert worker.lease_event.is_set()
        assert worker.redis.set_calls[0][0] == (LEASE_KEY, worker.lease_owner)
        assert worker.redis.set_calls[0][1]["nx"] is True
        assert worker.redis.set_calls[1][0] == (LEASE_PROTOCOL_KEY, "1")
    finally:
        await _close_test_worker(worker)


@pytest.mark.asyncio
async def test_worker_registers_protocol_without_taking_market_lease():
    worker = _worker()
    try:
        assert await worker.register_lease_protocol()
        assert worker.redis.set_calls == [
            (
                (LEASE_PROTOCOL_KEY, "1"),
                {"ex": LEASE_PROTOCOL_TTL_S},
            )
        ]
        assert not worker.lease_event.is_set()
    finally:
        await _close_test_worker(worker)


@pytest.mark.asyncio
async def test_worker_renews_lease_and_protocol_marker_atomically():
    worker = _worker()
    worker.lease_event.set()
    try:
        assert await worker.renew_lease()
        assert worker.redis.eval_calls[-1] == (
            RENEW_LEASE_SCRIPT,
            2,
            LEASE_KEY,
            LEASE_PROTOCOL_KEY,
            worker.lease_owner,
            worker.config.lease_ttl_s,
            LEASE_PROTOCOL_TTL_S,
        )
    finally:
        await _close_test_worker(worker)


@pytest.mark.asyncio
async def test_cursor_checkpoint_requires_lease_and_is_owner_guarded():
    worker = _worker()
    try:
        worker.state.rest_cursor = 42
        worker.state.cursor_dirty = True
        assert not await worker.checkpoint_cursor(force=True)

        worker.lease_event.set()
        assert await worker.checkpoint_cursor(force=True)
        assert worker.state.cursor_dirty is False
        script, numkeys, lease_key, _cursor_key, owner, cursor = (
            worker.redis.eval_calls[-1]
        )
        assert script == CHECKPOINT_CURSOR_SCRIPT
        assert numkeys == 2
        assert lease_key == LEASE_KEY
        assert owner == worker.lease_owner
        assert cursor == 42
    finally:
        await _close_test_worker(worker)


@pytest.mark.asyncio
async def test_direct_quote_write_is_owner_guarded():
    worker = _worker()
    worker.lease_event.set()
    try:
        await worker.write_quote(
            {
                "symbol": "AAPL",
                "price": 100.0,
                "previousClose": 99.0,
                "change": 1.0,
                "changePct": 1 / 99,
            },
            transport="rest",
        )
        assert worker.redis.eval_calls[-1][0] == WRITE_QUOTE_SCRIPT
        assert worker.redis.eval_calls[-1][2:5] == (
            LEASE_KEY,
            "quote:AAPL",
            worker.lease_owner,
        )
    finally:
        await _close_test_worker(worker)


@pytest.mark.asyncio
async def test_batched_quotes_keep_latest_value_and_retry_failed_flush():
    worker = _worker(batch_writes=True)
    worker.lease_event.set()
    try:
        await worker.write_quote(
            {
                "symbol": "AAPL",
                "price": 100.0,
                "previousClose": 99.0,
                "change": 1.0,
                "changePct": 1 / 99,
            },
            transport="websocket",
        )
        await worker.write_quote(
            {
                "symbol": "AAPL",
                "price": 101.0,
                "previousClose": 99.0,
                "change": 2.0,
                "changePct": 2 / 99,
            },
            transport="websocket",
        )
        assert len(worker.pending_quotes) == 1
        assert '"price": 101.0' in worker.pending_quotes["quote:AAPL"]

        worker.redis.fail_mset = True
        assert await worker.flush_quotes() == 0
        assert "quote:AAPL" in worker.pending_quotes
        assert worker.state.redis_flush_failures == 1

        worker.redis.fail_mset = False
        assert await worker.flush_quotes() == 1
        assert worker.pending_quotes == {}
        assert worker.state.redis_flushes == 1
        assert worker.state.redis_keys_flushed == 1
    finally:
        await _close_test_worker(worker)
