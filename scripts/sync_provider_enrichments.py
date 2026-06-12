#!/usr/bin/env python3
"""Build backend-only derived enrichment tables from free/basic providers.

This script intentionally writes derived summaries only. Raw provider payloads
are used in memory and discarded.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable

import requests

from provider_probe import CAPABILITIES_PATH, capability_is_ok, classify_response, load_capabilities
from provider_specs import EndpointSpec, endpoint_specs_by_id
from provider_utils import (
    DEFAULT_LEDGER_PATH,
    ProviderQuotaError,
    ProviderUsageLedger,
    api_keys_for_provider,
    default_data_dir,
    load_local_env,
    write_json,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = REPO_ROOT / "apps" / "frontend" / "public"
POPULAR_WEIGHTS_PATH = REPO_ROOT / "apps" / "frontend" / "lib" / "popular.ts"
OUTPUT_DIR = default_data_dir() / "provider_enrichments"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

WORK_ORDER = [
    "massive_options_snapshot",
    "av_earnings",
    "av_realtime_put_call_ratio",
    "av_realtime_voi_ratio",
    "av_historical_put_call_ratio",
    "av_historical_voi",
    "fmp_profile",
    "fmp_key_metrics_ttm",
    "fmp_ratios_ttm",
    "fmp_ratings_snapshot",
    "fmp_analyst_estimates",
    "massive_ticker_overview",
    "massive_short_interest",
    "massive_dividends",
    "massive_splits",
    "fmp_batch_aftermarket_quote",
    "fmp_batch_aftermarket_trade",
    "td_quote",
    "td_rsi_daily",
    "av_treasury_yield_10y",
    "av_federal_funds_rate",
    "av_cpi",
    "av_ipo_calendar",
    "massive_ipos",
    "fmp_symbol_changes",
    "fmp_delisted_companies",
    "av_news_sentiment",
    "td_press_releases",
    "fmp_press_releases",
    "massive_stock_news",
]

ENDPOINT_SYMBOL_LIMITS = {
    # Alpha Vantage is 25/day per key. Key-pool stacking multiplies the budget.
    "av_earnings": 4,
    "av_realtime_put_call_ratio": 3,
    "av_realtime_voi_ratio": 3,
    "av_historical_put_call_ratio": 4,
    "av_historical_voi": 4,
    "av_news_sentiment": 2,
}

# FMP free tier only serves a subset of tickers for these fundamentals endpoints.
# Probe passes on AAPL, but calendar reporters often 402 — skip after first deny.
FMP_SYMBOL_TIER_ENDPOINTS = frozenset(
    {
        "fmp_key_metrics_ttm",
        "fmp_ratios_ttm",
        "fmp_ratings_snapshot",
        "fmp_analyst_estimates",
    }
)

# Denied symbols persist across runs so tomorrow's budget isn't re-spent
# discovering the same tier blocks. Entries expire so symbols promoted into
# FMP's free tier (or fixed entitlements) are eventually retried.
FMP_SYMBOL_BLOCKS_FILENAME = "fmp_symbol_blocks.json"
FMP_SYMBOL_BLOCK_DAYS = 30


def load_fmp_symbol_blocks(path: Path, today: date) -> dict[str, str]:
    """Map of 402-blocked FMP symbols to their ISO retry date, expired pruned."""
    try:
        body = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(body, dict):
        return {}
    blocks: dict[str, str] = {}
    for symbol, until in body.items():
        try:
            if date.fromisoformat(str(until)) > today:
                blocks[str(symbol)] = str(until)
        except ValueError:
            continue
    return blocks


def normalize_symbol(value: Any) -> str | None:
    symbol = str(value or "").strip().upper()
    return symbol if SYMBOL_RE.match(symbol) else None


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _add_symbol(scores: dict[str, int], value: Any, weight: int) -> None:
    symbol = normalize_symbol(value)
    if symbol:
        scores[symbol] = scores.get(symbol, 0) + weight


def _add_events(scores: dict[str, int], path: Path, weight: int) -> None:
    data = _load_json(path)
    if not isinstance(data, dict):
        return
    events = data.get("events")
    if not isinstance(events, list):
        return
    for event in events:
        if isinstance(event, dict):
            _add_symbol(scores, event.get("ticker") or event.get("symbol") or event.get("act_symbol"), weight)


def _add_popular(scores: dict[str, int], path: Path, weight: int) -> None:
    try:
        text = path.read_text()
    except OSError:
        return
    for symbol, score in re.findall(r'"([A-Z][A-Z0-9.\-]{0,9})":\s*(\d+)', text):
        _add_symbol(scores, symbol, weight + int(score))


def priority_symbols(max_symbols: int) -> list[str]:
    scores: dict[str, int] = {}
    _add_events(scores, PUBLIC_DIR / "weekly.json", 10_000)
    _add_events(scores, PUBLIC_DIR / "screener.json", 5_000)
    for path in sorted((PUBLIC_DIR / "weeks").glob("*.json"), reverse=True):
        if path.name != "manifest.json":
            _add_events(scores, path, 2_000)
    _add_popular(scores, POPULAR_WEIGHTS_PATH, 100)
    return [
        symbol
        for symbol, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    ][:max_symbols]


def _float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first_row(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        return next((row for row in payload if isinstance(row, dict)), None)
    if isinstance(payload, dict):
        for key in (
            "results",
            "data",
            "press_releases",
            "feed",
            "put_call_ratios",
            "volume_open_interest_ratios",
            "values",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return next((row for row in value if isinstance(row, dict)), None)
        if isinstance(payload.get("results"), dict):
            return payload["results"]
        return payload
    return None


def _rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in keys or (
            "results",
            "data",
            "press_releases",
            "feed",
            "put_call_ratios",
            "volume_open_interest_ratios",
            "values",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _selected(row: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: row.get(key) for key in keys if row.get(key) not in (None, "", [])}


def normalize_company_facts(spec: EndpointSpec, symbol: str, payload: Any, collected_at: str) -> list[dict[str, Any]]:
    row = _first_row(payload)
    if not row:
        return []
    base = {
        "symbol": symbol,
        "provider": spec.provider,
        "source_endpoint": spec.id,
        "collected_at": collected_at,
    }
    if spec.id == "fmp_profile":
        fields = _selected(
            row,
            [
                "companyName",
                "sector",
                "industry",
                "marketCap",
                "beta",
                "exchange",
                "exchangeShortName",
                "currency",
                "country",
                "isActivelyTrading",
            ],
        )
        return [{**base, **fields}]
    if spec.id in {"fmp_key_metrics_ttm", "fmp_ratios_ttm", "fmp_ratings_snapshot", "fmp_financial_scores"}:
        keys = [
            "marketCap",
            "enterpriseValue",
            "peRatioTTM",
            "priceToSalesRatioTTM",
            "pbRatioTTM",
            "debtToEquityTTM",
            "returnOnEquityTTM",
            "returnOnAssetsTTM",
            "currentRatioTTM",
            "grossProfitMarginTTM",
            "netProfitMarginTTM",
            "rating",
            "ratingScore",
            "ratingRecommendation",
            "DCFRecommendation",
            "PiotroskiScore",
            "AltmanZScore",
        ]
        fields = _selected(row, keys)
        return [{**base, **fields}] if fields else []
    if spec.id == "fmp_analyst_estimates":
        fields = _selected(
            row,
            [
                "date",
                "revenueAvg",
                "revenueLow",
                "revenueHigh",
                "epsAvg",
                "epsLow",
                "epsHigh",
                "numAnalystsRevenue",
                "numAnalystsEps",
            ],
        )
        return [{**base, **fields}] if fields else []
    if spec.id == "fmp_stock_peers":
        peers = row.get("peersList") or row.get("peers")
        if isinstance(peers, list):
            peers = [normalize_symbol(p) for p in peers]
            peers = [p for p in peers if p][:25]
        return [{**base, "peer_count": len(peers or []), "peers": peers or []}]
    if spec.id == "av_earnings":
        annual = _rows({"results": row.get("annualEarnings")}, "results")
        quarterly = _rows({"results": row.get("quarterlyEarnings")}, "results")
        latest = quarterly[0] if quarterly else {}
        return [
            {
                **base,
                "annual_event_count": len(annual),
                "quarterly_event_count": len(quarterly),
                "latest_reported_date": latest.get("reportedDate"),
                "latest_reported_eps": _float(latest.get("reportedEPS")),
            }
        ]
    if spec.id.startswith("av_") and spec.category == "macro":
        data = _rows(payload, "data")
        latest = data[0] if data else {}
        return [
            {
                **base,
                "symbol": "__macro__",
                "indicator": spec.id.replace("av_", ""),
                "observation_count": len(data),
                "latest_date": latest.get("date"),
                "latest_value": _float(latest.get("value")),
            }
        ]
    if spec.id == "massive_ticker_overview":
        result = row.get("results") if isinstance(row.get("results"), dict) else row
        fields = _selected(
            result,
            [
                "ticker",
                "name",
                "market",
                "locale",
                "primary_exchange",
                "type",
                "active",
                "currency_name",
                "cik",
                "composite_figi",
                "share_class_figi",
                "market_cap",
                "sic_description",
                "weighted_shares_outstanding",
                "share_class_shares_outstanding",
            ],
        )
        branding = result.get("branding") if isinstance(result, dict) else None
        if isinstance(branding, dict):
            fields["has_branding"] = bool(branding.get("logo_url") or branding.get("icon_url"))
        return [{**base, **fields}]
    if spec.id == "massive_short_interest":
        result = _rows(payload, "results")
        latest = result[0] if result else {}
        return [
            {
                **base,
                "settlement_date": latest.get("settlement_date"),
                "short_interest": _int(latest.get("short_interest")),
                "avg_daily_volume": _int(latest.get("avg_daily_volume")),
                "days_to_cover": _float(latest.get("days_to_cover")),
            }
        ]
    if spec.id == "td_quote":
        fields = _selected(
            row,
            ["symbol", "name", "exchange", "mic_code", "currency", "datetime", "average_volume", "is_market_open"],
        )
        fifty_two = row.get("fifty_two_week")
        if isinstance(fifty_two, dict):
            fields["fifty_two_week_low"] = _float(fifty_two.get("low"))
            fields["fifty_two_week_high"] = _float(fifty_two.get("high"))
        return [{**base, **fields}]
    if spec.id == "td_rsi_daily":
        values = _rows(payload, "values")
        latest = values[0] if values else {}
        return [
            {
                **base,
                "latest_date": latest.get("datetime"),
                "rsi": _float(latest.get("rsi")),
                "observation_count": len(values),
            }
        ]
    return []


def normalize_news_signal(spec: EndpointSpec, symbol: str, payload: Any, collected_at: str) -> list[dict[str, Any]]:
    base = {
        "symbol": symbol,
        "provider": spec.provider,
        "source_endpoint": spec.id,
        "collected_at": collected_at,
    }
    if spec.id == "av_news_sentiment":
        rows = _rows(payload, "feed")
        scores: list[float] = []
        for article in rows:
            for item in article.get("ticker_sentiment") or []:
                if isinstance(item, dict) and normalize_symbol(item.get("ticker")) == symbol:
                    score = _float(item.get("ticker_sentiment_score"))
                    if score is not None:
                        scores.append(score)
        latest = max((str(row.get("time_published") or "") for row in rows), default="")
        return [
            {
                **base,
                "article_count": len(rows),
                "latest_published_at": latest or None,
                "mean_ticker_sentiment": round(sum(scores) / len(scores), 6) if scores else None,
                "earnings_topic": True,
                "official_press_release": False,
            }
        ]
    rows = _rows(payload, "press_releases", "results", "data")
    latest_fields = ["publishedDate", "published_utc", "datetime", "date"]
    latest = max(
        (
            str(next((row.get(field) for field in latest_fields if row.get(field)), ""))
            for row in rows
        ),
        default="",
    )
    official = spec.id in {"td_press_releases", "fmp_press_releases"}
    earnings_keywords = 0
    for row in rows:
        text = f"{row.get('title') or ''} {row.get('headline') or ''}".lower()
        if "earnings" in text or "results" in text or "quarter" in text:
            earnings_keywords += 1
    return [
        {
            **base,
            "article_count": len(rows),
            "latest_published_at": latest or None,
            "official_press_release": official,
            "earnings_keyword_count": earnings_keywords,
        }
    ]


def normalize_options_signal(spec: EndpointSpec, symbol: str, payload: Any, collected_at: str) -> list[dict[str, Any]]:
    base = {
        "symbol": symbol,
        "provider": spec.provider,
        "source_endpoint": spec.id,
        "collected_at": collected_at,
    }
    if spec.id in {
        "av_historical_voi",
        "av_realtime_voi_ratio",
        "av_historical_put_call_ratio",
        "av_realtime_put_call_ratio",
    }:
        rows = _rows(payload, "data", "put_call_ratios", "volume_open_interest_ratios")
        latest = rows[0] if rows else {}
        metric_name = "volume_open_interest_ratio"
        metric_value = _float(
            latest.get("volume_oi_ratio")
            or latest.get("volume_open_interest_ratio")
            or latest.get("ratio")
        )
        if "put_call" in spec.id:
            metric_name = "put_call_ratio"
            metric_value = _float(
                latest.get("put_call_ratio")
                or latest.get("putCallRatio")
                or latest.get("ratio")
            )
        return [
            {
                **base,
                "metric": metric_name,
                "mode": "realtime" if "realtime" in spec.id else "historical",
                "observation_count": len(rows),
                "latest_date": latest.get("date") or latest.get("timestamp") or latest.get("time"),
                "latest_value": metric_value,
            }
        ]
    if spec.id != "massive_options_snapshot":
        return []
    rows = _rows(payload, "results")
    if not rows:
        return []
    underlying_price = None
    for row in rows:
        underlying = row.get("underlying_asset")
        if isinstance(underlying, dict):
            underlying_price = _float(underlying.get("price"))
            if underlying_price is not None:
                break
    call_oi = put_oi = call_volume = put_volume = 0
    iv_count = greeks_count = bid_ask_count = 0
    spreads: list[float] = []
    nearest: list[tuple[float, float]] = []
    latest_quote_ts = ""
    for row in rows:
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        contract_type = str(details.get("contract_type") or "").lower()
        oi = _int(row.get("open_interest")) or 0
        volume = _int((row.get("day") or {}).get("volume")) or 0
        if contract_type.startswith("c"):
            call_oi += oi
            call_volume += volume
        elif contract_type.startswith("p"):
            put_oi += oi
            put_volume += volume
        iv = _float(row.get("implied_volatility"))
        if iv is not None:
            iv_count += 1
        greeks = row.get("greeks") if isinstance(row.get("greeks"), dict) else {}
        if any(_float(greeks.get(k)) is not None for k in ("delta", "gamma", "theta", "vega")):
            greeks_count += 1
        quote = row.get("last_quote") if isinstance(row.get("last_quote"), dict) else {}
        bid = _float(quote.get("bid"))
        ask = _float(quote.get("ask"))
        if bid is not None and ask is not None and ask > 0 and bid >= 0 and ask >= bid:
            bid_ask_count += 1
            mid = (bid + ask) / 2
            if mid > 0:
                spreads.append((ask - bid) / mid)
        ts = str(quote.get("timestamp") or "")
        if ts > latest_quote_ts:
            latest_quote_ts = ts
        strike = _float(details.get("strike_price"))
        if underlying_price and strike and iv is not None:
            nearest.append((abs(strike - underlying_price), iv))
    nearest.sort(key=lambda item: item[0])
    atm_iv_values = [iv for _dist, iv in nearest[:4]]
    return [
        {
            **base,
            "contract_count": len(rows),
            "call_count": sum(1 for row in rows if str((row.get("details") or {}).get("contract_type") or "").lower().startswith("c")),
            "put_count": sum(1 for row in rows if str((row.get("details") or {}).get("contract_type") or "").lower().startswith("p")),
            "underlying_price": underlying_price,
            "total_call_open_interest": call_oi,
            "total_put_open_interest": put_oi,
            "put_call_open_interest_ratio": round(put_oi / call_oi, 6) if call_oi else None,
            "total_call_volume": call_volume,
            "total_put_volume": put_volume,
            "put_call_volume_ratio": round(put_volume / call_volume, 6) if call_volume else None,
            "iv_coverage_pct": round(100.0 * iv_count / len(rows), 2),
            "greeks_coverage_pct": round(100.0 * greeks_count / len(rows), 2),
            "bid_ask_coverage_pct": round(100.0 * bid_ask_count / len(rows), 2),
            "avg_spread_pct": round(100.0 * sum(spreads[:50]) / min(len(spreads), 50), 4) if spreads else None,
            "atm_iv": round(sum(atm_iv_values) / len(atm_iv_values), 6) if atm_iv_values else None,
            "latest_quote_timestamp": latest_quote_ts or None,
        }
    ]


def normalize_live_market_signal(spec: EndpointSpec, symbol: str, payload: Any, collected_at: str) -> list[dict[str, Any]]:
    rows = _rows(payload, "results", "data")
    row = rows[0] if rows else _first_row(payload)
    if not row:
        return []
    base = {
        "symbol": symbol,
        "provider": spec.provider,
        "source_endpoint": spec.id,
        "collected_at": collected_at,
    }
    if spec.id == "fmp_batch_aftermarket_quote":
        return [
            {
                **base,
                "signal_type": "aftermarket_quote",
                "bid": _float(row.get("bid") or row.get("bidPrice")),
                "ask": _float(row.get("ask") or row.get("askPrice")),
                "volume": _int(row.get("volume") or row.get("askSize") or row.get("bidSize")),
                "timestamp": row.get("timestamp") or row.get("time") or row.get("updatedAt"),
            }
        ]
    if spec.id == "fmp_batch_aftermarket_trade":
        return [
            {
                **base,
                "signal_type": "aftermarket_trade",
                "price": _float(row.get("price") or row.get("tradePrice")),
                "size": _int(row.get("size") or row.get("volume") or row.get("tradeSize")),
                "timestamp": row.get("timestamp") or row.get("time") or row.get("updatedAt"),
            }
        ]
    return []


def normalize_corporate_actions(spec: EndpointSpec, symbol: str, payload: Any, collected_at: str) -> list[dict[str, Any]]:
    rows = _rows(payload, "results", "data")
    if spec.response_kind == "csv" and isinstance(payload, str):
        reader = csv.DictReader(StringIO(payload))
        rows = [row for row in reader if isinstance(row, dict)]
    latest_fields = [
        "execution_date",
        "ex_dividend_date",
        "ipo_date",
        "date",
        "publishedDate",
        "delistedDate",
        "changedDate",
    ]
    latest = max(
        (
            str(next((row.get(field) for field in latest_fields if row.get(field)), ""))
            for row in rows
        ),
        default="",
    )
    return [
        {
            "symbol": symbol if spec.symbol_scoped else "__market__",
            "provider": spec.provider,
            "source_endpoint": spec.id,
            "collected_at": collected_at,
            "event_count": len(rows),
            "latest_event_date": latest or None,
        }
    ]


def normalize_response(spec: EndpointSpec, symbol: str, payload: Any, collected_at: str) -> dict[str, list[dict[str, Any]]]:
    tables = {
        "earnings_news_signals": [],
        "company_facts": [],
        "options_provider_signals": [],
        "corporate_actions": [],
        "live_market_signals": [],
    }
    if spec.derived_table == "earnings_news_signals":
        tables["earnings_news_signals"].extend(normalize_news_signal(spec, symbol, payload, collected_at))
    elif spec.derived_table == "company_facts":
        tables["company_facts"].extend(normalize_company_facts(spec, symbol, payload, collected_at))
    elif spec.derived_table == "options_provider_signals":
        tables["options_provider_signals"].extend(normalize_options_signal(spec, symbol, payload, collected_at))
    elif spec.derived_table == "corporate_actions":
        tables["corporate_actions"].extend(normalize_corporate_actions(spec, symbol, payload, collected_at))
    elif spec.derived_table == "live_market_signals":
        tables["live_market_signals"].extend(normalize_live_market_signal(spec, symbol, payload, collected_at))
    return tables


def fetch_payload(spec: EndpointSpec, ledger: ProviderUsageLedger, *, wait_for_minute: bool) -> tuple[Any | None, dict[str, Any]]:
    keys = api_keys_for_provider(spec.provider)
    if not keys:
        return None, {"status": "missing_key", "error": f"{spec.provider} API key missing"}
    accounts = [f"k{idx}" for idx in range(len(keys))]
    key_by_account = dict(zip(accounts, keys))
    try:
        chosen_account, _ = ledger.reserve_pooled(
            spec.provider,
            spec.id,
            accounts,
            credits=spec.credit_cost,
            symbols=[] if not spec.symbol_scoped else [str(spec.params.get("symbol") or spec.params.get("ticker") or "")],
            wait_for_minute=wait_for_minute,
        )
    except ProviderQuotaError as exc:
        return None, {"status": "quota_blocked", "error": str(exc)}
    api_key = key_by_account[chosen_account]
    try:
        response = requests.get(
            spec.url,
            params=spec.request_params(api_key),
            headers=spec.request_headers(api_key),
            timeout=60,
        )
    except requests.RequestException as exc:
        return None, {"status": "transport_error", "error": str(exc)[:240]}
    status = classify_response(spec, response)
    if status.get("status") != "ok":
        return None, status
    if spec.response_kind == "csv":
        return response.text, status
    try:
        return response.json(), status
    except Exception as exc:
        return None, {"status": "malformed", "error": str(exc)[:240]}


def build_work(
    symbols: list[str],
    *,
    max_total_calls: int,
    capabilities: dict[str, Any],
    ignore_capabilities: bool,
    cadences: set[str] | None = None,
    providers: set[str] | None = None,
) -> list[tuple[str, EndpointSpec]]:
    # Default: only daily-cadence endpoints. Weekly/monthly reference and macro
    # endpoints run when their cadence is explicitly enabled; "off" endpoints
    # (retired redundancies) never auto-run. `providers` narrows the run to a
    # subset (e.g. the afternoon AV-only workflow, which exists because Alpha
    # Vantage enforces its daily cap per IP — a separate runner gets a fresh
    # allowance).
    cadences = cadences or {"daily"}
    work: list[tuple[str, EndpointSpec]] = []
    for endpoint_id in WORK_ORDER:
        # Use the first symbol to decide whether this endpoint is symbol-scoped.
        base_spec = endpoint_specs_by_id(symbols[0] if symbols else "AAPL")[endpoint_id]
        if providers and base_spec.provider not in providers:
            continue
        if getattr(base_spec, "cadence", "daily") not in cadences:
            continue
        if not ignore_capabilities and not capability_is_ok(capabilities, endpoint_id):
            continue
        target_symbols = symbols if base_spec.symbol_scoped else ["__market__"]
        if endpoint_id in ENDPOINT_SYMBOL_LIMITS:
            target_symbols = target_symbols[: ENDPOINT_SYMBOL_LIMITS[endpoint_id]]
        for symbol in target_symbols:
            spec = endpoint_specs_by_id(symbol if symbol != "__market__" else "AAPL")[endpoint_id]
            work.append((symbol, spec))
            if len(work) >= max_total_calls:
                return work
    return work


def merge_table(tables: dict[str, list[dict[str, Any]]], incoming: dict[str, list[dict[str, Any]]]) -> None:
    for name, rows in incoming.items():
        tables.setdefault(name, []).extend(rows)


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-symbols", type=int, default=8)
    parser.add_argument("--max-total-calls", type=int, default=60)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--capabilities", type=Path, default=CAPABILITIES_PATH)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--symbols", default="")
    parser.add_argument(
        "--cadences",
        default="daily",
        help=(
            "Comma-separated cadences to run (daily,weekly,monthly). Default 'daily'. "
            "Schedule weekly/monthly from CI on the matching day to refresh "
            "slow-changing reference and macro endpoints."
        ),
    )
    parser.add_argument(
        "--providers",
        default="",
        help=(
            "Comma/space-separated provider subset (fmp, alphavantage, massive, "
            "twelvedata). Default: all providers."
        ),
    )
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ignore-capabilities", action="store_true")
    parser.add_argument("--respect-minute-limits", action="store_true")
    parser.add_argument("--allow-missing-key", action="store_true")
    args = parser.parse_args()
    if args.max_symbols < 1:
        parser.error("--max-symbols must be at least 1")
    if args.max_total_calls < 1:
        parser.error("--max-total-calls must be at least 1")

    manual = [normalize_symbol(raw) for raw in args.symbols.replace(",", " ").split()]
    symbols = [sym for sym in manual if sym] or priority_symbols(args.max_symbols)
    symbols = symbols[: args.max_symbols]
    if not symbols:
        print("no symbols selected", file=sys.stderr)
        return 1

    cadences = {c.strip().lower() for c in args.cadences.split(",") if c.strip()} or {"daily"}
    providers = {
        p.strip().lower() for p in args.providers.replace(",", " ").split() if p.strip()
    } or None
    if providers:
        allowed = {"fmp", "alphavantage", "massive", "twelvedata"}
        unknown = providers - allowed
        if unknown:
            parser.error(f"unknown provider(s): {', '.join(sorted(unknown))}")
    capabilities = load_capabilities(args.capabilities)
    work = build_work(
        symbols,
        max_total_calls=args.max_total_calls,
        capabilities=capabilities,
        ignore_capabilities=args.ignore_capabilities,
        cadences=cadences,
        providers=providers,
    )
    print(
        "Provider enrichment sync: "
        f"{len(symbols)} symbol(s), {len(work)} planned call(s), "
        f"capability_gate={'off' if args.ignore_capabilities else 'on'}"
    )
    if args.dry_run:
        by_provider: dict[str, int] = {}
        for symbol, spec in work:
            by_provider[spec.provider] = by_provider.get(spec.provider, 0) + spec.credit_cost
            print(f"would fetch {spec.provider}:{spec.id} for {symbol}")
        print(f"planned calls/credits by provider: {by_provider}")
        print("dry run: no API calls made and no files written")
        return 0

    ledger = ProviderUsageLedger(args.ledger)
    tables: dict[str, list[dict[str, Any]]] = {
        "earnings_news_signals": [],
        "company_facts": [],
        "options_provider_signals": [],
        "corporate_actions": [],
        "live_market_signals": [],
    }
    errors: list[dict[str, Any]] = []
    today = datetime.now(timezone.utc).date()
    fmp_symbol_blocks = load_fmp_symbol_blocks(
        args.output_dir / FMP_SYMBOL_BLOCKS_FILENAME, today
    )
    fmp_symbol_blocked: set[str] = set(fmp_symbol_blocks)
    collected_at = datetime.now(timezone.utc).isoformat()
    for idx, (symbol, spec) in enumerate(work, start=1):
        if (
            spec.provider == "fmp"
            and spec.id in FMP_SYMBOL_TIER_ENDPOINTS
            and symbol in fmp_symbol_blocked
        ):
            print(
                f"skip {spec.provider}:{spec.id} {symbol} ({idx}/{len(work)}) — fmp tier block",
                flush=True,
            )
            continue
        print(f"fetching {spec.provider}:{spec.id} {symbol} ({idx}/{len(work)})", flush=True)
        payload, status = fetch_payload(spec, ledger, wait_for_minute=args.respect_minute_limits)
        if payload is None:
            errors.append({"symbol": symbol, "endpoint": spec.id, **status})
            print(f"  {status.get('status')}")
            err = str(status.get("error") or "").lower()
            if (
                spec.provider == "fmp"
                and spec.id in FMP_SYMBOL_TIER_ENDPOINTS
                and status.get("status") == "entitlement_denied"
                and "symbol" in err
            ):
                fmp_symbol_blocked.add(symbol)
                fmp_symbol_blocks[symbol] = (
                    today + timedelta(days=FMP_SYMBOL_BLOCK_DAYS)
                ).isoformat()
        else:
            merge_table(tables, normalize_response(spec, symbol, payload, collected_at))
            print("  ok")
        if idx < len(work):
            pause = args.delay
            if spec.provider == "alphavantage":
                # AV free tier throttles at 1 req/sec; never undercut the floor.
                pause = max(pause, 1.2)
            if pause > 0:
                time.sleep(pause)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "planned_calls": len(work),
        "errors": errors[:100],
        "error_count": len(errors),
        "tables": {name: len(rows) for name, rows in tables.items()},
    }
    for name, rows in tables.items():
        write_json(args.output_dir / f"{name}.json", {"generated_at": metadata["generated_at"], "rows": rows})
    write_json(args.output_dir / "metadata.json", metadata)
    write_json(args.output_dir / FMP_SYMBOL_BLOCKS_FILENAME, fmp_symbol_blocks)
    print(f"wrote {args.output_dir}")

    if work and len(errors) == len(work) and all(err.get("status") == "missing_key" for err in errors):
        if args.allow_missing_key:
            return 0
        print("all provider enrichment calls skipped because keys are missing", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
