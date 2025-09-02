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
import pyarrow as pa
import pyarrow.parquet as pq
import duckdb
import joblib
from lightgbm import LGBMRegressor


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
# Baseline EM multiplier and limits
EM_ALPHA = float(os.getenv("EM_ALPHA", "1.0"))
EXPIRY_WINDOW_DAYS = int(os.getenv("EXPIRY_WINDOW_DAYS", "120"))
MAX_EXPS_PER_SYMBOL = int(os.getenv("MAX_EXPS_PER_SYMBOL", "5"))
MODEL_DIR = Path(
    os.getenv(
        "MODEL_DIR",
        str((DATA_DIR if "DATA_DIR" in os.environ else Path(".")) / "models"),
    )
)
METADATA_DIR = Path(
    os.getenv(
        "METADATA_DIR",
        str((DATA_DIR if "DATA_DIR" in os.environ else Path(".")) / "metadata"),
    )
)
TRAIN_MODELS = os.getenv("TRAIN_MODELS", "auto").lower()  # 'auto' | 'true' | 'false'
MODEL_VERSION = os.getenv("MODEL_VERSION", "v0")


def _open_duckdb():
    try:
        return duckdb.connect()
    except Exception as e:
        print(f"[ML] Failed to open DuckDB connection: {e}")
        return None


def latest_iv_from_parquet(con: "duckdb.DuckDBPyConnection", root: Path, symbol: str):
    """Get latest IV for symbol from volatility parquet. Returns float or None (fallback to 0.2)."""
    if con is None:
        return None
    try:
        vol_glob = (root / "volatility").as_posix() + "/**/*.parquet"
        today = date.today()
        sql = (
            "SELECT iv FROM read_parquet(?) WHERE symbol = ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1"
        )
        res = con.execute(sql, [vol_glob, symbol, today]).fetchone()
        if res and res[0] is not None:
            return float(res[0])
    except Exception as e:
        print(f"[ML] latest_iv_from_parquet error for {symbol}: {e}")
    return None


def expiries_from_parquet(
    con: "duckdb.DuckDBPyConnection", root: Path, symbol: str, window_days: int
):
    """Find upcoming expiries for a symbol from options parquet. Returns List[date]."""
    if con is None:
        return []
    begin = date.today()
    end = begin + timedelta(days=max(1, int(window_days)))
    try:
        options_glob = (root / "options").as_posix() + "/**/*.parquet"
        sql = (
            "SELECT DISTINCT expiration AS exp_date "
            "FROM read_parquet(?) WHERE act_symbol = ? "
            "AND expiration BETWEEN ? AND ? "
            "ORDER BY exp_date ASC LIMIT ?"
        )
        rows = con.execute(
            sql, [options_glob, symbol, begin, end, MAX_EXPS_PER_SYMBOL]
        ).fetchall()
        exps = [r[0] for r in rows if r and r[0] is not None]
        return exps
    except Exception as e:
        # If the 'options' layout isn't present, gracefully fallback
        print(f"[ML] expiries_from_parquet error for {symbol}: {e}")
        return []


def compute_baseline_forecasts(symbols):
    """Compute baseline EM forecasts using IV from volatility parquet and expiries from options parquet."""
    con = _open_duckdb()
    now_ts = datetime.now(timezone.utc)
    rows = []
    for sym in symbols:
        iv = latest_iv_from_parquet(con, PARQUET_ROOT, sym) or 0.2
        expiries = expiries_from_parquet(con, PARQUET_ROOT, sym, EXPIRY_WINDOW_DAYS)
        if not expiries:
            expiries = [date.today() + timedelta(days=7)]

        # 'to_exp' forecasts for each expiry
        for exp in expiries:
            dte = max(1, (exp - date.today()).days)
            em = EM_ALPHA * iv * (dte / 365.0) ** 0.5
            band68_low = 0.75 * em
            band68_high = 1.25 * em
            band95_low = 0.50 * em
            band95_high = 1.50 * em
            rows.append(
                (
                    sym,
                    now_ts,
                    exp,
                    "to_exp",
                    em,
                    band68_low,
                    band68_high,
                    band95_low,
                    band95_high,
                )
            )

        # Also provide short-horizon baselines keyed to nearest expiry (for charts)
        nearest_exp = expiries[0]
        for horizon, t_days in [("1d", 1), ("5d", 5)]:
            em = EM_ALPHA * iv * (t_days / 365.0) ** 0.5
            band68_low = 0.75 * em
            band68_high = 1.25 * em
            band95_low = 0.50 * em
            band95_high = 1.50 * em
            rows.append(
                (
                    sym,
                    now_ts,
                    nearest_exp,
                    horizon,
                    em,
                    band68_low,
                    band68_high,
                    band95_low,
                    band95_high,
                )
            )

    if con is not None:
        con.close()
    return rows


