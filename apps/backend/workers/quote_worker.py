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
import uuid
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
PREVIOUS_CLOSE_CACHE_MAX_AGE_S = 30 * 60
INTEREST_ZSET = "quote:interest"
STATUS_KEY = "quote:worker:status"
CURSOR_KEY = "quote:railway:cursor"
LEASE_KEY = "quote:regular:lease"
LEASE_PROTOCOL_KEY = "quote:regular:lease:enabled"
LEASE_PROTOCOL_TTL_S = 7 * 24 * 60 * 60
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
FINNHUB_WS_URL = "wss://ws.finnhub.io"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
ET = ZoneInfo("America/New_York")

RENEW_LEASE_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  redis.call("EXPIRE", KEYS[1], ARGV[2])
  redis.call("SET", KEYS[2], "1", "EX", ARGV[3])
  return 1
end
return 0
"""

RELEASE_LEASE_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
end
return 0
"""

WRITE_QUOTE_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  redis.call("SET", KEYS[2], ARGV[2], "EX", ARGV[3])
  return 1
end
return 0
"""

FLUSH_QUOTES_SCRIPT = """
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
  return 0
end
local values = {}
for i = 2, #KEYS do
  values[#values + 1] = KEYS[i]
  values[#values + 1] = ARGV[i]
end
redis.call("MSET", unpack(values))
return #KEYS - 1
"""

CHECKPOINT_CURSOR_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  redis.call("SET", KEYS[2], ARGV[2])
  return 1
end
return 0
"""


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
    status_refresh_s: int = 60
    cursor_checkpoint_s: int = 60
    quote_flush_s: float = 5.0
    lease_ttl_s: int = 90
    batch_writes: bool = False
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
            status_refresh_s=max(30, env_int("QUOTE_WORKER_STATUS_REFRESH_S", 60)),
            cursor_checkpoint_s=max(
                15, env_int("QUOTE_WORKER_CURSOR_CHECKPOINT_S", 60)
            ),
            quote_flush_s=max(1.0, env_float("QUOTE_WORKER_QUOTE_FLUSH_S", 5.0)),
            lease_ttl_s=max(30, env_int("QUOTE_WORKER_LEASE_TTL_S", 90)),
            batch_writes=os.getenv("QUOTE_WORKER_BATCH_WRITES", "0") == "1",
            enable_websocket=os.getenv("QUOTE_WORKER_ENABLE_WEBSOCKET", "1") != "0",
            allow_market_hours_override=os.getenv("QUOTE_WORKER_FORCE_OPEN", "0")
            == "1",
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


def is_quote_window(
    now: datetime | None = None, holidays: set[str] | None = None
) -> bool:
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
                if isinstance(row, dict) and (
                    symbol := normalize_symbol(row.get("symbol"))
                ):
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
        (datetime.fromisoformat(f"{today_iso}T00:00:00+00:00") + timedelta(days=1))
        .date()
        .isoformat()
    )
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


def cached_previous_close(
    raw: str | bytes | None,
    *,
    now_ms: int,
    session_date: str | None = None,
) -> float | None:
    if not raw:
        return None
    try:
        entry = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
    except Exception:
        return None
    if not isinstance(entry, dict) or entry.get("transport") != "rest":
        return None
    cached_session_date = entry.get("sessionDate")
    if (
        session_date
        and isinstance(cached_session_date, str)
        and cached_session_date[:10] != session_date
    ):
        return None
    at_ms = entry.get("at")
    if not isinstance(at_ms, (int, float)):
        return None
    if now_ms - int(at_ms) > PREVIOUS_CLOSE_CACHE_MAX_AGE_S * 1000:
        return None
    tick = entry.get("tick")
    if not isinstance(tick, dict):
        return None
    pc = tick.get("previousClose")
    return float(pc) if isinstance(pc, (int, float)) and pc > 0 else None


@dataclass
class QuoteWorkerState:
    ranked_symbols: list[str] = field(default_factory=list)
    websocket_symbols: set[str] = field(default_factory=set)
    previous_close: dict[str, float] = field(default_factory=dict)
    previous_close_session_date: str | None = None
    missing_previous_close_cursor: int = 0
    calls_ok: int = 0
    calls_failed: int = 0
    ws_messages: int = 0
    redis_flushes: int = 0
    redis_keys_flushed: int = 0
    redis_flush_failures: int = 0
    last_universe_refresh: float = 0.0
    last_rest_symbol: str | None = None
    last_ws_symbol: str | None = None
    rest_cursor: int | None = None
    cursor_dirty: bool = False
    last_cursor_checkpoint: float = 0.0
    status: str = "starting"


def reset_previous_close_session(state: QuoteWorkerState, session_date: str) -> bool:
    if state.previous_close_session_date == session_date:
        return False
    state.previous_close.clear()
    state.missing_previous_close_cursor = 0
    state.previous_close_session_date = session_date
    return True


class QuoteWorker:
    def __init__(self, config: WorkerConfig):
        self.config = config
        self.state = QuoteWorkerState()
        self.holidays = load_holidays()
        self.redis = redis.from_url(config.redis_url, decode_responses=True)
        self.pg_pool: asyncpg.Pool | None = None
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self.stop_event = asyncio.Event()
        self.lease_event = asyncio.Event()
        self.lease_owner = uuid.uuid4().hex
        self.lease_valid_until = 0.0
        self.pending_quotes: dict[str, str] = {}
        self.pending_quotes_lock = asyncio.Lock()

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
            batch_writes=self.config.batch_writes,
            quote_flush_s=self.config.quote_flush_s,
            holidays=len(self.holidays),
        )

    async def close(self) -> None:
        if self.lease_event.is_set():
            await self.flush_quotes()
            await self.checkpoint_cursor(force=True)
            await self.release_lease()
        await self.http.aclose()
        await self.redis.aclose()
        if self.pg_pool:
            await self.pg_pool.close()

    async def register_lease_protocol(self) -> bool:
        try:
            await self.redis.set(
                LEASE_PROTOCOL_KEY,
                "1",
                ex=LEASE_PROTOCOL_TTL_S,
            )
            return True
        except Exception as exc:
            logger.warning("quote lease protocol registration failed", error=str(exc))
            return False

    async def acquire_lease(self) -> bool:
        try:
            acquired = await self.redis.set(
                LEASE_KEY,
                self.lease_owner,
                nx=True,
                ex=self.config.lease_ttl_s,
            )
            if not acquired:
                return False
            await self.register_lease_protocol()
        except Exception as exc:
            logger.warning("quote lease acquire failed", error=str(exc))
            try:
                await self.redis.eval(
                    RELEASE_LEASE_SCRIPT,
                    1,
                    LEASE_KEY,
                    self.lease_owner,
                )
            except Exception:
                pass
            return False
        self.lease_valid_until = (
            asyncio.get_running_loop().time() + self.config.lease_ttl_s
        )
        self.lease_event.set()
        self.state.rest_cursor = None
        self.state.cursor_dirty = False
        self.state.status = "starting"
        logger.info("quote lease acquired", owner=self.lease_owner)
        return True

    async def renew_lease(self) -> bool:
        try:
            renewed = await self.redis.eval(
                RENEW_LEASE_SCRIPT,
                2,
                LEASE_KEY,
                LEASE_PROTOCOL_KEY,
                self.lease_owner,
                self.config.lease_ttl_s,
                LEASE_PROTOCOL_TTL_S,
            )
        except Exception as exc:
            logger.warning("quote lease renewal failed", error=str(exc))
            if asyncio.get_running_loop().time() >= self.lease_valid_until:
                await self.mark_lease_lost()
            return False
        if int(renewed or 0) != 1:
            await self.mark_lease_lost()
            return False
        self.lease_valid_until = (
            asyncio.get_running_loop().time() + self.config.lease_ttl_s
        )
        return True

    async def mark_lease_lost(self) -> None:
        if not self.lease_event.is_set():
            return
        self.lease_event.clear()
        self.state.status = "standby"
        self.state.rest_cursor = None
        self.state.cursor_dirty = False
        async with self.pending_quotes_lock:
            self.pending_quotes.clear()
        logger.warning("quote lease lost", owner=self.lease_owner)

    async def release_lease(self) -> None:
        self.lease_event.clear()
        async with self.pending_quotes_lock:
            self.pending_quotes.clear()
        try:
            await self.redis.eval(
                RELEASE_LEASE_SCRIPT,
                1,
                LEASE_KEY,
                self.lease_owner,
            )
        except Exception as exc:
            logger.warning("quote lease release failed", error=str(exc))

    async def lease_loop(self) -> None:
        interval = max(10.0, self.config.lease_ttl_s / 3)
        while not self.stop_event.is_set():
            quote_window_open = (
                is_quote_window(holidays=self.holidays)
                or self.config.allow_market_hours_override
            )
            if quote_window_open and self.lease_event.is_set():
                await self.renew_lease()
            elif quote_window_open:
                await self.acquire_lease()
            elif self.lease_event.is_set():
                await self.flush_quotes()
                await self.checkpoint_cursor(force=True)
                await self.release_lease()
                self.state.status = "market_closed"
            else:
                self.state.status = "market_closed"
            await asyncio.sleep(interval)

    def ensure_quote_session(self) -> None:
        _weekday, session_date, _minutes = et_parts()
        if reset_previous_close_session(self.state, session_date):
            logger.info("quote previous-close session reset", session_date=session_date)

    async def refresh_universe(self, *, force: bool = False) -> None:
        now = asyncio.get_running_loop().time()
        if (
            not force
            and now - self.state.last_universe_refresh < self.config.universe_refresh_s
        ):
            return
        weekday, today_iso, _minutes = et_parts()
        this_monday = monday_iso_for(today_iso)
        next_monday = (
            (
                datetime.fromisoformat(f"{this_monday}T00:00:00+00:00")
                + timedelta(days=7)
            )
            .date()
            .isoformat()
        )
        week_after_next = (
            (
                datetime.fromisoformat(f"{this_monday}T00:00:00+00:00")
                + timedelta(days=14)
            )
            .date()
            .isoformat()
        )
        last_monday = (
            (
                datetime.fromisoformat(f"{this_monday}T00:00:00+00:00")
                - timedelta(days=7)
            )
            .date()
            .isoformat()
        )

        scores = await load_interest_scores(self.redis)
        for symbol, score in list(scores.items()):
            scores[symbol] = min(score, 250.0)

        for symbol in await load_watchlist_symbols(self.pg_pool):
            scores[symbol] = scores.get(symbol, 0.0) + 120.0

        score_week_events(
            scores, load_week_file(this_monday), today_iso=today_iso, weight=55.0
        )
        score_week_events(
            scores, load_week_file(next_monday), today_iso=today_iso, weight=30.0
        )
        score_week_events(
            scores, load_week_file(week_after_next), today_iso=today_iso, weight=20.0
        )
        score_week_events(
            scores, load_week_file(last_monday), today_iso=today_iso, weight=15.0
        )

        for symbol in load_sp500():
            scores[symbol] = scores.get(symbol, 0.0) + 5.0

        ranked = [
            symbol
            for symbol, _score in sorted(
                scores.items(), key=lambda item: (-item[1], item[0])
            )
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
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        _weekday, session_date, _minutes = et_parts()
        for symbol, raw in zip(missing, raws, strict=False):
            pc = cached_previous_close(raw, now_ms=now_ms, session_date=session_date)
            if pc is not None:
                self.state.previous_close[symbol] = pc

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
            "redis_flushes": self.state.redis_flushes,
            "redis_keys_flushed": self.state.redis_keys_flushed,
            "redis_flush_failures": self.state.redis_flush_failures,
            "pending_quotes": len(self.pending_quotes),
            "last_rest_symbol": self.state.last_rest_symbol,
            "last_ws_symbol": self.state.last_ws_symbol,
            "rest_per_minute": self.config.rest_per_minute,
            "batch_writes": self.config.batch_writes,
            "lease_protocol": 1,
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
            "changePct": float(change_pct_raw) / 100
            if change_pct_raw is not None
            else None,
        }

    async def write_quote(self, tick: dict[str, Any], *, transport: str) -> None:
        _weekday, session_date, _minutes = et_parts()
        entry = {
            "at": int(datetime.now(timezone.utc).timestamp() * 1000),
            "tick": tick,
            "source": "finnhub",
            "session": "regular",
            "sessionDate": session_date,
            "transport": transport,
        }
        key = f"quote:{tick['symbol']}"
        encoded = json.dumps(entry)
        if not self.config.batch_writes:
            written = await self.redis.eval(
                WRITE_QUOTE_SCRIPT,
                2,
                LEASE_KEY,
                key,
                self.lease_owner,
                encoded,
                STALE_TTL_S,
            )
            if int(written or 0) != 1:
                await self.mark_lease_lost()
                raise RuntimeError("quote lease lost before write")
            return
        async with self.pending_quotes_lock:
            if not self.lease_event.is_set():
                raise RuntimeError("quote lease lost before queue")
            self.pending_quotes[key] = encoded

    async def flush_quotes(self) -> int:
        if not self.config.batch_writes or not self.lease_event.is_set():
            return 0
        async with self.pending_quotes_lock:
            if not self.pending_quotes:
                return 0
            batch = self.pending_quotes
            self.pending_quotes = {}
        try:
            # The owner check and MSET are atomic. Readers reject entries older
            # than STALE_TTL_S because MSET cannot preserve per-key expirations.
            keys = [LEASE_KEY, *batch.keys()]
            values = [self.lease_owner, *batch.values()]
            flushed = await self.redis.eval(
                FLUSH_QUOTES_SCRIPT,
                len(keys),
                *keys,
                *values,
            )
        except Exception as exc:
            async with self.pending_quotes_lock:
                for key, value in batch.items():
                    self.pending_quotes.setdefault(key, value)
            self.state.redis_flush_failures += 1
            logger.warning("quote batch flush failed", keys=len(batch), error=str(exc))
            return 0
        if int(flushed or 0) != len(batch):
            await self.mark_lease_lost()
            return 0
        self.state.redis_flushes += 1
        self.state.redis_keys_flushed += len(batch)
        return len(batch)

    async def quote_flush_loop(self) -> None:
        if not self.config.batch_writes:
            return
        while not self.stop_event.is_set():
            await asyncio.sleep(self.config.quote_flush_s)
            await self.flush_quotes()

    async def load_cursor(self, symbols: list[str]) -> int:
        if self.state.rest_cursor is not None:
            return self.state.rest_cursor % len(symbols)
        try:
            raw_cursor = await self.redis.get(CURSOR_KEY)
            cursor = int(raw_cursor) % len(symbols) if raw_cursor is not None else 0
        except Exception:
            cursor = 0
        self.state.rest_cursor = cursor
        self.state.last_cursor_checkpoint = asyncio.get_running_loop().time()
        return cursor

    async def checkpoint_cursor(self, *, force: bool = False) -> bool:
        if (
            not self.lease_event.is_set()
            or not self.state.cursor_dirty
            or self.state.rest_cursor is None
        ):
            return False
        now = asyncio.get_running_loop().time()
        if (
            not force
            and now - self.state.last_cursor_checkpoint
            < self.config.cursor_checkpoint_s
        ):
            return False
        try:
            saved = await self.redis.eval(
                CHECKPOINT_CURSOR_SCRIPT,
                2,
                LEASE_KEY,
                CURSOR_KEY,
                self.lease_owner,
                self.state.rest_cursor,
            )
        except Exception as exc:
            logger.warning("quote cursor checkpoint failed", error=str(exc))
            return False
        if int(saved or 0) != 1:
            await self.mark_lease_lost()
            return False
        self.state.cursor_dirty = False
        self.state.last_cursor_checkpoint = now
        return True

    async def rest_loop(self) -> None:
        spacing = 60.0 / self.config.rest_per_minute
        while not self.stop_event.is_set():
            if not self.lease_event.is_set():
                self.state.status = "standby"
                await asyncio.sleep(5)
                continue
            if (
                not is_quote_window(holidays=self.holidays)
                and not self.config.allow_market_hours_override
            ):
                self.state.status = "market_closed"
                await asyncio.sleep(30)
                continue

            self.ensure_quote_session()
            await self.refresh_universe()
            websocket_missing_previous_close = [
                s
                for s in self.state.ranked_symbols
                if s in self.state.websocket_symbols
                and s not in self.state.previous_close
            ]
            longtail_symbols = [
                s
                for s in self.state.ranked_symbols
                if s not in self.state.websocket_symbols
            ]
            symbols = websocket_missing_previous_close + longtail_symbols
            if not symbols:
                self.state.status = "no_rest_symbols"
                await asyncio.sleep(5)
                continue
            if websocket_missing_previous_close:
                cursor = self.state.missing_previous_close_cursor % len(
                    websocket_missing_previous_close
                )
                symbol = websocket_missing_previous_close[cursor]
                self.state.missing_previous_close_cursor = (cursor + 1) % len(
                    websocket_missing_previous_close
                )
                next_cursor = None
            else:
                self.state.missing_previous_close_cursor = 0
                cursor = await self.load_cursor(symbols)
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
            if next_cursor is not None:
                self.state.rest_cursor = next_cursor
                self.state.cursor_dirty = True
                await self.checkpoint_cursor()
            await asyncio.sleep(spacing)

    async def websocket_loop(self) -> None:
        if not self.config.enable_websocket or self.config.websocket_symbols <= 0:
            return
        while not self.stop_event.is_set():
            if not self.lease_event.is_set():
                await asyncio.sleep(5)
                continue
            if (
                not is_quote_window(holidays=self.holidays)
                and not self.config.allow_market_hours_override
            ):
                await asyncio.sleep(30)
                continue
            self.ensure_quote_session()
            await self.refresh_universe()
            symbols = set(self.state.websocket_symbols)
            if not symbols:
                await asyncio.sleep(10)
                continue
            await self.seed_previous_closes(symbols)
            url = f"{FINNHUB_WS_URL}?token={self.config.finnhub_api_key}"
            try:
                async with websockets.connect(
                    url, ping_interval=20, ping_timeout=20
                ) as ws:
                    for symbol in sorted(symbols):
                        await ws.send(
                            json.dumps({"type": "subscribe", "symbol": symbol})
                        )
                    logger.info("Finnhub websocket subscribed", symbols=len(symbols))
                    deadline = (
                        asyncio.get_running_loop().time()
                        + self.config.websocket_refresh_s
                    )
                    while (
                        asyncio.get_running_loop().time() < deadline
                        and not self.stop_event.is_set()
                        and self.lease_event.is_set()
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
                logger.warning(
                    "websocket quote write failed", symbol=symbol, error=str(exc)
                )

    async def status_loop(self) -> None:
        while not self.stop_event.is_set():
            if self.lease_event.is_set():
                await self.write_status()
            await asyncio.sleep(self.config.status_refresh_s)

    async def run(self) -> None:
        await self.start()
        await self.register_lease_protocol()
        if (
            is_quote_window(holidays=self.holidays)
            or self.config.allow_market_hours_override
        ):
            await self.acquire_lease()
        await self.refresh_universe(force=True)
        tasks = [
            asyncio.create_task(self.lease_loop(), name="lease_loop"),
            asyncio.create_task(self.rest_loop(), name="rest_loop"),
            asyncio.create_task(self.websocket_loop(), name="websocket_loop"),
            asyncio.create_task(self.status_loop(), name="status_loop"),
            asyncio.create_task(self.quote_flush_loop(), name="quote_flush_loop"),
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
