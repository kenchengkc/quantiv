# Research Lab

The Research Lab turns Quantiv's point-in-time historical earnings evidence into queryable cohorts. It is designed for calibration and event research, not execution backtesting.

## Evidence boundary

An event is eligible only when the generated symbol research payload contains:

- a finite signed realized earnings move;
- a finite positive historical market-implied move;
- `implied_as_of`, identifying the observation date used before/at the event under the timing rule;
- `implied_quality_status = decision_eligible_eod`.

Those historical option observations are selected upstream from `v_eligible_straddles`, so the Research Lab reuses the same leg, pair, spread, delta, DTE, and quote-quality gates as the rest of Quantiv. It does not reconstruct historical implied moves from current quotes.

Realized moves are timing-aware close-to-close reactions:

- BMO: previous trading close to event-day/next available close;
- AMC: event-day/previous available close to next trading close;
- unknown/during-market rows use the conservative symmetric bracket already defined by the frontend-data pipeline.

## Build path

```text
public/symbols/*.json
        │
        ▼
predev / prebuild
build-research-history.mjs
        │
        ▼
public/research-history.json
        │
        ▼
/api/research/cohort
        │
        ├── content-addressed JSON
        ├── CSV export
        └── /research UI
```

`research-history.json` is deterministic and derived entirely from checked-in/generated symbol research payloads. It intentionally has no wall-clock build timestamp. Identical symbol payloads therefore produce identical historical-universe contents.

The aggregate is generated at frontend dev/build time instead of scanning hundreds of symbol files inside every serverless API request.

## Query contract

Current filters are:

- ticker substring (`q`);
- report session (`timing=bmo|amc`);
- fiscal quarter (`quarter=Q1|Q2|Q3|Q4`);
- realized move inside/outside the implied move (`outcome=inside|outside`);
- EPS beat/miss (`eps=beat|miss`);
- minimum/maximum implied move (`minImplied`, `maxImplied` as decimal fractions);
- minimum/maximum historical observation lead days (`minLead`, `maxLead`);
- sorting by date, ticker, implied move, realized move, edge, realized/implied ratio, or EPS surprise;
- sort direction and a bounded result limit.

The browser keeps these filters in the URL so a cohort view can be shared exactly.

## Diagnostics

The API computes summaries over the full matching cohort before applying the returned-row display limit:

- event and unique-symbol counts;
- average and median implied move;
- average and median absolute realized move;
- mean absolute implied-vs-realized error;
- share of events outside the implied move;
- median and interquartile realized/implied ratio;
- average signed move;
- average EPS surprise where available.

The UI plots implied move on the x-axis and absolute realized move on the y-axis. The diagonal is `realized = implied`; points above it exceeded the market-priced range.

## Content identity

API responses use schema `quantiv.historical-cohort.v1` and receive a `sha256:<hex>` snapshot ID. The ID covers:

- historical-universe source metadata;
- current forecast evidence receipt and publication-control state;
- canonical query;
- summary/matching counts;
- exact ordered returned event rows;
- decision-scope declarations.

CSV carries the same ID in the response header and every row.

## Decision scope

The Research Lab permanently declares:

```json
{
  "decision_scope": "end_of_day_research",
  "live_trading_eligible": false,
  "live_quote_overlay_included": false
}
```

This is historical research evidence, not a fill simulator. It does not model bid/ask execution, intraday option marks, commissions, liquidity/size, slippage, or synchronized order timestamps. A future options-strategy backtester would require those additional point-in-time execution inputs rather than reusing this calibration surface and calling it P&L.
