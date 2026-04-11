"""Pluggable data backends for Quantiv API (Postgres, DuckDB, Hybrid)."""

from typing import List, Optional, Dict, Any
from datetime import date

import asyncpg
import duckdb
import structlog

logger = structlog.get_logger()


class DataBackend:
    async def get_latest_forecasts(self, symbol: str, horizons: List[str]) -> List[Dict[str, Any]]:
        raise NotImplementedError
    async def get_latest_for_symbol_exp(self, symbol: str, exp_date: date) -> Optional[Dict[str, Any]]:
        raise NotImplementedError
    async def get_history_for_symbol_exp(self, symbol: str, exp_date: date, window_days: int) -> List[Dict[str, Any]]:
        raise NotImplementedError
    async def get_expiries(self, symbol: str, days: int) -> List[date]:
        raise NotImplementedError
    async def get_symbols(self, days: int) -> List[Dict[str, Any]]:
        raise NotImplementedError
    async def get_symbol_history_all_horizons(self, symbol: str, days: int) -> List[Dict[str, Any]]:
        raise NotImplementedError
    async def health(self) -> Dict[str, str]:
        return {"database": "unknown"}


class PostgresBackend(DataBackend):
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_latest_forecasts(self, symbol: str, horizons: List[str]) -> List[Dict[str, Any]]:
        query = """
        SELECT 
            underlying,
            quote_ts,
            exp_date,
            horizon,
            em_baseline,
            band68_low,
            band68_high,
            band95_low,
            band95_high
        FROM em_forecasts
        WHERE underlying = $1 
          AND horizon = ANY($2)
          AND quote_ts >= NOW() - INTERVAL '1 day'
        ORDER BY quote_ts DESC, exp_date ASC
        LIMIT 50
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, horizons)
        return [dict(row) for row in rows]

    async def get_latest_for_symbol_exp(self, symbol: str, exp_date: date) -> Optional[Dict[str, Any]]:
        query = """
        SELECT underlying, quote_ts, exp_date, horizon,
               em_baseline, band68_low, band68_high, band95_low, band95_high
        FROM em_forecasts
        WHERE underlying = $1 AND exp_date = $2 AND horizon = 'to_exp'
        ORDER BY quote_ts DESC
        LIMIT 1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, symbol, exp_date)
        return dict(row) if row else None

    async def get_history_for_symbol_exp(self, symbol: str, exp_date: date, window_days: int) -> List[Dict[str, Any]]:
        query = """
        SELECT quote_ts, em_baseline, band68_low, band68_high, band95_low, band95_high
        FROM em_forecasts
        WHERE underlying = $1 AND exp_date = $2 AND horizon = 'to_exp'
          AND quote_ts >= NOW() - ($3::text || ' days')::interval
        ORDER BY quote_ts ASC
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, exp_date, str(window_days))
        return [dict(r) for r in rows]

    async def get_expiries(self, symbol: str, days: int) -> List[date]:
        query = """
        SELECT DISTINCT exp_date
        FROM em_forecasts
        WHERE underlying = $1
          AND exp_date >= CURRENT_DATE
          AND exp_date <= (CURRENT_DATE + ($2::text || ' days')::interval)
        ORDER BY exp_date ASC
        LIMIT 50
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, str(days))
        return [r["exp_date"] for r in rows]

    async def get_symbols(self, days: int) -> List[Dict[str, Any]]:
        query = """
        SELECT DISTINCT underlying as symbol, COUNT(*) as forecast_count
        FROM em_forecasts
        WHERE quote_ts >= NOW() - ($1::text || ' days')::interval
        GROUP BY underlying
        ORDER BY forecast_count DESC, underlying
        LIMIT 100
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, str(max(1, int(days))))
        return [{"symbol": row["symbol"], "forecast_count": row["forecast_count"]} for row in rows]

    async def get_symbol_history_all_horizons(self, symbol: str, days: int) -> List[Dict[str, Any]]:
        query = """
        SELECT 
            quote_ts,
            horizon,
            em_baseline,
            band68_low,
            band68_high
        FROM em_forecasts
        WHERE underlying = $1 
          AND quote_ts >= NOW() - ($2::text || ' days')::interval
        ORDER BY quote_ts DESC, horizon
        LIMIT 1000
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, str(max(1, int(days))))
        return [dict(row) for row in rows]

    async def health(self) -> Dict[str, str]:
        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return {"postgres": "healthy"}
        except Exception:
            return {"postgres": "unhealthy"}