def _features_labels_paths():
    feats = DATA_DIR / "forecasts" / "atm_features.parquet"
    labels = DATA_DIR / "forecasts" / "em_labels.parquet"
    return feats, labels


def _detect_target_column(
    con: "duckdb.DuckDBPyConnection", labels_path: Path
) -> str | None:
    try:
        df = con.execute(
            "SELECT * FROM read_parquet(?) LIMIT 1", [str(labels_path)]
        ).fetchdf()
        candidates = [
            "realized_abs_log_return",
            "realized_move",
            "abs_log_return",
            "abs_move",
            "target",
            "em_target",
        ]
        for c in candidates:
            if c in df.columns:
                return c
    except Exception as e:
        print(f"[ML] _detect_target_column error: {e}")
    return None


def _prepare_training_frame(
    con: "duckdb.DuckDBPyConnection",
    feats_path: Path,
    labels_path: Path,
    target_col: str,
) -> pd.DataFrame:
    sql = f"""
        SELECT f.*, l.{target_col} AS target
        FROM read_parquet(?) AS f
        JOIN read_parquet(?) AS l USING (underlying, quote_ts, exp_date, horizon)
    """
    df = con.execute(sql, [str(feats_path), str(labels_path)]).fetchdf()
    if df.empty:
        return df
    # Drop obvious non-feature columns
    drop_cols = {"underlying", "quote_ts", "exp_date", "horizon", "created_at"}
    # Keep numeric features
    feature_cols = [
        c
        for c in df.columns
        if c not in drop_cols and c != "target" and pd.api.types.is_numeric_dtype(df[c])
    ]
    cols = feature_cols + ["underlying", "quote_ts", "exp_date", "horizon", "target"]
    return df[cols].copy()


def train_lightgbm_if_possible() -> bool:
    if TRAIN_MODELS not in ("auto", "true"):
        return False
    feats_path, labels_path = _features_labels_paths()
    if not feats_path.exists() or not labels_path.exists():
        print("[ML] Skipping training: features/labels parquet not found")
        return False
    con = _open_duckdb()
    if con is None:
        print("[ML] Skipping training: DuckDB not available")
        return False
    try:
        target_col = _detect_target_column(con, labels_path)
        if not target_col:
            print("[ML] Skipping training: no target column detected in labels")
            return False
        df = _prepare_training_frame(con, feats_path, labels_path, target_col)
        if df.empty:
            print("[ML] Skipping training: joined training frame is empty")
            return False

        # Time-based split
        df.sort_values("quote_ts", inplace=True)
        split_idx = int(len(df) * 0.85)
        df_train = df.iloc[:split_idx]
        df_valid = df.iloc[split_idx:]

        feature_cols = [
            c
            for c in df.columns
            if c not in ("underlying", "quote_ts", "exp_date", "horizon", "target")
        ]
        X_train, y_train = df_train[feature_cols], df_train["target"].astype(float)
        X_valid, y_valid = df_valid[feature_cols], df_valid["target"].astype(float)

        common_params = dict(
            n_estimators=600,
            learning_rate=0.05,
            num_leaves=63,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
        )

        mean_model = LGBMRegressor(objective="regression", **common_params)
        q16_model = LGBMRegressor(objective="quantile", alpha=0.16, **common_params)
        q84_model = LGBMRegressor(objective="quantile", alpha=0.84, **common_params)
        q025_model = LGBMRegressor(objective="quantile", alpha=0.025, **common_params)
        q975_model = LGBMRegressor(objective="quantile", alpha=0.975, **common_params)

        mean_model.fit(X_train, y_train, eval_set=[(X_valid, y_valid)], verbose=False)
        q16_model.fit(X_train, y_train, eval_set=[(X_valid, y_valid)], verbose=False)
        q84_model.fit(X_train, y_train, eval_set=[(X_valid, y_valid)], verbose=False)
        q025_model.fit(X_train, y_train, eval_set=[(X_valid, y_valid)], verbose=False)
        q975_model.fit(X_train, y_train, eval_set=[(X_valid, y_valid)], verbose=False)

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(mean_model, MODEL_DIR / f"em_mean_{MODEL_VERSION}.pkl")
        joblib.dump(q16_model, MODEL_DIR / f"em_q16_{MODEL_VERSION}.pkl")
        joblib.dump(q84_model, MODEL_DIR / f"em_q84_{MODEL_VERSION}.pkl")
        joblib.dump(q025_model, MODEL_DIR / f"em_q025_{MODEL_VERSION}.pkl")
        joblib.dump(q975_model, MODEL_DIR / f"em_q975_{MODEL_VERSION}.pkl")

        # Write simple metadata
        METADATA_DIR.mkdir(parents=True, exist_ok=True)
        meta = pd.DataFrame(
            [
                {
                    "model_family": "em_lightgbm",
                    "version": MODEL_VERSION,
                    "trained_at": datetime.now(timezone.utc),
                    "n_train": int(len(df_train)),
                    "n_valid": int(len(df_valid)),
                    "features": ",".join(feature_cols),
                    "target": target_col,
                }
            ]
        )
        meta.to_parquet(METADATA_DIR / "model_meta.parquet", index=False)
        print("[ML] Trained and saved LightGBM models")
        return True
    except Exception as e:
        print(f"[ML] Training error: {e}")
        return False
    finally:
        try:
            con.close()
        except Exception:
            pass


