"""Railway quote worker for Finnhub regular-hours price refresh.

Runs as a separate Railway service using the backend Docker image:

    python workers/quote_worker.py

It writes the same `quote:{SYMBOL}` Redis records that the Vercel cron route
currently writes, so the frontend read path does not change. The worker uses
Finnhub WebSocket for the top interest-ranked symbols and REST /quote for the
rotating long tail.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import redis.asyncio as redis
import structlog
import websockets
from dotenv import load_dotenv

logger = structlog.get_logger()

QUOTE_REFRESH_OPEN_MIN = 9 * 60 + 25
QUOTE_REFRESH_CLOSE_MIN = 16 * 60 + 45
STALE_TTL_S = 7 * 24 * 60 * 60
INTEREST_ZSET = "quote:interest"
STATUS_KEY = "quote:worker:status"
CURSOR_KEY = "quote:railway:cursor"
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
FINNHUB_WS_URL = "wss://ws.finnhub.io"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
ET = ZoneInfo("America/New_York")


try:
    REPO_ROOT = Path(__file__).resolve().parents[3]
except IndexError:
    REPO_ROOT = Path.cwd()


def load_env() -> None:
    env_file = (
        ".env.production"
        if os.getenv("NODE_ENV") == "production"
        or os.getenv("ENVIRONMENT") == "production"
        else ".env.local"
    )
    env_path = REPO_ROOT / "config" / env_file
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class WorkerConfig:
    finnhub_api_key: str
    redis_url: str
    database_url: str | None
    rest_per_minute: int = 55
    websocket_symbols: int = 50
    universe_refresh_s: int = 300
    websocket_refresh_s: int = 120
    status_refresh_s: int = 15
    enable_websocket: bool = True
    allow_market_hours_override: bool = False

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        load_env()
        token = os.getenv("FINNHUB_API_KEY")
        redis_url = os.getenv("REDIS_URL")
        if not token:
            raise RuntimeError("FINNHUB_API_KEY is required for quote worker")
        if not redis_url:
            raise RuntimeError("REDIS_URL is required for quote worker")
        rest_per_minute = max(1, min(env_int("QUOTE_WORKER_REST_PER_MIN", 55), 60))
        websocket_symbols = max(0, min(env_int("QUOTE_WORKER_WS_SYMBOLS", 50), 50))
        return cls(
            finnhub_api_key=token,
            redis_url=redis_url,
            database_url=os.getenv("DATABASE_URL"),
            rest_per_minute=rest_per_minute,
            websocket_symbols=websocket_symbols,
            universe_refresh_s=max(30, env_int("QUOTE_WORKER_UNIVERSE_REFRESH_S", 300)),
            websocket_refresh_s=max(30, env_int("QUOTE_WORKER_WS_REFRESH_S", 120)),
            status_refresh_s=max(5, env_int("QUOTE_WORKER_STATUS_REFRESH_S", 15)),
            enable_websocket=os.getenv("QUOTE_WORKER_ENABLE_WEBSOCKET", "1") != "0",
            allow_market_hours_override=os.getenv("QUOTE_WORKER_FORCE_OPEN", "0") == "1",
        )


def normalize_symbol(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    symbol = value.strip().upper()
    return symbol if SYMBOL_RE.match(symbol) else None


def public_dir() -> Path:
    candidates = [
        REPO_ROOT / "apps" / "frontend" / "public",
        Path("/app/apps/frontend/public"),
        Path.cwd() / "public",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def holidays_path() -> Path:
    candidates = [
        REPO_ROOT / "apps" / "frontend" / "lib" / "marketHolidays.generated.ts",
        Path("/app/apps/frontend/lib/marketHolidays.generated.ts"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def load_holidays() -> set[str]:
    path = holidays_path()
    if not path.exists():
        return set()
    text = path.read_text()
    match = re.search(r"MARKET_HOLIDAYS_US\s*=\s*\[(.*?)\]\s+as const", text, re.S)
    if not match:
        return set()
    return set(re.findall(r"""["'](\d{4}-\d{2}-\d{2})["']""", match.group(1)))


def et_parts(now: datetime | None = None) -> tuple[int, str, int]:
    et_now = (now or datetime.now(ET)).astimezone(ET)
    return et_now.weekday(), et_now.date().isoformat(), et_now.hour * 60 + et_now.minute


def is_quote_window(now: datetime | None = None, holidays: set[str] | None = None) -> bool:
    weekday, iso_date, minutes = et_parts(now)
    if weekday >= 5:
        return False
    if holidays and iso_date in holidays:
        return False
    return QUOTE_REFRESH_OPEN_MIN <= minutes <= QUOTE_REFRESH_CLOSE_MIN


def monday_iso_for(date_iso: str) -> str:
    date = datetime.fromisoformat(f"{date_iso}T00:00:00+00:00")
    delta = 6 if date.weekday() == 6 else date.weekday()
    return (date - timedelta(days=delta)).date().isoformat()


def load_week_file(monday_iso: str) -> list[dict[str, Any]]:
    path = public_dir() / "weeks" / f"{monday_iso}.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return []
    events = payload.get("events") if isinstance(payload, dict) else None
    return events if isinstance(events, list) else []


def load_sp500() -> list[str]:
    candidates = [
        REPO_ROOT / "lib" / "data" / "sp500-constituents.json",
        Path("/app/lib/data/sp500-constituents.json"),
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        out: list[str] = []
        if isinstance(payload, list):
            for row in payload:
                if isinstance(row, dict) and (symbol := normalize_symbol(row.get("symbol"))):
                    out.append(symbol)
        return out
    return []


async def load_watchlist_symbols(pool: asyncpg.Pool | None) -> list[str]:
    if pool is None:
        return []
    try:
        rows = await pool.fetch("SELECT DISTINCT symbol FROM watchlist")
    except Exception as exc:
        logger.warning("watchlist load failed", error=str(exc))
        return []
    return [symbol for row in rows if (symbol := normalize_symbol(row["symbol"]))]


async def load_interest_scores(client: redis.Redis) -> dict[str, float]:
    now_score = datetime.now(timezone.utc).timestamp()
    try:
        await client.zremrangebyscore(INTEREST_ZSET, "-inf", now_score - 60)
        rows = await client.zrevrange(INTEREST_ZSET, 0, 199, withscores=True)
    except Exception as exc:
        logger.warning("interest score load failed", error=str(exc))
        return {}
    out: dict[str, float] = {}
    for member, score in rows:
        symbol = normalize_symbol(member)
        if symbol:
            # Frontend writes score = current epoch seconds + context boost.
            # Convert the remaining future offset into points so interest
            # naturally decays without a separate cleanup job.
            points = max(0.0, (float(score) - now_score) / 10.0)
            if points > 0:
                out[symbol] = points
    return out


def score_week_events(
    scores: dict[str, float],
    events: list[dict[str, Any]],
    *,
    today_iso: str,
    weight: float,
) -> None:
    tomorrow_iso = (
        datetime.fromisoformat(f"{today_iso}T00:00:00+00:00") + timedelta(days=1)
    ).date().isoformat()
    for event in events:
        if not isinstance(event, dict):
            continue
        symbol = normalize_symbol(event.get("ticker"))
        if not symbol:
            continue
        event_date = str(event.get("earnings_date") or "")[:10]
        bonus = 0.0
        if event_date == today_iso:
            bonus = 80.0
        elif event_date == tomorrow_iso:
            bonus = 60.0
        scores[symbol] = scores.get(symbol, 0.0) + weight + bonus


@dataclass
class QuoteWorkerState:
    ranked_symbols: list[str] = field(default_factory=list)
    websocket_symbols: set[str] = field(default_factory=set)
    previous_close: dict[str, float] = field(default_factory=dict)
    calls_ok: int = 0
    calls_failed: int = 0
    ws_messages: int = 0
    last_universe_refresh: float = 0.0
    last_rest_symbol: str | None = None
    last_ws_symbol: str | None = None
    status: str = "starting"


class QuoteWorker:
    def __init__(self, config: WorkerConfig):
        self.config = config
        self.state = QuoteWorkerState()
        self.holidays = load_holidays()
        self.redis = redis.from_url(config.redis_url, decode_responses=True)
        self.pg_pool: asyncpg.Pool | None = None
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self.stop_event = asyncio.Event()

    async def start(self) -> None:
        if self.config.database_url:
            self.pg_pool = await asyncpg.create_pool(
                dsn=self.config.database_url,
                min_size=1,
                max_size=2,
            )
        logger.info(
            "quote worker started",
            rest_per_minute=self.config.rest_per_minute,
            websocket_symbols=self.config.websocket_symbols,
            websocket_enabled=self.config.enable_websocket,
            holidays=len(self.holidays),
        )

    async def close(self) -> None:
        await self.http.aclose()
        await self.redis.aclose()
        if self.pg_pool:
            await self.pg_pool.close()

    async def refresh_universe(self, *, force: bool = False) -> None:
        now = asyncio.get_running_loop().time()
        if not force and now - self.state.last_universe_refresh < self.config.universe_refresh_s:
            return
        weekday, today_iso, _minutes = et_parts()
        this_monday = monday_iso_for(today_iso)
        next_monday = (
            datetime.fromisoformat(f"{this_monday}T00:00:00+00:00") + timedelta(days=7)
        ).date().isoformat()
        week_after_next = (
            datetime.fromisoformat(f"{this_monday}T00:00:00+00:00") + timedelta(days=14)
        ).date().isoformat()
        last_monday = (
            datetime.fromisoformat(f"{this_monday}T00:00:00+00:00") - timedelta(days=7)
        ).date().isoformat()

        scores = await load_interest_scores(self.redis)
        for symbol, score in list(scores.items()):
            scores[symbol] = min(score, 250.0)

        for symbol in await load_watchlist_symbols(self.pg_pool):
            scores[symbol] = scores.get(symbol, 0.0) + 120.0

        score_week_events(scores, load_week_file(this_monday), today_iso=today_iso, weight=55.0)
        score_week_events(scores, load_week_file(next_monday), today_iso=today_iso, weight=30.0)
        score_week_events(scores, load_week_file(week_after_next), today_iso=today_iso, weight=20.0)
        score_week_events(scores, load_week_file(last_monday), today_iso=today_iso, weight=15.0)

        for symbol in load_sp500():
            scores[symbol] = scores.get(symbol, 0.0) + 5.0

        ranked = [
            symbol
            for symbol, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
            if normalize_symbol(symbol)
        ]
        self.state.ranked_symbols = ranked
        if self.config.enable_websocket and self.config.websocket_symbols > 0:
            self.state.websocket_symbols = set(ranked[: self.config.websocket_symbols])
        else:
            self.state.websocket_symbols = set()
        self.state.last_universe_refresh = now
        logger.info(
            "quote universe refreshed",
            weekday=weekday,
            symbols=len(ranked),
            websocket_symbols=len(self.state.websocket_symbols),
            top=ranked[:10],
        )

    async def seed_previous_closes(self, symbols: set[str]) -> None:
        missing = [s for s in symbols if s not in self.state.previous_close]
        if not missing:
            return
        keys = [f"quote:{s}" for s in missing]
        try:
            raws = await self.redis.mget(keys)
        except Exception:
            return
        for symbol, raw in zip(missing, raws, strict=False):
            if not raw:
                continue
            try:
                entry = json.loads(raw) if isinstance(raw, str) else raw
                tick = entry.get("tick") if isinstance(entry, dict) else None
                pc = tick.get("previousClose") if isinstance(tick, dict) else None
                if isinstance(pc, (int, float)) and pc > 0:
                    self.state.previous_close[symbol] = float(pc)
            except Exception:
                continue

    async def write_status(self) -> None:
        payload = {
            "status": self.state.status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "quote_window_open": is_quote_window(holidays=self.holidays)
            or self.config.allow_market_hours_override,
            "ranked_symbols": len(self.state.ranked_symbols),
            "websocket_symbols": len(self.state.websocket_symbols),
            "calls_ok": self.state.calls_ok,
            "calls_failed": self.state.calls_failed,
            "ws_messages": self.state.ws_messages,
            "last_rest_symbol": self.state.last_rest_symbol,
            "last_ws_symbol": self.state.last_ws_symbol,
            "rest_per_minute": self.config.rest_per_minute,
        }
        try:
            await self.redis.set(STATUS_KEY, json.dumps(payload), ex=180)
        except Exception as exc:
            logger.warning("status write failed", error=str(exc))

    async def fetch_quote(self, symbol: str) -> dict[str, Any] | None:
        try:
            res = await self.http.get(
                FINNHUB_QUOTE_URL,
                params={"symbol": symbol, "token": self.config.finnhub_api_key},
            )
        except httpx.HTTPError:
            return None
        if res.status_code == 429:
            logger.warning("Finnhub REST rate limited", symbol=symbol)
            return None
        if not res.is_success:
            return None
        try:
            body = res.json()
        except ValueError:
            return None
        price = body.get("c")
        if not isinstance(price, (int, float)) or price <= 0:
            return None
        previous_close = body.get("pc")
        if isinstance(previous_close, (int, float)) and previous_close > 0:
            self.state.previous_close[symbol] = float(previous_close)
        else:
            previous_close = None
        change = body.get("d") if isinstance(body.get("d"), (int, float)) else None
        change_pct_raw = (
            body.get("dp") if isinstance(body.get("dp"), (int, float)) else None
        )
        return {
            "symbol": symbol,
            "price": float(price),
            "previousClose": float(previous_close) if previous_close else None,
            "change": float(change) if change is not None else None,
            "changePct": float(change_pct_raw) / 100 if change_pct_raw is not None else None,
        }

    async def write_quote(self, tick: dict[str, Any], *, transport: str) -> None:
        entry = {
            "at": int(datetime.now(timezone.utc).timestamp() * 1000),
            "tick": tick,
            "source": "finnhub",
            "session": "regular",
            "transport": transport,
        }
        await self.redis.set(f"quote:{tick['symbol']}", json.dumps(entry), ex=STALE_TTL_S)

    async def rest_loop(self) -> None:
        spacing = 60.0 / self.config.rest_per_minute
        while not self.stop_event.is_set():
            if not is_quote_window(holidays=self.holidays) and not self.config.allow_market_hours_override:
                self.state.status = "market_closed"
                await self.write_status()
                await asyncio.sleep(30)
                continue

            await self.refresh_universe()
            symbols = [
                s for s in self.state.ranked_symbols if s not in self.state.websocket_symbols
            ]
            if not symbols:
                self.state.status = "no_rest_symbols"
                await asyncio.sleep(5)
                continue
            try:
                raw_cursor = await self.redis.get(CURSOR_KEY)
                cursor = int(raw_cursor) % len(symbols) if raw_cursor is not None else 0
            except Exception:
                cursor = 0
            symbol = symbols[cursor]
            next_cursor = (cursor + 1) % len(symbols)

            tick = await self.fetch_quote(symbol)
            if tick:
                try:
                    await self.write_quote(tick, transport="rest")
                    self.state.calls_ok += 1
                    self.state.last_rest_symbol = symbol
                    self.state.status = "running"
                except Exception as exc:
                    self.state.calls_failed += 1
                    logger.warning("quote write failed", symbol=symbol, error=str(exc))
            else:
                self.state.calls_failed += 1
            try:
                await self.redis.set(CURSOR_KEY, next_cursor)
            except Exception:
                pass
            await asyncio.sleep(spacing)

    async def websocket_loop(self) -> None:
        if not self.config.enable_websocket or self.config.websocket_symbols <= 0:
            return
        while not self.stop_event.is_set():
            if not is_quote_window(holidays=self.holidays) and not self.config.allow_market_hours_override:
                await asyncio.sleep(30)
                continue
            await self.refresh_universe()
            symbols = set(self.state.websocket_symbols)
            if not symbols:
                await asyncio.sleep(10)
                continue
            await self.seed_previous_closes(symbols)
            url = f"{FINNHUB_WS_URL}?token={self.config.finnhub_api_key}"
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    for symbol in sorted(symbols):
                        await ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                    logger.info("Finnhub websocket subscribed", symbols=len(symbols))
                    deadline = (
                        asyncio.get_running_loop().time() + self.config.websocket_refresh_s
                    )
                    while (
                        asyncio.get_running_loop().time() < deadline
                        and not self.stop_event.is_set()
                        and (
                            is_quote_window(holidays=self.holidays)
                            or self.config.allow_market_hours_override
                        )
                    ):
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                        await self.handle_ws_message(message)
            except Exception as exc:
                logger.warning("Finnhub websocket disconnected", error=str(exc))
                await asyncio.sleep(5)

    async def handle_ws_message(self, message: str | bytes) -> None:
        try:
            payload = json.loads(message)
        except Exception:
            return
        if not isinstance(payload, dict) or payload.get("type") != "trade":
            return
        rows = payload.get("data")
        if not isinstance(rows, list):
            return
        latest: dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = normalize_symbol(row.get("s"))
            price = row.get("p")
            if symbol and isinstance(price, (int, float)) and price > 0:
                latest[symbol] = float(price)
        for symbol, price in latest.items():
            pc = self.state.previous_close.get(symbol)
            change = price - pc if pc and pc > 0 else None
            tick = {
                "symbol": symbol,
                "price": price,
                "previousClose": pc,
                "change": change,
                "changePct": change / pc if change is not None and pc else None,
            }
            try:
                await self.write_quote(tick, transport="websocket")
                self.state.ws_messages += 1
                self.state.last_ws_symbol = symbol
            except Exception as exc:
                logger.warning("websocket quote write failed", symbol=symbol, error=str(exc))

    async def status_loop(self) -> None:
        while not self.stop_event.is_set():
            await self.write_status()
            await asyncio.sleep(self.config.status_refresh_s)

    async def run(self) -> None:
        await self.start()
        await self.refresh_universe(force=True)
        tasks = [
            asyncio.create_task(self.rest_loop(), name="rest_loop"),
            asyncio.create_task(self.websocket_loop(), name="websocket_loop"),
            asyncio.create_task(self.status_loop(), name="status_loop"),
        ]
        try:
            await self.stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.close()


async def main() -> None:
    config = WorkerConfig.from_env()
    worker = QuoteWorker(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop_event.set)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
