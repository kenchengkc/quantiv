# scripts/postgres_to_parquet.py
import os
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds

sys.stdout.reconfigure(line_buffering=True)
print("[bootstrap] starting…", flush=True)

from dotenv import load_dotenv
env_candidates = [Path("config/.env.local"), Path("../config/.env.local"), Path(".env")]
loaded_path = None
for p in env_candidates:
    if p.exists():
        load_dotenv(dotenv_path=p, override=False)
        loaded_path = p
        break
# Fallback: also load without explicit path to pick up plain .env if present
if loaded_path is None:
    load_dotenv(override=False)
    print("[env] no explicit .env found, tried: config/.env.local, ../config/.env.local, .env", flush=True)
else:
    print(f"[env] loaded {loaded_path}", flush=True)

DB_SCHEMA   = (os.environ.get("POSTGRES_SCHEMA") or "public").strip()
TABLE_NAME  = (os.environ.get("POSTGRES_TABLE") or os.environ.get("TABLE_NAME") or "options_chain").strip()

# Parquet output root (default to data/parquet at repo root)
PARQUET_ROOT = (os.environ.get("PARQUET_ROOT") or "data/parquet").strip()
CHUNK_SIZE   = int(os.environ.get("CHUNK_SIZE", "200000"))

ROW_LIMIT    = os.environ.get("ROW_LIMIT")
ROW_LIMIT    = ROW_LIMIT if (ROW_LIMIT and ROW_LIMIT.isdigit()) else None

# Optional filters
WHERE_CLAUSE   = (os.environ.get("WHERE_CLAUSE") or "").strip()
FILTER_TICKERS = [t.strip() for t in (os.environ.get("FILTER_TICKERS","")).split(",") if t.strip()]

# Timestamp column (quoted to avoid keyword issues)
TS_COLUMN = (os.environ.get("TS_COLUMN") or '"date"').strip()

# Likely ticker column names (for optional filtering)
TICKER_CANDIDATES = [c.strip() for c in (os.environ.get("TICKER_COLUMNS") or "act_symbol,underlying_symbol,symbol").split(",")]

# ---- Partitioning config (no symbol-first) ----
# Default: Hive-style year/month directories (year=YYYY/month=MM/)
PARTITION_COLS = [c.strip() for c in (os.environ.get("PARTITION_COLS", "year,month").split(",")) if c.strip()]
PARTITION_HIVE = (os.environ.get("PARTITION_HIVE", "true").lower() == "true")

# -------- DB URL & SSL handling --------
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url, URL

db_url_env = (os.environ.get("DATABASE_URL") or "").strip()
ssl_env = (os.environ.get("POSTGRES_SSLMODE") or "").strip().lower()
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

def finalized_url():
    if db_url_env:
        url = make_url(db_url_env)
        host = (url.host or "").lower()
        if "sslmode" in url.query:
            return url
        if ssl_env:
            if ssl_env == "require" and host in LOCAL_HOSTS:
                print("[warn] POSTGRES_SSLMODE=require on localhost; forcing sslmode=disable", flush=True)
                return url.set(query={"sslmode": "disable"})
            return url.set(query={"sslmode": ssl_env})
        if host in LOCAL_HOSTS:
            return url.set(query={"sslmode": "disable"})
        return url
    else:
        DB_HOST = (os.environ.get("POSTGRES_HOST") or "127.0.0.1").strip()
        DB_PORT = int(os.environ.get("POSTGRES_PORT") or "5432")
        DB_USER = (os.environ.get("POSTGRES_USER") or "").strip()
        DB_PASS = (os.environ.get("POSTGRES_PASSWORD") or "").strip()
        DB_NAME = (os.environ.get("POSTGRES_DB") or "").strip()

        q = {}
        if ssl_env:
            if ssl_env == "require" and DB_HOST in LOCAL_HOSTS:
                print("[warn] POSTGRES_SSLMODE=require on localhost; forcing sslmode=disable", flush=True)
                q["sslmode"] = "disable"
            else:
                q["sslmode"] = ssl_env
        elif DB_HOST in LOCAL_HOSTS:
            q["sslmode"] = "disable"

        return URL.create(
            "postgresql+psycopg2",
            username=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            query=q,
        )

url = finalized_url()
print(f"[conn] host={url.host} port={url.port} db={url.database} user={url.username}", flush=True)

engine = create_engine(url, pool_pre_ping=True)

