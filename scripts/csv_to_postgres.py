#!/usr/bin/env python3
"""
CSV -> Postgres loader (idempotent, staging-safe)

Requires ../config/.env.local with at least:
  DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/quantiv_options?sslmode=disable
  CSV_FILE=../data/option_chain.csv

Optional env:
  SCHEMA=public
  STAGE_TABLE=options_chain_stage
  FINAL_TABLE=options_chain
  KEEP_STAGE=false            # keep staging rows after load (default: false)
  CREATE_UNIQUE_INDEX=false   # build unique index if missing (runs at end)
  UPSERT_MODE=keep_first      # keep_first | latest_wins
  FORCE_RELOAD=false          # ignore load_log checksum and force load
"""

import os, sys, gzip, bz2, hashlib
from pathlib import Path
from contextlib import contextmanager

# -------- immediate, line-buffered logs --------
sys.stdout.reconfigure(line_buffering=True)
log = lambda m: print(m, flush=True)

# -------- env loading --------
from dotenv import load_dotenv
for p in (Path("config/.env.local"), Path("../config/.env.local"), Path(".env")):
    if p.exists():
        load_dotenv(dotenv_path=p, override=False)
        log(f"[env] loaded {p}")
        break
else:
    load_dotenv(override=False)
    log("[env] no explicit .env file found; relying on process env")

# -------- config --------
CSV_FILE    = os.environ.get("CSV_FILE")
DATABASE_URL = os.environ.get("DATABASE_URL")
SCHEMA       = (os.environ.get("SCHEMA") or "public").strip()
STAGE_TABLE  = (os.environ.get("STAGE_TABLE") or "options_chain_stage").strip()
FINAL_TABLE  = (os.environ.get("FINAL_TABLE") or "options_chain").strip()
KEEP_STAGE   = (os.environ.get("KEEP_STAGE") or "false").lower() == "true"
CREATE_UNIQUE_INDEX_FLAG = (os.environ.get("CREATE_UNIQUE_INDEX") or "false").lower() == "true"
UPSERT_MODE  = (os.environ.get("UPSERT_MODE") or "keep_first").lower()  # keep_first | latest_wins
FORCE_RELOAD = (os.environ.get("FORCE_RELOAD") or "false").lower() == "true"

if not CSV_FILE:
    sys.exit("[error] CSV_FILE is required (set it in ../config/.env.local)")
csv_path = Path(CSV_FILE).resolve()
if not csv_path.exists():
    sys.exit(f"[error] CSV_FILE not found: {csv_path}")

if not DATABASE_URL:
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "")
    pwd  = os.environ.get("POSTGRES_PASSWORD", "")
    db   = os.environ.get("POSTGRES_DB", "")
    sslmode = os.environ.get("POSTGRES_SSLMODE") or ("disable" if host in {"127.0.0.1","localhost","::1"} else "prefer")
    DATABASE_URL = f"postgresql://{user}:{pwd}@{host}:{port}/{db}?sslmode={sslmode}"

log(f"[cfg] CSV_FILE={csv_path}")
log(f"[cfg] DB_URL host={DATABASE_URL.split('@')[-1].split('?')[0]}")
log(f"[cfg] UPSERT_MODE={UPSERT_MODE} KEEP_STAGE={KEEP_STAGE} CREATE_UNIQUE_INDEX={CREATE_UNIQUE_INDEX_FLAG} FORCE_RELOAD={FORCE_RELOAD}")

# -------- db --------
import psycopg2
import psycopg2.extras

@contextmanager
def db_conn():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()

def run(cur, sql, params=None):
    cur.execute(sql, params or ())

def fetchone(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchone()

def try_set(cur, sql):
    try:
        cur.execute(sql)
    except Exception as e:
        print(f"[tune/warn] skipped: {sql.strip()} → {e}", flush=True)

# -------- file helpers --------
def opener(path: Path):
    s = str(path)
    if s.endswith(".gz"):
        return gzip.open(s, mode="rt", encoding="utf-8", newline="")
    if s.endswith(".bz2"):
        return bz2.open(s, mode="rt", encoding="utf-8", newline="")
    return open(s, mode="rt", encoding="utf-8", newline="")

def md5_of_file(path: Path, blocksize=2**20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(blocksize), b""):
            h.update(chunk)
    return h.hexdigest()

# -------- schema --------
COLS = [
    "date","act_symbol","expiration","strike","call_put","bid","ask",
    "vol","delta","gamma","theta","vega","rho"
]

DDL_STAGE = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}."{STAGE_TABLE}" (
  {', '.join(f'"{c}" TEXT' for c in COLS)}
);
"""

DDL_FINAL_PARENT = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}."{FINAL_TABLE}" (
  "date"      DATE NOT NULL,
  act_symbol  VARCHAR(10) NOT NULL,
  expiration  DATE NOT NULL,
  strike      NUMERIC(10,2) NOT NULL,
  call_put    CHAR(1) NOT NULL,
  bid         NUMERIC(8,2),
  ask         NUMERIC(8,2),
  vol         NUMERIC(8,4),
  delta       NUMERIC(10,6),
  gamma       NUMERIC(12,8),
  theta       NUMERIC(12,8),
  vega        NUMERIC(12,8),
  rho         NUMERIC(12,8),
  created_at  TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
) PARTITION BY RANGE ("date");
"""

