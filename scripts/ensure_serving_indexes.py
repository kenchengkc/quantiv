#!/usr/bin/env python3
"""
Ensure serving indexes exist for em_forecasts and verify them.
- Creates minimal serving DDL (if table missing)
- Ensures indexes with CREATE INDEX IF NOT EXISTS
- Prints verification from pg_indexes
"""
import os
from pathlib import Path
import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv

# Load repo-level .env.local
repo_root = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=repo_root / ".env.local")

PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB", "quantiv_options")
PG_USER = os.getenv("POSTGRES_USER", "quantiv_user")
PG_PW = os.getenv("POSTGRES_PASSWORD", "quantiv_secure_2024")

DDL = """
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

CREATE INDEX IF NOT EXISTS idx_em_forecasts_lookup 
    ON em_forecasts (underlying, exp_date, horizon);
CREATE INDEX IF NOT EXISTS idx_em_forecasts_recent 
    ON em_forecasts (quote_ts DESC);
CREATE INDEX IF NOT EXISTS idx_em_forecasts_symbol_recent 
    ON em_forecasts (underlying, quote_ts DESC);
"""

VERIFY_SQL = """
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'em_forecasts'
ORDER BY indexname;
"""

def get_conn():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PW,
    )


def main():
    print(f"[Indexes] Connecting to {PG_USER}@{PG_HOST}:{PG_PORT}/{PG_DB} ...")
    with get_conn() as conn, conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute(DDL)
        conn.commit()
        cur.execute(VERIFY_SQL)
        rows = cur.fetchall()
        print(f"[Indexes] Found {len(rows)} index(es) on em_forecasts:")
        for r in rows:
            print(f" - {r['indexname']}: {r['indexdef']}")
    print("[Indexes] Done.")


if __name__ == "__main__":
    main()
