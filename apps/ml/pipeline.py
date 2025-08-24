#!/usr/bin/env python3
"""
Quantiv ML Pipeline (skeleton)
- Reads available underlyings from Parquet root
- Inserts placeholder forecasts into Postgres em_forecasts for API smoke tests
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta, date
import glob
import psycopg2
import psycopg2.extras as extras
from dotenv import load_dotenv

# Robust dotenv loading: try common repo and container locations; otherwise rely on injected env vars
def _load_env() -> None:
    env_name = (os.getenv("NODE_ENV") or os.getenv("ENVIRONMENT") or "development").lower()
    env_file = ".env.production" if env_name == "production" else ".env.local"

    candidates = []
    try:
        here = Path(__file__).resolve()
        # If running from monorepo layout (apps/ml/pipeline.py), parents[2] is repo root
        if len(here.parents) >= 3:
            candidates.append(here.parents[2] / "config" / env_file)
        if len(here.parents) >= 2:
            candidates.append(here.parents[1] / "config" / env_file)
    except Exception:
        pass

    # CWD and container-friendly fallbacks
    candidates.extend([
        Path.cwd() / "config" / env_file,
        Path("/app") / "config" / env_file,
    ])

    for p in candidates:
        try:
            if p.exists():
                load_dotenv(dotenv_path=p)
                print(f"[ML] Loaded env from {p}")
                return
        except Exception:
            continue
    print("[ML] No .env file found; relying on environment variables")

_load_env()

DB_URL = os.getenv("DATABASE_URL")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "quantiv_options")
POSTGRES_USER = os.getenv("POSTGRES_USER", "quantiv_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "quantiv_secure_2024")
PARQUET_ROOT = Path(os.getenv("PARQUET_ROOT", "/app/data/parquet"))


def discover_symbols(parquet_root: Path, limit: int = 5):
    base = parquet_root / "options_chains"
    if not base.exists():
        return []
    syms = []
    for p in base.glob("underlying=*/*/*"):
        try:
            underlying = p.parts[p.parts.index("underlying=") if "underlying=" in p.parts else -1]
        except ValueError:
            # Fallback: parse from path string
            s = str(p)
            idx = s.find("underlying=")
            if idx >= 0:
                underlying = s[idx:].split("/")[0]
            else:
                continue
        if underlying.startswith("underlying="):
            sym = underlying.split("=", 1)[1]
            if sym not in syms:
                syms.append(sym)
        if len(syms) >= limit:
            break
    return syms


def get_conn():
    if DB_URL:
        return psycopg2.connect(DB_URL)
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )


def ensure_table_exists(conn):
    # Minimal serving-compatible DDL (avoids schema mismatch across variants)
    ddl = """
    CREATE TABLE IF NOT EXISTS em_forecasts (
        underlying TEXT NOT NULL,
        quote_ts TIMESTAMPTZ NOT NULL,
        exp_date DATE NOT NULL,
        horizon TEXT NOT NULL,
        em_baseline DOUBLE PRECISION,
        band68_low DOUBLE PRECISION,
        band68_high DOUBLE PRECISION,
        band95_low DOUBLE PRECISION,
        band95_high DOUBLE PRECISION,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (underlying, quote_ts, exp_date, horizon)
    );
    CREATE INDEX IF NOT EXISTS idx_em_forecasts_lookup ON em_forecasts (underlying, exp_date, horizon);
    CREATE INDEX IF NOT EXISTS idx_em_forecasts_recent ON em_forecasts (quote_ts DESC);
    """
    with conn, conn.cursor() as cur:
        cur.execute(ddl)


def insert_placeholder_forecasts(conn, symbols):
    now = datetime.now(datetime.UTC)
    exp = date.today() + timedelta(days=7)
    rows = []
    for sym in symbols:
        for horizon in ["to_exp", "1d", "5d"]:
            em_baseline = 0.02
            rows.append(
                (
                    sym,
                    now,
                    exp,
                    horizon,
                    em_baseline,
                    0.015,
                    0.025,
                    0.010,
                    0.030,
                )
            )
    sql = """
    INSERT INTO em_forecasts (
        underlying, quote_ts, exp_date, horizon,
        em_baseline, band68_low, band68_high, band95_low, band95_high
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (underlying, quote_ts, exp_date, horizon) DO UPDATE
    SET em_baseline = EXCLUDED.em_baseline,
        band68_low = EXCLUDED.band68_low,
        band68_high = EXCLUDED.band68_high,
        band95_low = EXCLUDED.band95_low,
        band95_high = EXCLUDED.band95_high
    """
    with conn, conn.cursor() as cur:
        extras.execute_batch(cur, sql, rows, page_size=100)


def main():
    print("[ML] Starting pipeline skeleton...")
    symbols = discover_symbols(PARQUET_ROOT, limit=5)
    if not symbols:
        symbols = ["AAPL", "MSFT"]  # fallback
    print(f"[ML] Using symbols: {symbols}")
    try:
        conn = get_conn()
    except Exception as e:
        print(f"[ML] Failed to connect to Postgres: {e}")
        sys.exit(1)
    try:
        ensure_table_exists(conn)
        insert_placeholder_forecasts(conn, symbols)
        print("[ML] Inserted placeholder forecasts.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
