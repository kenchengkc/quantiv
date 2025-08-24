-- PostgreSQL Schema for Quantiv Options Analytics
-- Optimized for 87M+ row datasets with partitioning

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- Main options chain table (partitioned by date)
CREATE TABLE options_chain (
    id BIGSERIAL,
    date DATE NOT NULL,
    act_symbol VARCHAR(10) NOT NULL,
    expiration DATE NOT NULL,
    strike DECIMAL(10,2) NOT NULL,
    call_put CHAR(1) NOT NULL CHECK (call_put IN ('C', 'P')),
    bid DECIMAL(8,2),
    ask DECIMAL(8,2),
    vol DECIMAL(6,4),  -- Implied Volatility
    delta DECIMAL(6,4),
    gamma DECIMAL(8,6),
    theta DECIMAL(8,6),
    vega DECIMAL(6,4),
    open_interest INTEGER,
    volume INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, date)
) PARTITION BY RANGE (date);

-- Create partitions for different years (improves query performance)
CREATE TABLE options_chain_2019 PARTITION OF options_chain
    FOR VALUES FROM ('2019-01-01') TO ('2020-01-01');
CREATE TABLE options_chain_2020 PARTITION OF options_chain
    FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
CREATE TABLE options_chain_2021 PARTITION OF options_chain
    FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
CREATE TABLE options_chain_2022 PARTITION OF options_chain
    FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE options_chain_2023 PARTITION OF options_chain
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE options_chain_2024 PARTITION OF options_chain
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE options_chain_2025 PARTITION OF options_chain
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');

-- Volatility history table
CREATE TABLE volatility_history (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    iv DECIMAL(6,4),           -- Current IV
    hv DECIMAL(6,4),           -- Historical Volatility
    iv_rank DECIMAL(5,2),      -- IV Rank (0-100)
    iv_percentile DECIMAL(5,2), -- IV Percentile (0-100)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Optimized indexes for options analytics queries
CREATE INDEX IF NOT EXISTS idx_options_symbol_date ON options_chain (act_symbol, date);
CREATE INDEX IF NOT EXISTS idx_options_expiration ON options_chain (expiration) WHERE expiration >= CURRENT_DATE;
CREATE INDEX IF NOT EXISTS idx_options_strike_type ON options_chain (strike, call_put);
CREATE INDEX IF NOT EXISTS idx_options_vol ON options_chain (vol) WHERE vol IS NOT NULL;

-- Volatility indexes
CREATE INDEX IF NOT EXISTS idx_vol_symbol_date ON volatility_history (symbol, date);
CREATE INDEX IF NOT EXISTS idx_vol_iv_rank ON volatility_history (iv_rank) WHERE iv_rank IS NOT NULL;

-- Symbols metadata table for quick lookups
CREATE TABLE symbols_metadata (
    symbol VARCHAR(10) PRIMARY KEY,
    first_date DATE,
    last_date DATE,
    total_options BIGINT,
    avg_iv DECIMAL(6,4),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Analytics views for common queries
CREATE VIEW daily_iv_summary AS
SELECT 
    date,
    act_symbol,
    AVG(vol) as avg_iv,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY vol) as median_iv,
    COUNT(*) as option_count
FROM options_chain 
WHERE vol IS NOT NULL
GROUP BY date, act_symbol;

CREATE VIEW atm_options AS
SELECT *
FROM options_chain o1
WHERE ABS(strike - (
    SELECT AVG(strike) 
    FROM options_chain o2 
    WHERE o2.act_symbol = o1.act_symbol 
    AND o2.date = o1.date
)) < 5.0;

-- Performance optimization settings
ALTER SYSTEM SET shared_buffers = '256MB';
ALTER SYSTEM SET effective_cache_size = '1GB';
ALTER SYSTEM SET maintenance_work_mem = '64MB';
ALTER SYSTEM SET checkpoint_completion_target = 0.9;
ALTER SYSTEM SET wal_buffers = '16MB';
ALTER SYSTEM SET default_statistics_target = 100;

-- Reload configuration
SELECT pg_reload_conf();

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO quantiv_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO quantiv_user;
