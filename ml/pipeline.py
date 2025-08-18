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
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )


def ensure_table_exists(conn):
    # Align with scripts/create-em-schema.sql
    ddl = """
    CREATE TABLE IF NOT EXISTS em_forecasts (
        underlying TEXT NOT NULL,
        quote_ts TIMESTAMPTZ NOT NULL,
        exp_date DATE NOT NULL,
        horizon TEXT NOT NULL,
        em_baseline DOUBLE PRECISION,
        em_calibrated DOUBLE PRECISION,
        em_quantile DOUBLE PRECISION,
        band68_low DOUBLE PRECISION,
        band68_high DOUBLE PRECISION,
        band95_low DOUBLE PRECISION,
        band95_high DOUBLE PRECISION,
        model_version TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (underlying, quote_ts, exp_date, horizon)
    );
    CREATE INDEX IF NOT EXISTS idx_em_forecasts_lookup ON em_forecasts (underlying, exp_date, horizon);
    CREATE INDEX IF NOT EXISTS idx_em_forecasts_recent ON em_forecasts (quote_ts DESC);
    """
    with conn, conn.cursor() as cur:
        cur.execute(ddl)


def insert_placeholder_forecasts(conn, symbols):
    now = datetime.utcnow()
    exp = date.today() + timedelta(days=7)
    rows = []
    for sym in symbols:
        for horizon in ["to_exp", "1d", "5d"]:
            em_baseline = 0.02
            em_calibrated = 0.02
            em_quantile = 0.02
            rows.append(
                (
                    sym,
                    now,
                    exp,
                    horizon,
                    em_baseline,
                    em_calibrated,
                    em_quantile,
                    0.015,
                    0.025,
                    0.010,
                    0.030,
                    "demo-0.1",
                )
            )
    sql = """
    INSERT INTO em_forecasts (
        underlying, quote_ts, exp_date, horizon,
        em_baseline, em_calibrated, em_quantile,
        band68_low, band68_high, band95_low, band95_high,
        model_version
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (underlying, quote_ts, exp_date, horizon) DO UPDATE
    SET em_baseline = EXCLUDED.em_baseline,
        em_calibrated = EXCLUDED.em_calibrated,
        em_quantile = EXCLUDED.em_quantile,
        band68_low = EXCLUDED.band68_low,
        band68_high = EXCLUDED.band68_high,
        band95_low = EXCLUDED.band95_low,
        band95_high = EXCLUDED.band95_high,
        model_version = EXCLUDED.model_version
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
