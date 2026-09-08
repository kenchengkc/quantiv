from datetime import date
from types import SimpleNamespace

import pytest

from workers import quote_worker
from workers.quote_worker import QuoteWorker, QuoteWorkerState, load_forecast_events


class _ForecastPool:
    def __init__(self, rows=None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        if self.error is not None:
            raise self.error
        return self.rows


@pytest.mark.asyncio
async def test_load_forecast_events_uses_bounded_neon_window():
    pool = _ForecastPool(
        [
            {"act_symbol": "CRM", "earnings_date": date(2026, 9, 8)},
            {"act_symbol": " nvda ", "earnings_date": date(2026, 9, 9)},
            {"act_symbol": "bad/value", "earnings_date": date(2026, 9, 10)},
        ]
    )

    events = await load_forecast_events(
        pool,
        date(2026, 9, 1),
        date(2026, 9, 28),
    )

    assert events == [
        {"ticker": "CRM", "earnings_date": "2026-09-08"},
        {"ticker": "NVDA", "earnings_date": "2026-09-09"},
    ]
    assert len(pool.calls) == 1
    _query, args = pool.calls[0]
    assert args == (date(2026, 9, 1), date(2026, 9, 28))


@pytest.mark.asyncio
async def test_load_forecast_events_distinguishes_disabled_from_transient_failure():
    assert await load_forecast_events(None, date(2026, 9, 1), date(2026, 9, 28)) == []

    pool = _ForecastPool(error=RuntimeError("temporary Neon failure"))
    assert await load_forecast_events(
        pool,
        date(2026, 9, 1),
        date(2026, 9, 28),
    ) is None


@pytest.mark.asyncio
async def test_forecast_universe_is_loaded_once_per_session_date(monkeypatch):
    calls = []

    async def _load(_pool, start_date, end_date):
        calls.append((start_date, end_date))
        return [{"ticker": "CRM", "earnings_date": "2026-09-08"}]

    monkeypatch.setattr(quote_worker, "load_forecast_events", _load)
    worker = SimpleNamespace(state=QuoteWorkerState(), pg_pool=object())

    first = await QuoteWorker.load_forecast_universe(worker, "2026-09-08")
    second = await QuoteWorker.load_forecast_universe(worker, "2026-09-08")

    assert first == second == [{"ticker": "CRM", "earnings_date": "2026-09-08"}]
    assert calls == [(date(2026, 9, 1), date(2026, 9, 29))]


@pytest.mark.asyncio
async def test_forecast_universe_retains_last_good_snapshot_and_retries(monkeypatch):
    responses = iter(
        [
            None,
            [{"ticker": "NVDA", "earnings_date": "2026-09-09"}],
        ]
    )
    calls = 0

    async def _load(_pool, _start_date, _end_date):
        nonlocal calls
        calls += 1
        return next(responses)

    monkeypatch.setattr(quote_worker, "load_forecast_events", _load)
    worker = SimpleNamespace(
        state=QuoteWorkerState(
            forecast_events=[{"ticker": "CRM", "earnings_date": "2026-09-08"}],
            forecast_events_session_date="2026-09-07",
        ),
        pg_pool=object(),
    )

    stale = await QuoteWorker.load_forecast_universe(worker, "2026-09-08")
    refreshed = await QuoteWorker.load_forecast_universe(worker, "2026-09-08")

    assert stale == [{"ticker": "CRM", "earnings_date": "2026-09-08"}]
    assert refreshed == [{"ticker": "NVDA", "earnings_date": "2026-09-09"}]
    assert worker.state.forecast_events_session_date == "2026-09-08"
    assert calls == 2
