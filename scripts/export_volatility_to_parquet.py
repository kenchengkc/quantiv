import os
import argparse
from pathlib import Path
import duckdb
from dotenv import load_dotenv

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OUT_ROOT = DATA_DIR / "volatility"
DUCKDB_PATH = DATA_DIR / "quantiv.duckdb"


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Export volatility_history to yearly Parquet")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing Parquet files")
    parser.add_argument("--update-views", action="store_true", help="Create/replace DuckDB views over Parquet to query without Postgres")
    parser.add_argument("--verify", action="store_true", help="Verify row counts by reading Parquet back")
    args = parser.parse_args()

    pg_dsn = (
        f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
        f"user={os.getenv('POSTGRES_USER', 'quantiv_user')} "
        f"password={os.getenv('POSTGRES_PASSWORD', 'quantiv_secure_2024')} "
        f"port={os.getenv('POSTGRES_PORT', '5432')} "
        f"{'sslmode=' + os.getenv('POSTGRES_SSLMODE') + ' ' if os.getenv('POSTGRES_SSLMODE') else ''}"
        f"dbname={os.getenv('POSTGRES_DB', 'quantiv_options')}"
    )

    con = duckdb.connect(str(DUCKDB_PATH))
    con.execute("INSTALL postgres; LOAD postgres;")
    con.execute(f"ATTACH '{pg_dsn}' AS pg (TYPE POSTGRES);")

    years = [r[0] for r in con.execute(
        """
        SELECT DISTINCT CAST(EXTRACT(YEAR FROM date) AS INT) AS y
        FROM pg.public.volatility_history
        ORDER BY 1
        """
    ).fetchall()]

    total_written = 0
    for y in years:
        out_path = OUT_ROOT / f"year={y:04d}" / f"volatility_{y:04d}.parquet"
        if out_path.exists() and not args.overwrite:
            print(f"Skip existing {out_path}")
            continue
        ensure_parent(out_path)

        print(f"Exporting year {y} -> {out_path}")
        con.execute(
            f"""
            COPY (
                SELECT id, date, symbol, iv, hv, iv_rank, iv_percentile, created_at
                FROM pg.public.volatility_history
                WHERE date >= MAKE_DATE({y}, 1, 1) AND date < MAKE_DATE({y} + 1, 1, 1)
                ORDER BY date, symbol
            ) TO '{out_path}' (FORMAT 'parquet', COMPRESSION 'zstd');
            """
        )

        cnt = con.execute(
            "SELECT COUNT(*) FROM pg.public.volatility_history WHERE date >= MAKE_DATE(?,1,1) AND date < MAKE_DATE(?+1,1,1)",
            [y, y],
        ).fetchone()[0]
        total_written += cnt
        print(f"  Wrote {cnt:,} rows")
        if args.verify:
            pcnt = con.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(out_path)]).fetchone()[0]
            print(f"  Verified Parquet rows: {pcnt:,}" + ("  [MISMATCH]" if pcnt != cnt else ""))

    print(f"Done. Total rows exported: {total_written:,}")
    if args.update_views:
        con.execute("CREATE SCHEMA IF NOT EXISTS parquet_data;")
        con.execute(f"CREATE OR REPLACE VIEW parquet_data.volatility_history AS SELECT * FROM read_parquet('{OUT_ROOT}/year=*/volatility_*.parquet');")
    con.close()


if __name__ == "__main__":
    main()
