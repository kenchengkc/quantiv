-- Quantiv Serving Schema for Expected Move Forecasting
-- Optimized for fast API queries and ML pipeline integration

-- Expected Move Forecasts serving table
DROP TABLE IF EXISTS em_forecasts CASCADE;
CREATE TABLE em_forecasts (
    underlying TEXT NOT NULL,
    quote_ts TIMESTAMPTZ NOT NULL,
    exp_date DATE NOT NULL,
    horizon TEXT NOT NULL,                  -- 'to_exp','1d','5d'
    em_baseline DOUBLE PRECISION,           -- S*ATM_IV*sqrt(T/365)
    em_hat DOUBLE PRECISION,                -- calibrated/quantile output
    band68_low DOUBLE PRECISION,
    band68_high DOUBLE PRECISION,
    band95_low DOUBLE PRECISION,
    band95_high DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (underlying, quote_ts, exp_date, horizon)
);

-- Optimized indexes for serving
CREATE INDEX idx_em_forecasts_lookup ON em_forecasts (underlying, exp_date, horizon);
CREATE INDEX idx_em_forecasts_recent ON em_forecasts (quote_ts DESC);
CREATE INDEX idx_em_forecasts_symbol_recent ON em_forecasts (underlying, quote_ts DESC);

-- Model metadata table
DROP TABLE IF EXISTS model_meta CASCADE;
CREATE TABLE model_meta (
    model_name TEXT PRIMARY KEY,
    trained_at TIMESTAMPTZ,
    version TEXT,
    notes TEXT,
    performance_metrics JSONB
);

-- Insert initial model metadata
INSERT INTO model_meta (model_name, trained_at, version, notes) VALUES
('calibrated_alpha', NOW(), '1.0', 'Baseline calibrated ATM-IV multiplier model'),
('quantile_lightgbm', NOW(), '1.0', 'LightGBM quantile regression model');
