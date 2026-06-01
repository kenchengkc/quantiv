#!/usr/bin/env python3
"""Endpoint definitions for additive/free-tier provider probes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


FMP_BASE_URL = "https://financialmodelingprep.com"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
MASSIVE_BASE_URL = "https://api.massive.com"
TWELVEDATA_BASE_URL = "https://api.twelvedata.com"


@dataclass(frozen=True)
class EndpointSpec:
    id: str
    provider: str
    category: str
    purpose: str
    url: str
    params: dict[str, Any] = field(default_factory=dict)
    response_kind: str = "json"
    credit_cost: int = 1
    symbol_scoped: bool = True
    heavy: bool = False
    derived_table: str | None = None
    doc_url: str = ""
    # Refresh cadence for the nightly enrichment runner. "daily" runs every
    # night; "weekly"/"monthly" only run when the runner is invoked with that
    # cadence enabled (slow-changing reference/macro data); "off" keeps the
    # spec in the catalog for manual/probe use but never auto-runs it (used to
    # retire redundant pulls without losing the endpoint definition).
    cadence: str = "daily"

    def request_params(self, api_key: str) -> dict[str, Any]:
        params = dict(self.params)
        if self.provider in {"fmp", "alphavantage", "twelvedata"}:
            params["apikey"] = api_key
        return params

    def request_headers(self, api_key: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json,text/csv;q=0.9,*/*;q=0.8",
            "User-Agent": "Quantiv provider-enrichment",
        }
        if self.provider == "massive":
            headers["Authorization"] = f"Bearer {api_key}"
        return headers


def _previous_weekday(today: date) -> date:
    cur = today - timedelta(days=1)
    while cur.weekday() >= 5:
        cur -= timedelta(days=1)
    return cur


def endpoint_specs(sample_symbol: str = "AAPL", today: date | None = None) -> list[EndpointSpec]:
    symbol = sample_symbol.strip().upper() or "AAPL"
    today = today or date.today()
    prev_day = _previous_weekday(today)
    week_ago = prev_day - timedelta(days=7)

    fmp_doc = "https://site.financialmodelingprep.com/developer/docs"
    av_doc = "https://www.alphavantage.co/documentation/"
    massive_doc = "https://massive.com/docs/rest"
    td_doc = "https://twelvedata.com/docs"

    return [
        EndpointSpec(
            "fmp_profile",
            "fmp",
            "company_facts",
            "Company profile/reference data",
            f"{FMP_BASE_URL}/stable/profile",
            {"symbol": symbol},
            derived_table="company_facts",
            doc_url=fmp_doc,
            # Finnhub owns the profile/reference role (sync_finnhub_profiles.py).
            # Keep FMP profile for sector/beta/marketCap cross-checks, but only
            # weekly — it is slow-changing reference data.
            cadence="weekly",
        ),
        EndpointSpec(
            "fmp_analyst_estimates",
            "fmp",
            "analyst",
            "Forward EPS/revenue analyst estimates",
            f"{FMP_BASE_URL}/stable/analyst-estimates",
            {"symbol": symbol, "period": "annual", "page": 0, "limit": 1},
            derived_table="company_facts",
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "fmp_ratings_snapshot",
            "fmp",
            "analyst",
            "Current ratings snapshot",
            f"{FMP_BASE_URL}/stable/ratings-snapshot",
            {"symbol": symbol},
            derived_table="company_facts",
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "fmp_key_metrics_ttm",
            "fmp",
            "fundamentals",
            "TTM key metrics",
            f"{FMP_BASE_URL}/stable/key-metrics-ttm",
            {"symbol": symbol},
            derived_table="company_facts",
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "fmp_ratios_ttm",
            "fmp",
            "fundamentals",
            "TTM financial ratios",
            f"{FMP_BASE_URL}/stable/ratios-ttm",
            {"symbol": symbol},
            derived_table="company_facts",
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "fmp_financial_scores",
            "fmp",
            "fundamentals",
            "Financial score summary",
            f"{FMP_BASE_URL}/stable/financial-scores",
            {"symbol": symbol},
            derived_table="company_facts",
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "fmp_stock_peers",
            "fmp",
            "reference",
            "Company peer list",
            f"{FMP_BASE_URL}/stable/stock-peers",
            {"symbol": symbol},
            derived_table="company_facts",
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "fmp_batch_aftermarket_quote",
            "fmp",
            "live_market",
            "Batch aftermarket bid/ask quote probe",
            f"{FMP_BASE_URL}/stable/batch-aftermarket-quote",
            {"symbols": symbol},
            derived_table="live_market_signals",
            doc_url="https://site.financialmodelingprep.com/developer/docs/stable/batch-aftermarket-quote",
        ),
        EndpointSpec(
            "fmp_batch_aftermarket_trade",
            "fmp",
            "live_market",
            "Batch aftermarket trade probe",
            f"{FMP_BASE_URL}/stable/batch-aftermarket-trade",
            {"symbols": symbol},
            derived_table="live_market_signals",
            doc_url="https://site.financialmodelingprep.com/developer/docs/stable/batch-aftermarket-trade",
        ),
        EndpointSpec(
            "fmp_press_releases",
            "fmp",
            "news",
            "Official company press releases",
            f"{FMP_BASE_URL}/stable/news/press-releases",
            {"symbols": symbol, "page": 0, "limit": 5},
            derived_table="earnings_news_signals",
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "fmp_stock_news",
            "fmp",
            "news",
            "Ticker-specific stock news",
            f"{FMP_BASE_URL}/stable/news/stock",
            {"symbols": symbol, "page": 0, "limit": 5},
            derived_table="earnings_news_signals",
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "fmp_symbol_changes",
            "fmp",
            "corporate_actions",
            "Recent symbol changes",
            f"{FMP_BASE_URL}/stable/symbol-change",
            {"page": 0, "limit": 5},
            symbol_scoped=False,
            derived_table="corporate_actions",
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "fmp_delisted_companies",
            "fmp",
            "corporate_actions",
            "Recent delisted companies",
            f"{FMP_BASE_URL}/stable/delisted-companies",
            {"page": 0, "limit": 5},
            symbol_scoped=False,
            derived_table="corporate_actions",
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "fmp_profile_bulk",
            "fmp",
            "bulk",
            "Bulk company profiles",
            f"{FMP_BASE_URL}/stable/profile-bulk",
            {"part": 0},
            symbol_scoped=False,
            heavy=True,
            derived_table="company_facts",
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "fmp_eod_bulk",
            "fmp",
            "bulk",
            "Bulk EOD prices by date for gap filling only",
            f"{FMP_BASE_URL}/stable/eod-bulk",
            {"date": prev_day.isoformat()},
            symbol_scoped=False,
            heavy=True,
            derived_table=None,
            doc_url=fmp_doc,
        ),
        EndpointSpec(
            "av_news_sentiment",
            "alphavantage",
            "news",
            "Earnings-topic news sentiment",
            ALPHA_VANTAGE_URL,
            {"function": "NEWS_SENTIMENT", "tickers": symbol, "topics": "earnings", "limit": 10},
            derived_table="earnings_news_signals",
            doc_url=av_doc,
            # Retired from the daily run: news is single-sourced from
            # fmp_press_releases. AV's 25/day budget is reserved for the
            # unique options ratios / EARNINGS endpoints.
            cadence="off",
        ),
        EndpointSpec(
            "av_earnings",
            "alphavantage",
            "earnings",
            "Earnings history/estimates validation",
            ALPHA_VANTAGE_URL,
            {"function": "EARNINGS", "symbol": symbol},
            derived_table="company_facts",
            doc_url=av_doc,
        ),
        EndpointSpec(
            "av_realtime_put_call_ratio",
            "alphavantage",
            "options",
            "Realtime options put/call ratio",
            ALPHA_VANTAGE_URL,
            {"function": "REALTIME_PUT_CALL_RATIO", "symbol": symbol},
            derived_table="options_provider_signals",
            doc_url=av_doc,
        ),
        EndpointSpec(
            "av_realtime_voi_ratio",
            "alphavantage",
            "options",
            "Realtime options volume/open-interest ratio",
            ALPHA_VANTAGE_URL,
            {"function": "REALTIME_VOLUME_OPEN_INTEREST_RATIO", "symbol": symbol},
            derived_table="options_provider_signals",
            doc_url=av_doc,
        ),
        EndpointSpec(
            "av_historical_put_call_ratio",
            "alphavantage",
            "options",
            "Historical options put/call ratio",
            ALPHA_VANTAGE_URL,
            {"function": "HISTORICAL_PUT_CALL_RATIO", "symbol": symbol},
            derived_table="options_provider_signals",
            doc_url=av_doc,
        ),
        EndpointSpec(
            "av_listing_status_active",
            "alphavantage",
            "corporate_actions",
            "Active listing directory",
            ALPHA_VANTAGE_URL,
            {"function": "LISTING_STATUS", "state": "active"},
            response_kind="csv",
            symbol_scoped=False,
            heavy=True,
            derived_table="corporate_actions",
            doc_url=av_doc,
        ),
        EndpointSpec(
            "av_listing_status_delisted",
            "alphavantage",
            "corporate_actions",
            "Delisted listing directory",
            ALPHA_VANTAGE_URL,
            {"function": "LISTING_STATUS", "state": "delisted"},
            response_kind="csv",
            symbol_scoped=False,
            heavy=True,
            derived_table="corporate_actions",
            doc_url=av_doc,
        ),
        EndpointSpec(
            "av_ipo_calendar",
            "alphavantage",
            "corporate_actions",
            "IPO calendar",
            ALPHA_VANTAGE_URL,
            {"function": "IPO_CALENDAR"},
            response_kind="csv",
            symbol_scoped=False,
            derived_table="corporate_actions",
            doc_url=av_doc,
        ),
        EndpointSpec(
            "av_treasury_yield_10y",
            "alphavantage",
            "macro",
            "10-year Treasury yield macro context",
            ALPHA_VANTAGE_URL,
            {"function": "TREASURY_YIELD", "interval": "monthly", "maturity": "10year"},
            symbol_scoped=False,
            derived_table="company_facts",
            doc_url=av_doc,
            cadence="monthly",
        ),
        EndpointSpec(
            "av_federal_funds_rate",
            "alphavantage",
            "macro",
            "Federal funds rate macro context",
            ALPHA_VANTAGE_URL,
            {"function": "FEDERAL_FUNDS_RATE", "interval": "monthly"},
            symbol_scoped=False,
            derived_table="company_facts",
            doc_url=av_doc,
            cadence="monthly",
        ),
        EndpointSpec(
            "av_cpi",
            "alphavantage",
            "macro",
            "CPI inflation macro context",
            ALPHA_VANTAGE_URL,
            {"function": "CPI", "interval": "monthly"},
            symbol_scoped=False,
            derived_table="company_facts",
            doc_url=av_doc,
            cadence="monthly",
        ),
        EndpointSpec(
            "av_historical_voi",
            "alphavantage",
            "options",
            "Historical options volume/open-interest ratio",
            ALPHA_VANTAGE_URL,
            {"function": "HISTORICAL_VOLUME_OPEN_INTEREST_RATIO", "symbol": symbol},
            derived_table="options_provider_signals",
            doc_url=av_doc,
        ),
        EndpointSpec(
            "massive_ticker_overview",
            "massive",
            "company_facts",
            "Ticker overview and identifiers",
            f"{MASSIVE_BASE_URL}/v3/reference/tickers/{symbol}",
            derived_table="company_facts",
            doc_url=massive_doc,
            cadence="weekly",  # Finnhub owns profiles; weekly cross-check only
        ),
        EndpointSpec(
            "massive_related_tickers",
            "massive",
            "reference",
            "Related companies",
            f"{MASSIVE_BASE_URL}/v1/related-companies/{symbol}",
            derived_table="company_facts",
            doc_url=massive_doc,
        ),
        EndpointSpec(
            "massive_stock_news",
            "massive",
            "news",
            "Ticker news",
            f"{MASSIVE_BASE_URL}/v2/reference/news",
            {"ticker": symbol, "limit": 5, "order": "desc", "sort": "published_utc"},
            derived_table="earnings_news_signals",
            doc_url=massive_doc,
            cadence="off",  # news single-sourced from fmp_press_releases
        ),
        EndpointSpec(
            "massive_dividends",
            "massive",
            "corporate_actions",
            "Cash dividends",
            f"{MASSIVE_BASE_URL}/v3/reference/dividends",
            {"ticker": symbol, "limit": 5},
            derived_table="corporate_actions",
            doc_url=massive_doc,
        ),
        EndpointSpec(
            "massive_splits",
            "massive",
            "corporate_actions",
            "Stock splits",
            f"{MASSIVE_BASE_URL}/v3/reference/splits",
            {"ticker": symbol, "limit": 5},
            derived_table="corporate_actions",
            doc_url=massive_doc,
        ),
        EndpointSpec(
            "massive_financials",
            "massive",
            "fundamentals",
            "Structured financial statements",
            f"{MASSIVE_BASE_URL}/vX/reference/financials",
            {"ticker": symbol, "limit": 1},
            derived_table="company_facts",
            doc_url=massive_doc,
        ),
        EndpointSpec(
            "massive_short_interest",
            "massive",
            "sentiment",
            "FINRA short interest",
            f"{MASSIVE_BASE_URL}/stocks/v1/short-interest",
            {"ticker": symbol, "limit": 1, "sort": "settlement_date.desc"},
            derived_table="company_facts",
            doc_url=massive_doc,
        ),
        EndpointSpec(
            "massive_options_snapshot",
            "massive",
            "options",
            "Current option-chain snapshot with IV/Greeks/OI",
            f"{MASSIVE_BASE_URL}/v3/snapshot/options/{symbol}",
            {"limit": 250},
            derived_table="options_provider_signals",
            doc_url="https://massive.com/docs/rest/options/snapshots/option-chain-snapshot",
        ),
        EndpointSpec(
            "massive_aggregates_daily",
            "massive",
            "ohlcv",
            "Adjusted daily bars for realized-move gap filling only",
            f"{MASSIVE_BASE_URL}/v2/aggs/ticker/{symbol}/range/1/day/{week_ago.isoformat()}/{prev_day.isoformat()}",
            {"adjusted": "true", "sort": "asc", "limit": 5},
            derived_table=None,
            doc_url=massive_doc,
        ),
        EndpointSpec(
            "massive_market_status",
            "massive",
            "reference",
            "Market status",
            f"{MASSIVE_BASE_URL}/v1/marketstatus/now",
            symbol_scoped=False,
            derived_table=None,
            doc_url=massive_doc,
        ),
        EndpointSpec(
            "massive_ipos",
            "massive",
            "corporate_actions",
            "IPO reference data",
            f"{MASSIVE_BASE_URL}/vX/reference/ipos",
            {"limit": 5, "sort": "ipo_date.desc"},
            symbol_scoped=False,
            derived_table="corporate_actions",
            doc_url=massive_doc,
        ),
        EndpointSpec(
            "td_press_releases",
            "twelvedata",
            "news",
            "Official company press releases",
            f"{TWELVEDATA_BASE_URL}/press_releases",
            {"symbol": symbol, "outputsize": 2, "language": "en,en-US"},
            derived_table="earnings_news_signals",
            doc_url="https://twelvedata.com/docs/fundamentals/last-changes",
            cadence="off",  # news single-sourced from fmp_press_releases
        ),
        EndpointSpec(
            "td_last_change_statistics",
            "twelvedata",
            "reference",
            "Statistics dataset change timestamp",
            f"{TWELVEDATA_BASE_URL}/last_change/statistics",
            symbol_scoped=False,
            derived_table="company_facts",
            doc_url=td_doc,
        ),
        EndpointSpec(
            "td_last_change_profile",
            "twelvedata",
            "reference",
            "Profile dataset change timestamp",
            f"{TWELVEDATA_BASE_URL}/last_change/profile",
            symbol_scoped=False,
            derived_table="company_facts",
            doc_url=td_doc,
        ),
        EndpointSpec(
            "td_quote",
            "twelvedata",
            "reference",
            "Quote metadata and 52-week context",
            f"{TWELVEDATA_BASE_URL}/quote",
            {"symbol": symbol},
            derived_table="company_facts",
            doc_url=td_doc,
            cadence="weekly",  # 52-week context is slow-changing reference data
        ),
        EndpointSpec(
            "td_rsi_daily",
            "twelvedata",
            "technical",
            "Daily RSI validation signal",
            f"{TWELVEDATA_BASE_URL}/rsi",
            {"symbol": symbol, "interval": "1day", "time_period": 14, "outputsize": 5},
            derived_table="company_facts",
            doc_url=td_doc,
        ),
        EndpointSpec(
            "td_time_series_daily",
            "twelvedata",
            "ohlcv",
            "Daily OHLCV fallback for realized-move gaps only",
            f"{TWELVEDATA_BASE_URL}/time_series",
            {
                "symbol": symbol,
                "interval": "1day",
                "start_date": week_ago.isoformat(),
                "end_date": prev_day.isoformat(),
                "order": "ASC",
                "adjust": "splits",
            },
            derived_table=None,
            doc_url=td_doc,
        ),
    ]


def endpoint_specs_by_id(sample_symbol: str = "AAPL") -> dict[str, EndpointSpec]:
    return {spec.id: spec for spec in endpoint_specs(sample_symbol)}
