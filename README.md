# Quantiv

Use Quantiv to look up earnings dates for U.S. stocks. Pick a company to see how much its stock moved after past reports and how much it might move after the next one. [Open the earnings calendar](https://usequantiv.com)

## What does a 5% forecast mean?

For a stock trading at $100, a 5% forecast means a move of about $5, up or down. To see how past forecasts held up, visit the [validation page](https://usequantiv.com/validation).

The label beside each forecast tells you where the number came from. The calendar uses the first estimate available in this order.

| Label | How it works |
| --- | --- |
| ML forecast | A model learns from past earnings reports. Quantiv uses its forecast when the model has passed testing and its data is recent enough. |
| IV forecast | Quantiv uses implied volatility (IV) to estimate the move. IV measures how much prices might swing, based on what traders pay for options. |
| Straddle implied | Add the prices of a call and a put with the same strike price and expiration. Divide by the stock price to get the move as a percentage. |
| Historical median | Take the size of each past earnings move, whether the stock rose or fell. The median is the middle of those values. |

Stock quotes can update during the day. The options estimates use end-of-day prices.

## How the calendar gets its numbers

Each day, Quantiv checks upcoming earnings dates and reporting times against several data sources. It compares the new calendar with the last published one to catch missing reports. The website reads the saved results when you open a week or a ticker.

Quantiv chooses a model based on how many calendar days separate the stock price from the earnings date. For a Wednesday report, Monday's closing price goes into the model trained to forecast two days ahead. If the latest closing price hasn't arrived, Quantiv can use the previous trading day's close. The forecast shows the date of the price it used.

To test a model on past earnings, Quantiv trains it on older reports and sets aside later ones. For each test report, the model gets only the information available on the forecast date. Quantiv then compares its estimate with the actual move and checks how it did against a simpler estimate, such as the stock's historical median. [Forecast testing details](docs/ML_FORECAST_COVERAGE.md)

## Elsewhere on the site

- Filter upcoming reports and compare companies in the [screener](https://usequantiv.com/screener).
- See how often stocks moved more than option prices suggested in the [research lab](https://usequantiv.com/research).
- Save the tickers you follow to [your watchlist](https://usequantiv.com/sign-in?redirect_url=%2Fwatchlist).
- See how Quantiv calculates each number and where the data comes from in the [methodology guide](https://usequantiv.com/about).

In October 2026, Quantiv had 120,000+ earnings records, 10,000+ searchable tickers and 150+ monthly active users.

## Run the site locally

With Node.js 24 installed, run these commands from the repository root.

```bash
npm ci
npm run dev
```

Open [localhost:3000](http://localhost:3000). The repository includes calendar data. For sign-in, copy `.env.example` to `config/.env.local` and add your Clerk development keys. To save watchlists, set `DATABASE_URL` to your Postgres connection string. [Environment settings](.env.example)

Run the frontend checks from the same directory.

```bash
npm run lint
npm run type-check
npm run test --workspace=apps/frontend -- --run
```

### Data and forecasts

If you're working on the daily refresh or the models, install Python 3.11. On macOS or Linux, start in the repository root.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.txt
python -m pip install --no-deps -e apps/ml
```

On Windows, use `py -3.11 -m venv .venv` and `.\.venv\Scripts\Activate.ps1` in PowerShell, then run the two install commands above.

Once the input files are in `data/`, run `npm run data:views` and then `npm run data:frontend` to rebuild the site's JSON files. [Data setup](docs/R2_SETUP.md) · [Daily refresh steps](.github/workflows/data-refresh.yml)

## Code

| Directory | Contents |
| --- | --- |
| [apps/frontend](apps/frontend/) | Next.js website, API routes and calendar data |
| [apps/ml](apps/ml/) | Forecast models, training code and tests |
| [apps/backend](apps/backend/) | FastAPI service that recalculates forecasts with a newer stock price |
| [scripts](scripts/README.md) | Scripts to fetch data, check it and run forecasts |
| [tools](tools/README.md) | Tools that build the site's data files |

For help deploying the site, checking a model or restoring an earlier version, see the [project docs](docs/README.md).

[Contributing](CONTRIBUTING.md) · [Report a security issue](SECURITY.md) · [GPL v3.0 license](LICENSE)

Quantiv is for research and education.
