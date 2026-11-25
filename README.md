# Quantiv

Use Quantiv to look up earnings dates for U.S. stocks. Pick a company to see how much its stock moved after past reports and how much it might move after the next one. [Open the earnings calendar](https://usequantiv.com)

## What does a 5% forecast mean?

For a stock trading at $100, a 5% forecast means a move of about $5, up or down. To see how past forecasts held up, visit the [validation page](https://usequantiv.com/validation).

The label beside each forecast tells you where the number came from. The calendar uses the first estimate available in this order.

| Label | How it works |
| --- | --- |
| ML forecast | A model estimates the size of the move from past earnings reactions, recent stock prices and, when available, options quotes. |
| IV forecast | Multiply annual implied volatility (IV) from option prices by the square root of the time to expiration in years. At 40% IV with 7 calendar days to expiration, the estimate is `40% × √(7 / 365) ≈ 5.5%`. |
| Straddle implied | Add the midpoint prices of a call and a put with the same strike and expiration. Divide by that strike, chosen near the stock price. A $3 call and a $2 put at a $100 strike imply a 5% move. |
| Historical median | Take the middle value of the stock's recent earnings moves after counting gains and losses by size. |

Stock quotes can update during the day. The options estimates use end-of-day prices.

## How the model works

**What counts as an earnings move?** Quantiv measures the change between the closing prices on either side of the announcement. For a report before the market opens, it uses the previous trading day's close and the report day's close. For an after-close report, it uses the report day's close and the next trading day's close. The calculation accounts for splits and cash dividends. The move as a percentage is `100 × abs(adjusted close after / close before − 1)`. A drop from $100 to $94 gives the model a 6% move to learn from. [Price calculation](apps/ml/ml/causal_features.py)

**How is it predicted?** Quantiv uses LightGBM, which builds a series of decision trees. Each new tree corrects errors in the estimate so far. It trains a separate model for each lead time using the median move over the last four reports, recent daily price swings and the price of an options straddle when quotes pass the quality checks. Monday's close for a Wednesday report uses the two-day model. If the latest close is missing, Quantiv can use one trading session earlier and keeps the actual price date. [Model inputs](apps/ml/ml/causal_features.py) · [Forecast timing](docs/ML_FORECAST_COVERAGE.md)

**How is it tested?** The main model minimizes absolute error. A 5% forecast against a 6% move misses by one percentage point. Quantiv tests on later reports set aside from training and model selection. It compares average absolute error with the straddle estimate for stocks with usable options, or with the stock's historical median otherwise.

Five more models per lead time estimate the 10th, 25th, 50th, 75th and 90th percentiles. Quantiv checks how many actual moves land between each pair of bounds. About half should fall between the 25th and 75th percentiles, and about 80% between the 10th and 90th. [Training code](apps/ml/model_trainer.py) · [Historical tests](apps/ml/ml/historical_admission.py)

## How the backend serves the site

| Request | What happens |
| --- | --- |
| Open the calendar or a ticker | GitHub Actions runs the daily Python refresh. It checks earnings dates across providers, reads price and option data from Parquet files with DuckDB, runs the models and writes JSON by week and ticker. Next.js on Vercel serves those files. Cloudflare R2 stores the datasets and model releases. |
| Get current stock prices | A quote worker writes prices to Upstash Redis. The calendar requests several tickers at once from `/api/stocks/batch-price`, which reads the saved quotes. |
| Recalculate a forecast with a new stock price | Vercel signs a request to `POST /api/ml/predict` on the Railway FastAPI service. The service reads the original model inputs from Neon Postgres, updates the fields that depend on the stock price and runs the verified model again. The options inputs keep their original date. |

[System architecture](docs/ARCHITECTURE.md) · [Prediction service](apps/backend/services/predict_service.py)

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