def _load_models_if_available():
    paths = {
        "mean": MODEL_DIR / f"em_mean_{MODEL_VERSION}.pkl",
        "q16": MODEL_DIR / f"em_q16_{MODEL_VERSION}.pkl",
        "q84": MODEL_DIR / f"em_q84_{MODEL_VERSION}.pkl",
        "q025": MODEL_DIR / f"em_q025_{MODEL_VERSION}.pkl",
        "q975": MODEL_DIR / f"em_q975_{MODEL_VERSION}.pkl",
    }
    loaded = {}
    for k, p in paths.items():
        if p.exists():
            try:
                loaded[k] = joblib.load(p)
            except Exception as e:
                print(f"[ML] Failed to load model {k} at {p}: {e}")
    return loaded


def _prepare_latest_features(
    con: "duckdb.DuckDBPyConnection", feats_path: Path, symbols: list[str]
) -> pd.DataFrame:
    # Restrict to recent timestamps per symbol to reduce load
    try:
        df_max = con.execute(
            "SELECT underlying, MAX(quote_ts) AS max_ts FROM read_parquet(?) GROUP BY underlying",
            [str(feats_path)],
        ).fetchdf()
        if df_max.empty:
            return pd.DataFrame()
        # Pull rows where quote_ts = max_ts per underlying
        sql = (
            "SELECT * FROM read_parquet(?) WHERE (underlying, quote_ts) IN ("
            "SELECT underlying, MAX(quote_ts) FROM read_parquet(?) GROUP BY underlying)"
        )
        df = con.execute(sql, [str(feats_path), str(feats_path)]).fetchdf()
        if symbols:
            df = df[df["underlying"].isin(symbols)]
        # Keep numeric feature columns
        drop_cols = {"underlying", "quote_ts", "exp_date", "horizon", "created_at"}
        feature_cols = [
            c
            for c in df.columns
            if c not in drop_cols and pd.api.types.is_numeric_dtype(df[c])
        ]
        cols = feature_cols + ["underlying", "quote_ts", "exp_date", "horizon"]
        return df[cols].copy()
    except Exception as e:
        print(f"[ML] _prepare_latest_features error: {e}")
        return pd.DataFrame()


