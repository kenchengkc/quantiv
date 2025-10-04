# ML MVP2 Architecture: ML-Powered Earnings Expected Move Forecaster

## Overview

The ML MVP2 system combines historical options chain data with machine learning to generate accurate expected move forecasts for earnings events. It builds on the math baseline (MVP1) by adding:

1. **Historical Bias Curve Conditioning** - Learns systematic biases in ATM straddle pricing by lead time
2. **Multi-Horizon ML Models** - Separate LightGBM models for T-1, T-2, T-3, T-7, T-14, T-21 forecasts
3. **Feature Engineering Pipeline** - Extracts 20+ features from options chains (Greeks, skew, volume, term structure)
4. **Blended Serving** - Combines math baseline with ML correction factors and confidence bands

## Data Architecture

### Historical Options Data
- **Source**: 517 Parquet files spanning 2019-2025 (2.1GB)
- **Schema**: `act_symbol, date, expiration, strike, call_put, bid, ask, vol, delta, gamma, theta, vega, rho`
- **Coverage**: 2,202 unique symbols with 87M+ option records
- **Market Regimes**: Pre-COVID (2019), COVID crash (2020), meme stocks (2021), bear market (2022), banking crisis (2023), AI rally (2024-25)

### Earnings Calendar
- **Source**: `data/earnings_calendar.csv`
- **Schema**: `act_symbol, date, when`
- **Coverage**: 6,968 symbols, 101K+ earnings events
- **Overlap**: 2,108 symbols with both earnings and options data

## Pipeline Components

### 1. Bias Curve Builder (`apps/ml/bias_curve_builder.py`)

**Purpose**: Learn historical calibration multipliers for ATM straddle pricing

**Methodology**:
```python
# For each (symbol, earnings_date, lead_time_days):
1. Pair call/put options at same (symbol, date, expiration, strike)
2. Estimate spot S_hat = strike where |call_delta| ≈ 0.5
3. Compute EM_math = (call_mid + put_mid) / S_hat
4. Compute realized_move = |ln(S_post / S_pre)|
5. Compute bias_ratio = realized_move / EM_math
```

**Output**: `data/bias_curves.parquet`
- Market-level curves: median bias_ratio per lead_time bucket (T-1, T-2, T-3, T-7, T-14, T-21, T-30)
- Sector-level curves: sector-specific multipliers where sufficient data exists

**Key Insight**: ATM straddles systematically under/overestimate realized moves depending on lead time and market regime. Bias curves correct for this.

### 2. Feature Engineering (`apps/ml/feature_engineering.py`)

**Purpose**: Extract training datasets for each horizon

**Features Extracted** (per earnings event at T-k):

**Price & Moneyness**:
- `underlying_price` (S_hat proxy)
- `log_price`
- `log_market_cap`

**ATM Straddle Features**:
- `atm_straddle_price` (call_mid + put_mid at ATM strike)
- `atm_straddle_pct` (straddle / S_hat) - **primary EM_math baseline**
- `atm_iv` (proxy from |call_delta|)

**Greeks**:
- `atm_delta`, `atm_gamma`, `atm_theta`, `atm_vega`

**Skew & Surface**:
- `skew_25d` (25-delta put vs call normalized mid difference)
- `iv_term_slope` (IV term structure slope across expirations)

**Volume & Liquidity**:
- `total_volume` (sum of call + put volume)
- `pc_volume_ratio` (put volume / call volume)

**Temporal**:
- `horizon` (T-k lead time in days)
- `earnings_month`, `earnings_weekday`
- `tte_earnings` (time to earnings in years)

**Target Variable**:
```python
target = realized_move / em_math  # Correction factor
```

**Output**: `data/ml_training/training_T{horizon}.parquet` for each horizon

### 3. Model Trainer (`apps/ml/model_trainer.py`)

**Purpose**: Train LightGBM regression models per horizon

**Model Architecture**:
- **Algorithm**: LightGBM with MAE loss
- **Hyperparameter Optimization**: Optuna with 50 trials per horizon
- **Cross-Validation**: TimeSeriesSplit (3 folds) to prevent leakage
- **Train/Val Split**: 80/20 chronological split
- **Calibration**: Isotonic regression on validation predictions for confidence intervals

**Optimized Hyperparameters**:
- `num_leaves`: 10-300
- `learning_rate`: 0.01-0.3
- `feature_fraction`: 0.4-1.0
- `bagging_fraction`: 0.4-1.0
- `min_child_samples`: 5-100
- `reg_alpha`, `reg_lambda`: L1/L2 regularization

**Output**: 
- `models/lgbm_T{horizon}.joblib` - Trained model + calibrator
- `models/metadata_T{horizon}.json` - Metrics and feature importance

**Validation Metrics**:
- MAE (Mean Absolute Error) on correction factor
- RMSE (Root Mean Squared Error)
- Feature importance rankings

### 4. Serving Pipeline (`apps/ml/serving_pipeline.py`)

**Purpose**: Generate live forecasts combining math + ML

**Serving Flow**:
```python
1. Calculate EM_math baseline from current ATM straddle
2. Apply bias curve multiplier: EM_corrected = EM_math * bias_multiplier
3. Extract live features from current options chain
4. Predict ML correction factor: correction = model.predict(features)
5. Blend: EM_final = EM_corrected * correction
6. Generate confidence bands: P10, P50, P90
```

**Confidence Band Calculation**:
```python
uncertainty = max(0.1, 1.0 - combined_confidence)
p10 = em_ml * (1 - uncertainty)
p50 = em_ml
p90 = em_ml * (1 + uncertainty)
```