DDL_LOAD_LOG = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}."load_log" (
  id           BIGSERIAL PRIMARY KEY,
  file_name    TEXT NOT NULL,
  file_md5     TEXT NOT NULL,
  rows_staged  BIGINT,
  rows_inserted BIGINT,
  upsert_mode  TEXT NOT NULL,
  started_at   TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
  finished_at  TIMESTAMP WITHOUT TIME ZONE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_load_log_md5
ON {SCHEMA}."load_log"(file_md5);
"""

CALL_PUT_EXPR = """
CASE
  WHEN lower(NULLIF("call_put",'')) IN ('c','call') THEN 'C'
  WHEN lower(NULLIF("call_put",'')) IN ('p','put')  THEN 'P'
  ELSE NULL
END
"""

CAST_SELECT = f"""
SELECT
  NULLIF("date",'')::DATE                         AS "date",
  NULLIF("act_symbol",'')                         AS act_symbol,
  NULLIF("expiration",'')::DATE                   AS expiration,
  NULLIF("strike",'')::NUMERIC(10,2)              AS strike,
  {CALL_PUT_EXPR}                                 AS call_put,
  NULLIF("bid",'')::NUMERIC(8,2)                  AS bid,
  NULLIF("ask",'')::NUMERIC(8,2)                  AS ask,
  NULLIF("vol",'')::NUMERIC(8,4)                  AS vol,
  NULLIF("delta",'')::NUMERIC(10,6)               AS delta,
  NULLIF("gamma",'')::NUMERIC(12,8)               AS gamma,
  NULLIF("theta",'')::NUMERIC(12,8)               AS theta,
  NULLIF("vega",'')::NUMERIC(12,8)                AS vega,
  NULLIF("rho",'')::NUMERIC(12,8)                 AS rho
FROM {SCHEMA}."{STAGE_TABLE}"
WHERE
  NULLIF("date",'') ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'
  AND {CALL_PUT_EXPR} IS NOT NULL
"""

# Upsert variants (idempotent)
INSERT_KEEP_FIRST = f"""
INSERT INTO {SCHEMA}."{FINAL_TABLE}"
("date", act_symbol, expiration, strike, call_put, bid, ask, vol, delta, gamma, theta, vega, rho)
{CAST_SELECT}
ON CONFLICT ("date", act_symbol, expiration, strike, call_put) DO NOTHING;
"""

INSERT_LATEST_WINS = f"""
INSERT INTO {SCHEMA}."{FINAL_TABLE}"
("date", act_symbol, expiration, strike, call_put, bid, ask, vol, delta, gamma, theta, vega, rho, created_at)
SELECT "date", act_symbol, expiration, strike, call_put, bid, ask, vol, delta, gamma, theta, vega, rho, now()
FROM ({CAST_SELECT}) s
ON CONFLICT ("date", act_symbol, expiration, strike, call_put) DO UPDATE
SET bid   = EXCLUDED.bid,
    ask   = EXCLUDED.ask,
    vol   = EXCLUDED.vol,
    delta = EXCLUDED.delta,
    gamma = EXCLUDED.gamma,
    theta = EXCLUDED.theta,
    vega  = EXCLUDED.vega,
    rho   = EXCLUDED.rho,
    created_at = EXCLUDED.created_at;
"""

def partition_ddl(year: int) -> str:
    y = int(year); y1 = y + 1
    name = f'{FINAL_TABLE}_{y}'
    return f"""
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_class c
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='{SCHEMA}' AND c.relname='{name}'
  ) THEN
    EXECUTE $c$
      CREATE TABLE {SCHEMA}."{name}"
      PARTITION OF {SCHEMA}."{FINAL_TABLE}"
      FOR VALUES FROM ('{y}-01-01') TO ('{y1}-01-01');
    $c$;
  END IF;
