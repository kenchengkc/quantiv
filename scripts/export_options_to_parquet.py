import os
import argparse
from pathlib import Path
from datetime import date, timedelta
import duckdb
from dotenv import load_dotenv

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OUT_ROOT = DATA_DIR / "options"
DUCKDB_PATH = DATA_DIR / "quantiv.duckdb"


def month_bounds(y: int, m: int):
    start = date(y, m, 1)
    if m == 12:
        end = date(y + 1, 1, 1)
    else:
        end = date(y, m + 1, 1)
    return start, end


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Export options_chain to monthly Parquet")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing Parquet files")
    parser.add_argument("--update-views", action="store_true", help="Create/replace DuckDB views over Parquet to query without Postgres")
    parser.add_argument("--verify", action="store_true", help="Verify row counts by reading Parquet back")
    args = parser.parse_args()

    pg_dsn = (
        f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
        f"user={os.getenv('POSTGRES_USER', 'quantiv_user')} "
        f"password={os.getenv('POSTGRES_PASSWORD', 'quantiv_secure_2024')} "
        f"port={os.getenv('POSTGRES_PORT', '5432')} "
        f"{ 'sslmode=' + os.getenv('POSTGRES_SSLMODE') + ' ' if os.getenv('POSTGRES_SSLMODE') else '' }"
        f"dbname={os.getenv('POSTGRES_DB', 'quantiv_options')}"
    )

    con = duckdb.connect(str(DUCKDB_PATH))
    con.execute("INSTALL postgres; LOAD postgres;")
    con.execute(f"ATTACH '{pg_dsn}' AS pg (TYPE POSTGRES);")

    # discover available (year, month) pairs in Postgres
    ym = con.execute(
        """
        SELECT DISTINCT CAST(EXTRACT(YEAR FROM date) AS INT) AS y,
                        CAST(EXTRACT(MONTH FROM date) AS INT) AS m
        FROM pg.public.options_chain
        ORDER BY 1, 2
        """
    ).fetchall()

    total_written = 0
    for y, m in ym:
        start, end = month_bounds(y, m)
        out_path = OUT_ROOT / f"year={y:04d}" / f"month={m:02d}" / f"options_{y:04d}_{m:02d}.parquet"
        if out_path.exists() and not args.overwrite:
            print(f"Skip existing {out_path}")
            continue
        ensure_parent(out_path)

        print(f"Exporting {start}..{end - timedelta(days=1)} -> {out_path}")
        con.execute(
            f"""
            COPY (
                SELECT id, date, act_symbol, expiration, strike, call_put,
                       bid, ask, vol, delta, gamma, theta, vega,
                       open_interest, volume, created_at
                FROM pg.public.options_chain
                WHERE date >= DATE '{start.isoformat()}' AND date < DATE '{end.isoformat()}'
                ORDER BY date, act_symbol, expiration, strike, call_put
            ) TO '{out_path}' (FORMAT 'parquet', COMPRESSION 'zstd');
            """
        )

        # sanity count from Postgres for the slice
        cnt = con.execute(
            "SELECT COUNT(*) FROM pg.public.options_chain WHERE date >= ? AND date < ?",
            [start, end],
        ).fetchone()[0]
        total_written += cnt
        print(f"  Wrote {cnt:,} rows")
        if args.verify:
            pcnt = con.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(out_path)]).fetchone()[0]
            print(f"  Verified Parquet rows: {pcnt:,}" + ("  [MISMATCH]" if pcnt != cnt else ""))

    print(f"Done. Total rows exported: {total_written:,}")
    if args.update_views:
        con.execute("CREATE SCHEMA IF NOT EXISTS parquet_data;")
        # Enable Hive partition discovery so year/month are available as columns automatically
        con.execute(f"CREATE OR REPLACE VIEW parquet_data.options_chain AS SELECT * FROM read_parquet('{OUT_ROOT}/year=*/month=*/options_*.parquet', hive_partitioning=1);")
    con.close()


if __name__ == "__main__":
    main()