**Output**: Real-time forecasts with:
- `em_math` - Raw ATM straddle baseline
- `em_ml` - ML-enhanced prediction
- `p10, p50, p90` - Confidence bands
- `correction_factor` - ML adjustment
- `bias_multiplier` - Historical calibration
- `combined_confidence` - Forecast reliability score

## Technical Implementation

### DuckDB Query Patterns

**Spot Price Estimation**:
```sql
-- S_hat = strike where |call_delta| ≈ 0.5
SELECT symbol, quote_date,
       arg_min(strike, ABS(ABS(COALESCE(call_delta,0.0)) - 0.5)) AS s_hat
FROM paired_options
GROUP BY symbol, quote_date
```

**ATM Strike Selection**:
```sql
-- Minimize |ln(K/S)|
SELECT symbol, earnings_date, lead_time_days,
       arg_min(strike, ABS(LN(strike / s_hat))) AS atm_strike
FROM options_with_spot
GROUP BY symbol, earnings_date, lead_time_days
```

**Realized Move Calculation**:
```sql
-- |ln(S_post / S_pre)| around earnings
SELECT ABS(LN(post.s_hat / pre.s_hat)) AS realized_move
FROM earnings_events
JOIN spot_estimates pre ON pre.date = earnings_date - 1
JOIN spot_estimates post ON post.date = earnings_date + 1
```

### Key Design Decisions

1. **No Synthetic Data**: All training uses real historical Parquet files + Polygon API
2. **Spot Proxy via Delta**: Use `|call_delta| ≈ 0.5` strike as S_hat when underlying price unavailable
3. **Separate Models per Horizon**: T-1 dynamics differ from T-21; dedicated models capture this
4. **Time-Based Splits**: Prevent lookahead bias in backtesting
5. **Isotonic Calibration**: Ensures confidence bands are well-calibrated to actual coverage

## Performance Targets

### Coverage Calibration
- **68% band**: Should contain 68% of realized moves (1-sigma)
- **95% band**: Should contain 95% of realized moves (2-sigma)

### Accuracy Metrics
- **MAE**: Mean absolute error on correction factor < 0.15
- **Pinball Loss**: Quantile regression loss for P10/P90 bands
- **Bias**: Mean(predicted - realized) ≈ 0

### Production Requirements
- **Latency**: < 100ms for single forecast
- **Batch**: Process 500+ symbols in < 30s
- **Refresh**: Nightly model retraining with latest data
- **Monitoring**: Track calibration drift and retrain triggers

## File Structure

```
apps/ml/
├── bias_curve_builder.py      # Historical bias curve extraction
├── feature_engineering.py     # Training data generation
├── model_trainer.py           # LightGBM training per horizon
├── serving_pipeline.py        # Live forecast generation
├── run_ml_pipeline.py         # Orchestration script
└── requirements.txt           # Dependencies

data/
├── parquet/
│   └── options_chain/         # 517 Parquet files (2019-2025)
├── earnings_calendar.csv      # Earnings events
├── bias_curves.parquet        # Learned calibration curves
└── ml_training/               # Training datasets per horizon
    ├── training_T1.parquet
    ├── training_T2.parquet
    └── ...

models/
├── lgbm_T1.joblib            # Trained models
├── lgbm_T2.joblib
├── ...
└── metadata_T*.json          # Model metrics
```

## Usage

### Training Pipeline
```bash
cd apps/ml
python run_ml_pipeline.py
```

This will:
1. Extract bias curves from 2019-2024 data
2. Generate training datasets for all horizons
3. Train LightGBM models with hyperparameter optimization
4. Save models and metadata
5. Test serving pipeline with sample forecasts

### Generating Forecasts
```python
from serving_pipeline import MLServingPipeline

pipeline = MLServingPipeline()
forecast = pipeline.generate_forecast(
    symbol='AAPL',
    earnings_date='2025-01-30',
    sector='Technology'
)

print(f"EM Math: {forecast['em_math']:.3f}")
print(f"EM ML: {forecast['em_ml']:.3f}")
print(f"Confidence: {forecast['combined_confidence']:.3f}")
print(f"Bands: [{forecast['p10']:.3f}, {forecast['p90']:.3f}]")
```

### Batch Forecasts
```python
symbols_earnings = [
    ('AAPL', '2025-01-30', 'Technology'),
    ('MSFT', '2025-01-29', 'Technology'),
    ('GOOGL', '2025-02-04', 'Technology'),
]

forecasts = pipeline.batch_forecast(symbols_earnings)
```

## Next Steps (MVP3+)

1. **Quantile Regression Models**: Direct P10/P50/P90 prediction instead of correction factors
2. **Ensemble Methods**: Combine multiple model architectures (LightGBM + XGBoost + Neural Nets)
3. **Live Data Integration**: Real-time Polygon API streaming for intraday updates
4. **Sector-Specific Models**: Dedicated models for Tech, Finance, Healthcare, etc.
5. **Regime Detection**: Automatic VIX regime classification and model switching
6. **Explainability**: SHAP values for feature importance per prediction
7. **A/B Testing**: Compare Math-only vs ML-enhanced forecasts in production
8. **Feedback Loop**: Incorporate realized moves to continuously improve models

## References

- Original Plan: `MEMORY[0134e0d5-37ba-4c9f-97cb-354129e58516]`
- DuckDB Architecture: `docs/duckdb_architecture.md`
- Math Baseline: `tools/build_weekly_json_v2.py`
- Frontend Integration: `apps/frontend/components/WeeklyEarnings.tsx`
