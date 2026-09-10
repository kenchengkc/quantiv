"""Point-in-time source supplements required by historical research.

The live earnings/calendar and corporate-action controls intentionally exclude
retired companies. Historical research must not inherit that active-universe
filter, so this module performs bounded provider reads for the explicit
retirement ledger and installs those rows only into the in-memory research
connection.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import duckdb

QueryFn = Callable[[str, str], list[dict[str, Any]]]
ROW_LIMIT = 1000
ACTION_START = date(2019, 1, 1)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _canonical_digest(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _paged_query(
    query_fn: QueryFn,
    *,
    api_url: str,
    select: str,
    table: str,
    where: str,
    order_by: str,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    offset = 0
    pages = 0
    while True:
        sql = (
            f"SELECT {select} FROM {table} WHERE {where} "
            f"ORDER BY {order_by} LIMIT {ROW_LIMIT} OFFSET {offset}"
        )
        batch = query_fn(sql, api_url)
        pages += 1
        rows.extend(batch)
        if len(batch) < ROW_LIMIT:
            break
        offset += ROW_LIMIT
    return rows, pages


def _normalize_earnings(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    timing = {
        "After market close": "after_market_close",
        "Before market open": "before_market_open",
        "amc": "after_market_close",
        "bmo": "before_market_open",
    }
    normalized = [
        {
            "ticker": str(row.get("act_symbol") or "").strip().upper(),
            "date": str(row.get("date") or "")[:10],
            "timing": timing.get(str(row.get("when") or row.get("timing") or ""), "unknown"),
        }
        for row in rows
    ]
    normalized = [row for row in normalized if row["ticker"] and len(row["date"]) == 10]
    normalized.sort(key=lambda row: (row["date"], row["ticker"], row["timing"]))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in normalized:
        key = (row["ticker"], row["date"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _normalize_actions(
    rows: Iterable[dict[str, Any]], value_columns: tuple[str, ...]
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("act_symbol") or "").strip().upper()
        ex_date = str(row.get("ex_date") or "")[:10]
        if not ticker or len(ex_date) != 10:
            continue
        item: dict[str, Any] = {"ticker": ticker, "ex_date": ex_date}
        for column in value_columns:
            item[column] = float(row[column])
        normalized.append(item)
    normalized.sort(key=lambda row: (row["ticker"], row["ex_date"]))
    return normalized


def fetch_retired_research_sources(
    query_fn: QueryFn,
    *,
    earnings_api: str,
    stocks_api: str,
    retired_tickers: Iterable[str],
    action_end: date,
) -> dict[str, Any]:
    """Fetch exhaustive bounded history for the explicit retirement ledger."""
    tickers = sorted({str(value).strip().upper() for value in retired_tickers if str(value).strip()})
    if not tickers:
        empty_digest = _canonical_digest([])
        return {
            "status": "verified",
            "method": "bounded_provider_query_for_explicit_retirement_ledger",
            "configured_tickers": [],
            "earnings": {"rows": [], "row_count": 0, "pages": 0, "sha256": empty_digest},
            "corporate_actions": {
                "splits": {"rows": [], "row_count": 0, "pages": 0, "sha256": empty_digest},
                "dividends": {"rows": [], "row_count": 0, "pages": 0, "sha256": empty_digest},
            },
            "missing_earnings_tickers": [],
        }

    in_list = ", ".join(_sql_literal(ticker) for ticker in tickers)
    earnings_raw, earnings_pages = _paged_query(
        query_fn,
        api_url=earnings_api,
        select="act_symbol, date, `when`",
        table="earnings_calendar",
        where=f"act_symbol IN ({in_list})",
        order_by="date, act_symbol",
    )
    split_raw, split_pages = _paged_query(
        query_fn,
        api_url=stocks_api,
        select="act_symbol, ex_date, to_factor, for_factor",
        table="split",
        where=(
            f"act_symbol IN ({in_list}) AND ex_date BETWEEN "
            f"{_sql_literal(ACTION_START.isoformat())} AND {_sql_literal(action_end.isoformat())}"
        ),
        order_by="act_symbol, ex_date",
    )
    dividend_raw, dividend_pages = _paged_query(
        query_fn,
        api_url=stocks_api,
        select="act_symbol, ex_date, amount",
        table="dividend",
        where=(
            f"act_symbol IN ({in_list}) AND ex_date BETWEEN "
            f"{_sql_literal(ACTION_START.isoformat())} AND {_sql_literal(action_end.isoformat())}"
        ),
        order_by="act_symbol, ex_date",
    )

    earnings = _normalize_earnings(earnings_raw)
    splits = _normalize_actions(split_raw, ("to_factor", "for_factor"))
    dividends = _normalize_actions(dividend_raw, ("amount",))
    returned_tickers = {row["ticker"] for row in earnings}

    return {
        "status": "verified",
        "method": "bounded_provider_query_for_explicit_retirement_ledger",
        "configured_tickers": tickers,
        "earnings": {
            "rows": earnings,
            "row_count": len(earnings),
            "pages": earnings_pages,
            "sha256": _canonical_digest(earnings),
        },
        "corporate_actions": {
            "splits": {
                "rows": splits,
                "row_count": len(splits),
                "pages": split_pages,
                "sha256": _canonical_digest(splits),
            },
            "dividends": {
                "rows": dividends,
                "row_count": len(dividends),
                "pages": dividend_pages,
                "sha256": _canonical_digest(dividends),
            },
        },
        "missing_earnings_tickers": sorted(set(tickers) - returned_tickers),
    }


def install_retired_earnings(
    conn: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]]
) -> int:
    """Add retired companies only to the in-memory research earnings table."""
    added = 0
    for row in rows:
        event_date = date.fromisoformat(str(row["date"]))
        exists = conn.execute(
            "SELECT 1 FROM earnings_events WHERE ticker = ? AND earnings_dt = ? LIMIT 1",
            [row["ticker"], event_date],
        ).fetchone()
        if exists:
            continue
        month = event_date.month
        fiscal_q = f"Q{(month - 1) // 3 + 1}"
        source_timing = str(row.get("timing") or "unknown")
        conn.execute(
            """
            INSERT INTO earnings_events (
                ticker, earnings_dt, timing, source, confirmed_flag,
                fiscal_year, fiscal_q, eps_actual, eps_estimate,
                revenue_actual, revenue_estimate, timing_source
            ) VALUES (?, ?, ?, 'dolthub_retired_membership', TRUE, ?, ?, NULL, NULL, NULL, NULL, ?)
            """,
            [
                row["ticker"],
                event_date,
                source_timing,
                event_date.year,
                fiscal_q,
                "reported" if source_timing in {"before_market_open", "after_market_close"} else "unknown",
            ],
        )
        added += 1
    return added


def install_research_corporate_actions(
    conn: duckdb.DuckDBPyConnection,
    *,
    data_dir: Path,
    supplemental_splits: list[dict[str, Any]],
    supplemental_dividends: list[dict[str, Any]],
) -> dict[str, Any]:
    """Install active receipt actions plus retired-company source supplements."""
    receipt_path = data_dir / "control" / "ingestion" / "corporate_actions" / "latest.json"
    if not receipt_path.is_file():
        raise RuntimeError("verified corporate-action latest receipt is required for source-level research")
    receipt = json.loads(receipt_path.read_text())
    split_path = data_dir / str(receipt["datasets"]["splits"]["partition"])
    dividend_path = data_dir / str(receipt["datasets"]["dividends"]["partition"])
    if not split_path.is_file() or not dividend_path.is_file():
        raise RuntimeError("corporate-action receipt points to missing partition(s)")

    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _research_splits AS
        SELECT CAST(act_symbol AS VARCHAR) AS act_symbol,
               CAST(ex_date AS DATE) AS ex_date,
               CAST(to_factor AS DOUBLE) AS to_factor,
               CAST(for_factor AS DOUBLE) AS for_factor
        FROM read_parquet('{split_path}')
        """
    )
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _research_dividends AS
        SELECT CAST(act_symbol AS VARCHAR) AS act_symbol,
               CAST(ex_date AS DATE) AS ex_date,
               CAST(amount AS DOUBLE) AS amount
        FROM read_parquet('{dividend_path}')
        """
    )
    for row in supplemental_splits:
        conn.execute(
            "INSERT INTO _research_splits VALUES (?, ?, ?, ?)",
            [row["ticker"], date.fromisoformat(row["ex_date"]), row["to_factor"], row["for_factor"]],
        )
    for row in supplemental_dividends:
        conn.execute(
            "INSERT INTO _research_dividends VALUES (?, ?, ?)",
            [row["ticker"], date.fromisoformat(row["ex_date"]), row["amount"]],
        )

    conn.execute(
        """
        CREATE OR REPLACE TEMP VIEW v_splits AS
        SELECT * FROM _research_splits
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY act_symbol, ex_date ORDER BY to_factor, for_factor
        ) = 1
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP VIEW v_dividends AS
        SELECT * FROM _research_dividends
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY act_symbol, ex_date ORDER BY amount
        ) = 1
        """
    )
    counts = conn.execute(
        "SELECT (SELECT COUNT(*) FROM v_splits), (SELECT COUNT(*) FROM v_dividends)"
    ).fetchone()
    return {
        "receipt_id": receipt.get("receipt_id"),
        "source_options_date": receipt.get("source_options_date"),
        "split_rows": int(counts[0] or 0),
        "dividend_rows": int(counts[1] or 0),
        "retired_split_rows": len(supplemental_splits),
        "retired_dividend_rows": len(supplemental_dividends),
    }


def bind_retired_membership(
    payload: dict[str, Any],
    evidence: dict[str, Any],
    *,
    installed_event_rows: int,
    corporate_action_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Bind explicit retirement-ledger source evidence into universe identity."""
    membership = {
        **evidence,
        "installed_event_rows": int(installed_event_rows),
        "corporate_action_control": corporate_action_evidence,
    }
    source = dict(payload["source"])
    source["retired_membership"] = membership
    identity = {
        key: value
        for key, value in payload.items()
        if key not in {"universe_id", "generated_at"}
    }
    identity["source"] = source
    payload["source"] = source
    payload["universe_id"] = _canonical_digest(identity)
    return payload