def predict_lightgbm_if_possible(symbols: list[str]):
    models = _load_models_if_available()
    if not models:
        return {}
    feats_path, _ = _features_labels_paths()
    if not feats_path.exists():
        return {}
    con = _open_duckdb()
    if con is None:
        return {}
    try:
        df_feats = _prepare_latest_features(con, feats_path, symbols)
        if df_feats.empty:
            return {}
        drop_cols = {"underlying", "quote_ts", "exp_date", "horizon"}
        feature_cols = [c for c in df_feats.columns if c not in drop_cols]
        X = df_feats[feature_cols]

        preds = {}

        # Compute quantiles
        def key(i: int):
            return (
                df_feats.iloc[i]["underlying"],
                pd.to_datetime(df_feats.iloc[i]["quote_ts"], utc=True).to_pydatetime(),
                pd.to_datetime(df_feats.iloc[i]["exp_date"]).date(),
                str(df_feats.iloc[i]["horizon"]),
            )

        if "q16" in models and "q84" in models:
            q16 = models["q16"].predict(X)
            q84 = models["q84"].predict(X)
        else:
            q16 = q84 = None
        if "q025" in models and "q975" in models:
            q025 = models["q025"].predict(X)
            q975 = models["q975"].predict(X)
        else:
            q025 = q975 = None

        for i in range(len(df_feats)):
            preds[key(i)] = {
                "band68_low": float(q16[i]) if q16 is not None else None,
                "band68_high": float(q84[i]) if q84 is not None else None,
                "band95_low": float(q025[i]) if q025 is not None else None,
                "band95_high": float(q975[i]) if q975 is not None else None,
            }
        return preds
    except Exception as e:
        print(f"[ML] Prediction error: {e}")
        return {}
    finally:
        try:
            con.close()
        except Exception:
            pass


def merge_predictions_into_rows(rows: list[tuple], preds: dict) -> list[tuple]:
    if not preds:
        return rows
    merged = []
    for r in rows:
        underlying, quote_ts, exp_date, horizon, em, b68l, b68h, b95l, b95h = r
        k = (underlying, quote_ts, exp_date, horizon)
        p = preds.get(k)
        if p:
            b68l = (
                p.get("band68_low", b68l) if p.get("band68_low") is not None else b68l
            )
            b68h = (
                p.get("band68_high", b68h) if p.get("band68_high") is not None else b68h
            )
            b95l = (
                p.get("band95_low", b95l) if p.get("band95_low") is not None else b95l
            )
            b95h = (
                p.get("band95_high", b95h) if p.get("band95_high") is not None else b95h
            )
        merged.append(
            (underlying, quote_ts, exp_date, horizon, em, b68l, b68h, b95l, b95h)
        )
    return merged


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


def insert_forecasts(conn, rows):
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

    # Standardize dtypes for Parquet schema compatibility (DuckDB/Postgres)
    df_all["underlying"] = df_all["underlying"].astype("string")
    df_all["horizon"] = df_all["horizon"].astype("string")
    df_all["quote_ts"] = (
        pd.to_datetime(df_all["quote_ts"], utc=True, errors="coerce").dt.tz_localize(None)
    )
    df_all["exp_date"] = pd.to_datetime(df_all["exp_date"], errors="coerce").dt.date
    df_all["created_at"] = (
        pd.to_datetime(df_all["created_at"], utc=True, errors="coerce").dt.tz_localize(None)
    )
    for c in [
        "em_baseline",
        "band68_low",
        "band68_high",
        "band95_low",
        "band95_high",
    ]:
        df_all[c] = pd.to_numeric(df_all[c], errors="coerce").astype("float64")

    # Reorder columns for stability
    cols_order = [
        "underlying",
        "quote_ts",
        "exp_date",
        "horizon",
        "em_baseline",
        "band68_low",
        "band68_high",
        "band95_low",
        "band95_high",
        "created_at",
    ]
    df_all = df_all[cols_order]

    # Enforce Arrow schema and write Parquet with snappy
    schema = pa.schema(
        [
            pa.field("underlying", pa.string()),
            pa.field("quote_ts", pa.timestamp("ns")),
            pa.field("exp_date", pa.date32()),
            pa.field("horizon", pa.string()),
            pa.field("em_baseline", pa.float64()),
            pa.field("band68_low", pa.float64()),
            pa.field("band68_high", pa.float64()),
            pa.field("band95_low", pa.float64()),
            pa.field("band95_high", pa.float64()),
            pa.field("created_at", pa.timestamp("ns")),
        ]
    )
    table = pa.Table.from_pandas(df_all, schema=schema, preserve_index=False)
    pq.write_table(table, parquet_path, compression="snappy")
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
        # Optional training step
        train_lightgbm_if_possible()

        # Baseline forecasts then optionally enrich with model-based bands
        rows = compute_baseline_forecasts(symbols)
        preds = predict_lightgbm_if_possible(symbols)
        rows = merge_predictions_into_rows(rows, preds)
        insert_forecasts(conn, rows)
        print(f"[ML] Inserted {len(rows)} baseline forecasts into Postgres.")
        # Also upsert to Parquet for DuckDB backend
        upsert_parquet_forecasts(rows, DATA_DIR)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
