# Quantiv

Quantiv is an earnings calendar for U.S. stocks. It shows when a company reports, how far its stock might move, and how the price changed after past reports. Pick a ticker on the calendar to see its earnings history and the estimate for its next report. [Open the earnings calendar](https://usequantiv.com)

## Reading a forecast

A 5% forecast means an estimated move of about 5% in either direction. The stock could rise or fall, and the actual move can be larger or smaller.

The calendar uses the first available method in the table. The label tells you which one you're seeing.

| Calendar label | Where the estimate comes from |
| --- | --- |
| ML forecast | A machine learning model trained on past earnings reports. Quantiv checks the model and the age of its inputs before showing a forecast. |
| IV forecast | Implied volatility (IV), a measure of how much stock price movement is reflected in option prices. |
| Straddle implied | The combined price of a call and a put with the same strike price and expiration, expressed as a percentage of the stock price. |
| Historical median | The middle value of the stock's past earnings moves, measured by size in either direction. |

Stock quotes can update during the trading day. The option prices used in a published forecast come from an end-of-day snapshot. For a look at how past forecasts compared with actual moves, see the [validation page](https://usequantiv.com/validation).

## Exploring earnings

- Filter earnings events and compare tickers side by side in the [screener](https://usequantiv.com/screener).
- Compare the move implied by options with the actual move across a group of past reports in the [research lab](https://usequantiv.com/research).
- Keep a watchlist of companies you follow. [Sign in to your watchlist](https://usequantiv.com/sign-in?redirect_url=%2Fwatchlist)
- Find the formulas and data sources in the [methodology guide](https://usequantiv.com/about).

As of October 2026, Quantiv had 120,000+ historical earnings records, 10,000+ searchable tickers and 150+ monthly active users.

## Local development

To start the website, use Node.js 24 and run these commands from the repository root.

```bash
npm ci
npm run dev
```

Open [localhost:3000](http://localhost:3000). The calendar can use the data included in the repository. To set up sign-in, copy `.env.example` to `config/.env.local` and fill in your Clerk development keys. Saved watchlists also need a Postgres connection. [Environment settings](.env.example)

Run the frontend checks from the same directory.

```bash
npm run lint
npm run type-check
npm run test --workspace=apps/frontend -- --run
```

### Working on data or models

The daily refresh checks earnings dates and prices, calculates forecasts and writes the JSON files the website reads. To work on the refresh or the models, set up Python 3.11. On macOS or Linux, run the following from the repository root.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.txt
python -m pip install --no-deps -e apps/ml
```

On Windows, create the environment with `py -3.11 -m venv .venv` and activate it in PowerShell with `.\.venv\Scripts\Activate.ps1`.

To rebuild the site's JSON from local data, run `npm run data:views` and then `npm run data:frontend`. You'll need the input datasets in `data/`. [Data setup](docs/R2_SETUP.md) · [Daily refresh steps](.github/workflows/data-refresh.yml)

## Finding the code

| Directory | What's there |
| --- | --- |
| [apps/frontend](apps/frontend/) | The Next.js website, API routes and published calendar data. |
| [apps/ml](apps/ml/) | Python code for training and testing forecast models. |
| [apps/backend](apps/backend/) | The FastAPI service for updating forecasts with a newer stock price. |
| [scripts](scripts/README.md) | Commands that fetch data, check it and run forecasts. |
| [tools](tools/README.md) | Code that builds the files served by the website. |

For deployment, model monitoring and rollback, see the [documentation index](docs/README.md). For development and reporting security issues, see the [contributing guide](CONTRIBUTING.md) and [security policy](SECURITY.md).

Quantiv is for research and education. [GNU General Public License v3.0](LICENSE)
