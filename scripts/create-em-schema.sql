-- Expected Move Forecasting Schema for Quantiv
-- Extends existing options_chain with ML pipeline tables

-- Feature engineering intermediate table
CREATE TABLE IF NOT EXISTS atm_features (
    underlying TEXT NOT NULL,
    quote_date DATE NOT NULL,
    exp_date DATE NOT NULL,
    dte INTEGER NOT NULL,
    spot_price DECIMAL(10,2),
    atm_strike DECIMAL(10,2),
    atm_iv DECIMAL(8,4),
    atm_call_price DECIMAL(8,4),
    atm_put_price DECIMAL(8,4),
    straddle_price DECIMAL(8,4),
    em_baseline DECIMAL(8,4), -- S * σ_ATM * √(T/365)
    
    -- Skew features
    rr_25delta DECIMAL(8,4), -- 25Δ risk reversal
    fly_25delta DECIMAL(8,4), -- 25Δ butterfly
    
    -- Greeks aggregates
    avg_vega_atm DECIMAL(8,4),
    avg_gamma_atm DECIMAL(8,4),
    avg_theta_atm DECIMAL(8,4),
    
    -- Term structure
    iv_term_slope DECIMAL(8,4),
    
    PRIMARY KEY (underlying, quote_date, exp_date)
);

-- Model training labels
CREATE TABLE IF NOT EXISTS em_labels (
    underlying TEXT NOT NULL,
    quote_date DATE NOT NULL,
    exp_date DATE NOT NULL,
    horizon TEXT NOT NULL, -- 'to_exp', '1d', '5d'
    
    -- Realized moves
    realized_move DECIMAL(8,4), -- |ln(S_t+T/S_t)|
    
    -- Calibration targets
    alpha_multiplier DECIMAL(8,4), -- realized_move / em_baseline
    
    PRIMARY KEY (underlying, quote_date, exp_date, horizon),
    FOREIGN KEY (underlying, quote_date, exp_date) 
        REFERENCES atm_features(underlying, quote_date, exp_date)
);

-- Production forecasts serving table
CREATE TABLE IF NOT EXISTS em_forecasts (
    underlying TEXT NOT NULL,
    quote_ts TIMESTAMPTZ NOT NULL,
    exp_date DATE NOT NULL,
    horizon TEXT NOT NULL, -- 'to_exp', '1d', '5d'
    
    -- Model outputs
    em_baseline DECIMAL(8,4), -- S*ATM_IV*sqrt(T/365)
    em_calibrated DECIMAL(8,4), -- α̂ * em_baseline
    em_quantile DECIMAL(8,4), -- direct quantile prediction
    
    -- Confidence bands
    band68_low DECIMAL(8,4),
    band68_high DECIMAL(8,4),
    band95_low DECIMAL(8,4),
    band95_high DECIMAL(8,4),
    
    -- Model metadata
    model_version TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    PRIMARY KEY (underlying, quote_ts, exp_date, horizon)
);

-- Indexes for serving
CREATE INDEX IF NOT EXISTS idx_em_forecasts_lookup 
    ON em_forecasts (underlying, exp_date, horizon);
CREATE INDEX IF NOT EXISTS idx_em_forecasts_recent 
    ON em_forecasts (quote_ts DESC);

-- Model performance tracking
CREATE TABLE IF NOT EXISTS model_performance (
    model_name TEXT NOT NULL,
    evaluation_date DATE NOT NULL,
    horizon TEXT NOT NULL,
    
    -- Coverage metrics
    coverage_68 DECIMAL(5,4), -- should be ~0.68
    coverage_95 DECIMAL(5,4), -- should be ~0.95
    
    -- Accuracy metrics
    mae_em DECIMAL(8,4),
    mape_em DECIMAL(8,4),
    pinball_loss_68 DECIMAL(8,4),
    pinball_loss_95 DECIMAL(8,4),
    
    PRIMARY KEY (model_name, evaluation_date, horizon)
);
