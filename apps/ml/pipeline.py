#!/usr/bin/env python3
"""
Quantiv ML Pipeline (skeleton)
- Reads available underlyings from Parquet root
- Inserts placeholder forecasts into Postgres em_forecasts for API smoke tests
- Writes/upsers same forecasts to Parquet at DATA_DIR/forecasts/em_forecasts.parquet for DuckDB backend
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, date, timezone
import psycopg2
import psycopg2.extras as extras
from dotenv import load_dotenv
import pandas as pd


# Robust dotenv loading: try common repo and container locations; otherwise rely on injected env vars
def _load_env() -> None:
    env_name = (
        os.getenv("NODE_ENV") or os.getenv("ENVIRONMENT") or "development"
    ).lower()
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
    candidates.extend(
        [
            Path.cwd() / "config" / env_file,
            Path("/app") / "config" / env_file,
        ]
    )

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
# Base data directory for forecasts parquet (matches backend DATA_DIR)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))


def discover_symbols(parquet_root: Path, limit: int = 5):
    """Discover symbols from hive partitions like underlying=SYM/quote_year=YYYY/quote_month=MM"""
    base_candidates = [parquet_root / "options_chains", parquet_root]
    base = next((b for b in base_candidates if b.exists()), None)
    if base is None:
        return []
    syms: list[str] = []
    for p in base.glob("underlying=*/*/*"):
        part = next((seg for seg in p.parts if seg.startswith("underlying=")), None)
        if not part:
            continue
        sym = part.split("=", 1)[1]
        if sym and sym not in syms:
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
    now = datetime.now(timezone.utc)
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
    return rows


def upsert_parquet_forecasts(rows, data_dir: Path):
    """Upsert rows into DATA_DIR/forecasts/em_forecasts.parquet for DuckDB consumption.
    Rows schema matches Postgres insert order.
    Key: (underlying, quote_ts, exp_date, horizon)
    """
    forecasts_dir = data_dir / "forecasts"
    forecasts_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = forecasts_dir / "em_forecasts.parquet"

    # Build DataFrame from rows
    cols = [
        "underlying",
        "quote_ts",
        "exp_date",
        "horizon",
        "em_baseline",
        "band68_low",
        "band68_high",
        "band95_low",
        "band95_high",
    ]
    df_new = pd.DataFrame(rows, columns=cols)
    df_new["created_at"] = datetime.now(timezone.utc)

    if parquet_path.exists():
        try:
            df_old = pd.read_parquet(parquet_path)
        except Exception:
            df_old = pd.DataFrame(columns=cols + ["created_at"])
        df_all = pd.concat([df_old, df_new], ignore_index=True)
        df_all.drop_duplicates(
            subset=["underlying", "quote_ts", "exp_date", "horizon"],
            keep="last",
            inplace=True,
        )
    else:
        df_all = df_new

    # Sort for stability
    df_all.sort_values(
        by=["underlying", "quote_ts", "exp_date", "horizon"], inplace=True
    )

    # Write Parquet with snappy (as per docs)
    df_all.to_parquet(parquet_path, engine="pyarrow", compression="snappy", index=False)
    print(f"[ML] Upserted {len(df_new)} rows to {parquet_path}")


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
        rows = insert_placeholder_forecasts(conn, symbols)
        print("[ML] Inserted placeholder forecasts into Postgres.")
        # Also upsert to Parquet for DuckDB backend
        upsert_parquet_forecasts(rows, DATA_DIR)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