END $$;
"""

ANALYZE_TABLES = f"""
ANALYZE {SCHEMA}."{FINAL_TABLE}";
ANALYZE {SCHEMA}."{STAGE_TABLE}";
"""

CREATE_UNIQUE_INDEX_SQL = f"""
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_{FINAL_TABLE}_key
ON {SCHEMA}."{FINAL_TABLE}"("date", act_symbol, expiration, strike, call_put);
"""

def main():
    # checksum gate
    file_md5 = md5_of_file(csv_path)
    log(f"[file] md5={file_md5}")

    log("[connect] opening DB connection…")
    with db_conn() as conn:
        cur = conn.cursor()

        # Speed knobs (session-local)
        try_set(cur, "SET lock_timeout = '10s';")
        try_set(cur, "SET statement_timeout = 0;")
        try_set(cur, "SET synchronous_commit = off;")
        try_set(cur, "SET work_mem = '128MB';")
        try_set(cur, "SET maintenance_work_mem = '1GB';")
        try_set(cur, "SET temp_buffers = '256MB';")
        try_set(cur, "SET jit = off;")

        # Ensure schemas
        log("[ddl] creating staging, final parent, and load_log…")
        run(cur, DDL_STAGE)
        run(cur, DDL_FINAL_PARENT)
        run(cur, DDL_LOAD_LOG)
        conn.commit()

        # If a unique index exists, great. If not and user asked, build it at the end.

        # Skip if we've already loaded this exact file (unless FORCE_RELOAD)
        already = fetchone(cur, f'SELECT 1 FROM {SCHEMA}."load_log" WHERE file_md5=%s', (file_md5,))
        if already and not FORCE_RELOAD:
            log("[guard] this file md5 already processed; set FORCE_RELOAD=true to override. Exiting.")
            return

        # Always start with an empty stage to avoid accumulation
        log("[stage] ensuring empty staging table…")
        run(cur, f'TRUNCATE {SCHEMA}."{STAGE_TABLE}";')
        conn.commit()

        # COPY single file into stage
        log(f"[copy] loading {csv_path.name} → {SCHEMA}.{STAGE_TABLE}")
        with opener(csv_path) as f:
            cols = ",".join(f'"{c}"' for c in COLS)
            copy_sql = f"""
COPY {SCHEMA}."{STAGE_TABLE}" ({cols})
FROM STDIN WITH (FORMAT csv, HEADER true, DELIMITER ',', QUOTE '\"', ESCAPE '\"', NULL '', ENCODING 'UTF8');
"""
            try:
                cur.copy_expert(copy_sql, f)
                conn.commit()
            except Exception as e:
                conn.rollback()
                log(f"[error] COPY failed: {e}")
                raise

        # discover years in stage for partitions
        log("[scan] discovering years in staging…")
        run(cur, f"""
            SELECT DISTINCT EXTRACT(YEAR FROM NULLIF("date",'')::DATE)::INT AS y
            FROM {SCHEMA}."{STAGE_TABLE}"
            WHERE NULLIF("date",'') ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'
            ORDER BY 1;
        """)
        years = [r[0] for r in cur.fetchall()]
        log(f"[scan] years detected: {years}")

        # ensure yearly partitions
        log("[ddl] ensuring yearly partitions exist…")
        for y in years:
            run(cur, partition_ddl(int(y)))
        conn.commit()

        # Upsert from stage -> final (idempotent)
        log(f"[insert] moving rows stage → final via UPSERT ({UPSERT_MODE})…")
        sql_upsert = INSERT_KEEP_FIRST if UPSERT_MODE == "keep_first" else INSERT_LATEST_WINS
        try:
            run(cur, sql_upsert)
            conn.commit()
        except Exception as e:
            conn.rollback()
            log("[error] insert/upsert failed; check casts/values. Stage left in place.")
            log(str(e))
            raise

        # stats
        log("[analyze] updating stats…")
        run(cur, ANALYZE_TABLES)
        conn.commit()

        # Optional unique index creation (guarded)
        if CREATE_UNIQUE_INDEX_FLAG:
            log("[index] creating unique index (concurrent)…")
            # must be autocommit / outside explicit txn for CONCURRENTLY
            conn.set_session(autocommit=True)
            try:
                with conn.cursor() as c2:
                    c2.execute(CREATE_UNIQUE_INDEX_SQL)
                log("[index] done (or already existed).")
            except Exception as e:
                log(f"[index/warn] skipped: {e}")
            finally:
                conn.set_session(autocommit=False)

        # totals
        cur.execute(f'SELECT COUNT(*) FROM {SCHEMA}."{FINAL_TABLE}";')
        total = cur.fetchone()[0]
        cur.execute(f'SELECT COUNT(*) FROM {SCHEMA}."{STAGE_TABLE}";')
        staged = cur.fetchone()[0]
        log(f"[done] final row count in {SCHEMA}.{FINAL_TABLE}: {total:,}")
        log(f"[done] staging row count: {staged:,}")

        # record in load_log
        log("[log] recording successful load…")
        run(cur, f"""
            INSERT INTO {SCHEMA}."load_log"(file_name, file_md5, rows_staged, rows_inserted, upsert_mode, finished_at)
            VALUES (%s,%s,%s,%s,%s, now())
            ON CONFLICT (file_md5) DO UPDATE
            SET rows_staged = EXCLUDED.rows_staged,
                rows_inserted = EXCLUDED.rows_inserted,
                upsert_mode = EXCLUDED.upsert_mode,
                finished_at = EXCLUDED.finished_at;
        """, (csv_path.name, file_md5, staged, total, UPSERT_MODE))
        conn.commit()

        # cleanup stage unless requested to keep
        if not KEEP_STAGE:
            log("[cleanup] truncating staging table…")
            run(cur, f'TRUNCATE {SCHEMA}."{STAGE_TABLE}";')
            conn.commit()

    log("[success] CSV → Postgres load completed.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("[abort] interrupted by user")
        raise
    except Exception:
        import traceback
        log("[FATAL] exception:")
        traceback.print_exc()
        raise