class DuckDBBackend(DataBackend):
    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self.conn = conn

    def _fetch_df(self, sql: str, params: Optional[list] = None):
        if params is None:
            return self.conn.execute(sql).fetchdf()
        return self.conn.execute(sql, params).fetchdf()

    async def get_latest_forecasts(self, symbol: str, horizons: List[str]) -> List[Dict[str, Any]]:
        sql = (
            "SELECT underlying, quote_ts, exp_date, horizon, em_baseline, band68_low, band68_high, band95_low, band95_high "
            "FROM em_forecasts "
            "WHERE underlying = ? AND quote_ts >= now() - INTERVAL 1 DAY "
            "ORDER BY quote_ts DESC, exp_date ASC LIMIT 200"
        )
        df = self._fetch_df(sql, [symbol])
        if df.empty:
            return []
        if horizons:
            df = df[df["horizon"].isin(horizons)]
        return df.head(50).to_dict(orient="records")

    async def get_latest_for_symbol_exp(self, symbol: str, exp_date: date) -> Optional[Dict[str, Any]]:
        sql = (
            "SELECT underlying, quote_ts, exp_date, horizon, em_baseline, band68_low, band68_high, band95_low, band95_high "
            "FROM em_forecasts WHERE underlying = ? AND exp_date = ? AND horizon = 'to_exp' "
            "ORDER BY quote_ts DESC LIMIT 1"
        )
        df = self._fetch_df(sql, [symbol, exp_date])
        return df.to_dict(orient="records")[0] if not df.empty else None

    async def get_history_for_symbol_exp(self, symbol: str, exp_date: date, window_days: int) -> List[Dict[str, Any]]:
        sql = (
            "SELECT quote_ts, em_baseline, band68_low, band68_high, band95_low, band95_high "
            "FROM em_forecasts WHERE underlying = ? AND exp_date = ? AND horizon = 'to_exp' "
            f"AND quote_ts >= now() - INTERVAL {max(1, int(window_days))} DAY "
            "ORDER BY quote_ts ASC"
        )
        df = self._fetch_df(sql, [symbol, exp_date])
        return df.to_dict(orient="records") if not df.empty else []

    async def get_expiries(self, symbol: str, days: int) -> List[date]:
        sql = (
            "SELECT DISTINCT exp_date FROM em_forecasts WHERE underlying = ? "
            f"AND exp_date BETWEEN current_date AND current_date + INTERVAL {max(1, int(days))} DAY "
            "ORDER BY exp_date ASC LIMIT 50"
        )
        df = self._fetch_df(sql, [symbol])
        if df.empty:
            return []
        vals = df["exp_date"].tolist()
        out: List[date] = []
        for v in vals:
            if isinstance(v, date):
                out.append(v)
            else:
                try:
                    out.append(date.fromisoformat(str(v)))
                except Exception:
                    continue
        return out

    async def get_symbols(self, days: int) -> List[Dict[str, Any]]:
        sql = (
            "SELECT underlying as symbol, COUNT(*) as forecast_count FROM em_forecasts "
            f"WHERE quote_ts >= now() - INTERVAL {max(1, int(days))} DAY "
            "GROUP BY underlying ORDER BY forecast_count DESC, underlying LIMIT 100"
        )
        df = self._fetch_df(sql)
        return [] if df.empty else df.to_dict(orient="records")

    async def get_symbol_history_all_horizons(self, symbol: str, days: int) -> List[Dict[str, Any]]:
        sql = (
            "SELECT quote_ts, horizon, em_baseline, band68_low, band68_high FROM em_forecasts "
            "WHERE underlying = ? "
            f"AND quote_ts >= now() - INTERVAL {max(1, int(days))} DAY "
            "ORDER BY quote_ts DESC, horizon LIMIT 1000"
        )
        df = self._fetch_df(sql, [symbol])
        return [] if df.empty else df.to_dict(orient="records")

    async def health(self) -> Dict[str, str]:
        try:
            _ = self.conn.execute("SELECT 1").fetchone()
            return {"duckdb": "healthy"}
        except Exception:
            return {"duckdb": "unhealthy"}


