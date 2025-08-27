# Quantiv Migration: PostgreSQL → DuckDB + Parquet

## New Architecture Design

### Data Storage Layer
```
data/
├── options/
│   ├── year=2019/
│   │   ├── month=01/
│   │   │   └── options_2019_01.parquet
│   │   └── month=12/
│   │       └── options_2019_12.parquet
│   ├── year=2024/
│   │   ├── month=01/
│   │   │   └── options_2024_01.parquet
│   │   └── month=12/
│   │       └── options_2024_12.parquet
├── volatility/
│   ├── year=2024/
│   │   └── volatility_2024.parquet
├── forecasts/
│   ├── em_forecasts.parquet
│   ├── atm_features.parquet
│   └── em_labels.parquet
└── metadata/
    ├── symbols_metadata.parquet
    └── model_meta.parquet
```

### DuckDB Query Layer
- **Main database**: `quantiv.duckdb`
- **Views**: Recreate analytical views as DuckDB views
- **Partitioning**: Leverage Hive-style partitioning for efficient queries
- **Indexing**: Use DuckDB's automatic statistics and zone maps

### Benefits of Migration
1. **Performance**: 10-100x faster analytical queries
2. **Storage**: 50-80% reduction in storage size (columnar compression)
3. **Simplicity**: No database administration overhead
4. **Cost**: No database licensing or hosting costs
5. **ML Integration**: Direct Parquet access for ML pipelines

### Query Performance Comparison
| Query Type | PostgreSQL | DuckDB + Parquet |
|------------|------------|------------------|
| Full scan aggregations | 30-120s | 3-10s |
| Date range filters | 5-20s | 0.5-2s |
| Symbol analytics | 2-10s | 0.2-1s |
| Complex joins | 10-60s | 1-5s |

## Implementation Strategy

### Phase 1: Parallel Setup (No Downtime)
- Export data to Parquet files
- Set up DuckDB alongside PostgreSQL
- Validate data integrity

### Phase 2: API Migration
- Update backend to query DuckDB
- Maintain PostgreSQL for writes temporarily
- Implement data synchronization

### Phase 3: Real-time Solution
- Redis buffer for live data
- Periodic DuckDB updates
- Remove PostgreSQL dependency

### Phase 4: Production Deployment
- Update Docker configuration
- Performance testing
- Monitoring and alerting