# -------- paths --------
ROOT = Path(__file__).resolve().parent.parent if "__file__" in globals() else Path.cwd()
OUTPUT_DIR = (ROOT / PARQUET_ROOT).resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# -------- helpers --------
def resolve_table_and_columns(conn):
    reg = conn.execute(text("SELECT to_regclass(:r)"), {"r": f"{DB_SCHEMA}.{TABLE_NAME}"}).scalar()
    if not reg:
        raise SystemExit(f"[schema] Table {DB_SCHEMA}.{TABLE_NAME} not found. Check POSTGRES_SCHEMA/POSTGRES_TABLE.")

    cols = conn.execute(text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = :s AND table_name = :t
    """), {"s": DB_SCHEMA, "t": TABLE_NAME}).fetchall()
    colnames = {c[0] for c in cols}

    ts_unquoted = TS_COLUMN.strip('"')
    if ts_unquoted not in colnames:
        raise SystemExit(f"[schema] Timestamp column {TS_COLUMN!r} not in {DB_SCHEMA}.{TABLE_NAME}. "
                         f"Available: {sorted(colnames)}")

    ticker_col = None
    for cand in TICKER_CANDIDATES:
        if cand in colnames:
            ticker_col = cand
            break
    return ticker_col, colnames

def build_query(fq_table: str, ticker_col: str | None):
    where_parts = []
    params = {}

    if WHERE_CLAUSE.upper().startswith("WHERE"):
        where_parts.append(WHERE_CLAUSE)

    if FILTER_TICKERS and ticker_col:
        where_parts.append(f"{'AND' if where_parts else 'WHERE'} {ticker_col} = ANY(:tickers)")
        params["tickers"] = FILTER_TICKERS
    elif FILTER_TICKERS and not ticker_col:
        print("[warn] FILTER_TICKERS set but no ticker column found; ignoring.", flush=True)

    where_sql = " ".join(where_parts)
    limit_sql = f"LIMIT {ROW_LIMIT}" if ROW_LIMIT else ""

    query = f"""
        SELECT
            *,
            EXTRACT(YEAR  FROM {TS_COLUMN})::INT AS year,
            EXTRACT(MONTH FROM {TS_COLUMN})::INT AS month
        FROM {fq_table}
        {where_sql}
        {limit_sql}
    """
    return text(query), params

# -------- build Partitioning object once (no symbol-first) --------
def make_partitioning():
    if PARTITION_HIVE:
        fields = []
        for c in PARTITION_COLS:
            if c == "year":
                fields.append(pa.field("year", pa.int16()))
            elif c in ("month", "day"):
                fields.append(pa.field(c, pa.int8()))
            else:
                fields.append(pa.field(c, pa.string()))
        return ds.partitioning(schema=pa.schema(fields), flavor="hive")
    else:
        return PARTITION_COLS

# -------- main --------
def main():
    print("[main] connecting…", flush=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("[main] connection OK", flush=True)

    with engine.connect() as conn:
        total = conn.execute(text(f'SELECT COUNT(*) FROM {DB_SCHEMA}."{TABLE_NAME}"')).scalar()
        print(f"[preflight] {DB_SCHEMA}.{TABLE_NAME} rows: {total:,}", flush=True)
        sample = conn.execute(text(f'SELECT * FROM {DB_SCHEMA}."{TABLE_NAME}" LIMIT 3')).fetchall()
        print(f"[preflight] sample rows fetched: {len(sample)}", flush=True)
        if total == 0:
            raise SystemExit("[preflight] Table has 0 rows; nothing to migrate.")

        ticker_col, _ = resolve_table_and_columns(conn)
        fq_table = f'{DB_SCHEMA}."{TABLE_NAME}"'
        query, params = build_query(fq_table, ticker_col)

    print(f"[read] source={fq_table} -> {OUTPUT_DIR}", flush=True)
    if WHERE_CLAUSE:
        print(f"[read] where={WHERE_CLAUSE}", flush=True)
    if FILTER_TICKERS and ticker_col:
        print(f"[read] filter tickers via {ticker_col}={FILTER_TICKERS}", flush=True)
    if ROW_LIMIT:
        print(f"[read] row limit={ROW_LIMIT}", flush=True)

    import pandas as pd

    total_rows = 0
    part_counts: dict[int, int] = {}
    partitioning = make_partitioning()
    print(f"[write] partitioning = {'hive ' if PARTITION_HIVE else ''}{PARTITION_COLS}", flush=True)

    # stream results to keep memory stable
    with engine.connect().execution_options(stream_results=True) as conn:
        chunk_iter = pd.read_sql_query(
            sql=query,
            con=conn,
            params=params,
            chunksize=CHUNK_SIZE,
        )

        for i, df in enumerate(chunk_iter, start=1):
            if df.empty:
                print(f"[chunk {i}] empty, skipping", flush=True)
                continue

            ts_unquoted = TS_COLUMN.strip('"')
            if ts_unquoted in df.columns and not pd.api.types.is_datetime64_any_dtype(df[ts_unquoted]):
                df[ts_unquoted] = pd.to_datetime(df[ts_unquoted], utc=True, errors="coerce")

            tbl = pa.Table.from_pandas(df, preserve_index=False)

            # Build Parquet write options (portable across PyArrow versions)
            fmt = ds.ParquetFileFormat()
            file_options = fmt.make_write_options(
                compression=os.environ.get("PARQUET_COMPRESSION", "zstd"),
                use_dictionary=True,
                write_statistics=True,
            )

            ds.write_dataset(
                data=tbl,
                base_dir=str(OUTPUT_DIR),
                format=fmt,                    # pass the format object (not "parquet")
                file_options=file_options,     # <- use file_options instead of format_options
                partitioning=partitioning,     # your hive ['year','month'] from make_partitioning()
                existing_data_behavior="delete_matching",
                max_rows_per_group=int(os.environ.get("ROW_GROUP_ROWS", "500000")),
            )


            total_rows += len(df)
            if "year" in df.columns:
                for y, c in df["year"].value_counts(dropna=False).items():
                    try:
                        y_int = int(y)
                    except Exception:
                        continue
                    part_counts[y_int] = part_counts.get(y_int, 0) + int(c)

            print(f"[chunk {i}] wrote {len(df):,} rows (total {total_rows:,})", flush=True)

    print(f"[done] rows written: {total_rows:,}", flush=True)
    print(f"[done] dataset root: {OUTPUT_DIR}", flush=True)
    if part_counts:
        print(f"[done] sample year counts: {sorted(part_counts.items())[:8]}", flush=True)
    print("[main] done", flush=True)

if __name__ == "__main__":
    try:
        print("[main] enter", flush=True)
        main()
        print("[main] done", flush=True)
    except Exception as e:
        import traceback
        print("[FATAL] exception:", flush=True)
        traceback.print_exc()
        raise