class HybridBackend(DataBackend):
    def __init__(self, duck: DuckDBBackend, pg: PostgresBackend, last_days: int = 1):
        self.duck = duck
        self.pg = pg
        self.last_days = max(1, int(last_days))

    async def get_latest_forecasts(self, symbol: str, horizons: List[str]) -> List[Dict[str, Any]]:
        d = await self.duck.get_latest_forecasts(symbol, horizons)
        p = await self.pg.get_latest_forecasts(symbol, horizons)
        seen = set()
        out = []
        for rec in d + p:
            key = (rec.get("quote_ts"), rec.get("exp_date"), rec.get("horizon"))
            if key not in seen:
                seen.add(key)
                out.append(rec)
        out.sort(key=lambda r: (r.get("quote_ts"), r.get("exp_date")), reverse=True)
        return out[:50]

    async def get_latest_for_symbol_exp(self, symbol: str, exp_date: date) -> Optional[Dict[str, Any]]:
        d = await self.duck.get_latest_for_symbol_exp(symbol, exp_date)
        p = await self.pg.get_latest_for_symbol_exp(symbol, exp_date)
        if d and p:
            return d if d["quote_ts"] >= p["quote_ts"] else p
        return d or p

    async def get_history_for_symbol_exp(self, symbol: str, exp_date: date, window_days: int) -> List[Dict[str, Any]]:
        d = await self.duck.get_history_for_symbol_exp(symbol, exp_date, window_days)
        p = await self.pg.get_history_for_symbol_exp(symbol, exp_date, min(self.last_days, window_days))
        seen = set()
        out = []
        for rec in d + p:
            key = rec.get("quote_ts")
            if key not in seen:
                seen.add(key)
                out.append(rec)
        out.sort(key=lambda r: r.get("quote_ts"))
        return out

    async def get_expiries(self, symbol: str, days: int) -> List[date]:
        ds = set(await self.duck.get_expiries(symbol, days))
        ps = set(await self.pg.get_expiries(symbol, days))
        return sorted(list(ds | ps))[:50]

    async def get_symbols(self, days: int) -> List[Dict[str, Any]]:
        ds = await self.duck.get_symbols(days)
        ps = await self.pg.get_symbols(days)
        agg: Dict[str, int] = {}
        for rec in ds + ps:
            agg[rec["symbol"]] = agg.get(rec["symbol"], 0) + int(rec.get("forecast_count", 0))
        out = [{"symbol": k, "forecast_count": v} for k, v in agg.items()]
        out.sort(key=lambda r: (r["forecast_count"], r["symbol"]), reverse=True)
        return out[:100]

    async def get_symbol_history_all_horizons(self, symbol: str, days: int) -> List[Dict[str, Any]]:
        d = await self.duck.get_symbol_history_all_horizons(symbol, days)
        p = await self.pg.get_symbol_history_all_horizons(symbol, min(self.last_days, days))
        seen = set()
        out = []
        for rec in d + p:
            key = (rec.get("quote_ts"), rec.get("horizon"))
            if key not in seen:
                seen.add(key)
                out.append(rec)
        out.sort(key=lambda r: (r.get("quote_ts"), r.get("horizon")), reverse=True)
        return out[:1000]

    async def health(self) -> Dict[str, str]:
        h = {}
        h.update(await self.duck.health())
        h.update(await self.pg.health())
        return h
