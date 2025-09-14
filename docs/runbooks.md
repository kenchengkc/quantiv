# Quantiv ML Pipeline Runbooks

This document provides copy-paste commands for daily and weekly operations of the Quantiv ML pipeline.

## Daily Operations

### 1. Publish New Data from Laptop to VM

Run this from your laptop to sync new Parquet data to the VM:

```bash
# Set variables
KEY=~/.ssh/quantiv-ec2.pem
VM=ubuntu@ec2-xxx.amazonaws.com
RUN="run-$(date +%F-%H%M%S)"

# Sync data with atomic swap
rsync -av --delete -e "ssh -i $KEY" data/parquet/ "$VM":/srv/quantiv-data/_staging/"$RUN"/

# Promote staging to production on VM
ssh -i "$KEY" "$VM" "bash -s" <<'SH'
set -euo pipefail
cd /srv/quantiv-data
test -d _staging/$RUN/options_chain
test -d _staging/$RUN/volatility_history
find _staging/$RUN -type f -name '*.parquet' -quit
rm -rf parquet_new
cp -a _staging/$RUN parquet_new
rm -rf parquet_old
[ -d parquet ] && mv parquet parquet_old || true
mv parquet_new parquet
ls -1dt _staging/run-* 2>/dev/null | tail -n +6 | xargs -r rm -rf
echo "[ok] promoted $RUN"
SH
```

### 2. Daily Scoring Pipeline

Run this daily on the VM to generate new predictions:

```bash
# On the VM
cd /srv/quantiv-data
python3 scripts/daily_scoring_pipeline.py

# Check results
python3 scripts/data_healthcheck.py --quick
```

### 3. Quick Health Check

```bash
# On the VM - quick validation
python3 scripts/data_healthcheck.py --quick

# Full health check (weekly)
python3 scripts/data_healthcheck.py
```

## Weekly Operations

### 1. Full Data Refresh and Validation

```bash
# On the VM
cd /srv/quantiv-data

# Refresh DuckDB views and run full validation
python3 scripts/setup_duckdb_views.py
python3 scripts/data_healthcheck.py

# Rebuild features and labels (if needed)
python3 scripts/build_em_labels_features.py
```

### 2. Model Retraining (Weekly/Monthly)

```bash
# On the VM
cd /srv/quantiv-data

# Retrain models with latest data
python3 scripts/train_baseline_models.py

# Check model performance
ls -la models/
cat models/em_model_*_summary.json | tail -20
```

### 3. Backup Operations

```bash
# On the VM - create backups
cd /srv/quantiv-data

# Backup DuckDB database
cp quantiv.duckdb backups/quantiv_$(date +%Y%m%d).duckdb

# Backup models
tar -czf backups/models_$(date +%Y%m%d).tar.gz models/

# Clean old backups (keep 30 days)
find backups/ -name "*.duckdb" -mtime +30 -delete
find backups/ -name "*.tar.gz" -mtime +30 -delete
```

## Monitoring Commands

### Check Data Freshness

```bash
# On the VM
/home/ubuntu/.duckdb/cli/latest/duckdb /srv/quantiv-data/quantiv.duckdb <<'SQL'
SELECT 
  'options' as dataset,
  MAX(trade_date) as latest_date,
  COUNT(*) as total_rows
FROM v_options_norm
UNION ALL
SELECT 
  'volatility' as dataset,
  MAX(trade_date) as latest_date,
  COUNT(*) as total_rows
FROM v_volhist_norm;
SQL
```

### Check Recent Predictions

```bash
# On the VM
/home/ubuntu/.duckdb/cli/latest/duckdb /srv/quantiv-data/quantiv.duckdb <<'SQL'
SELECT 
  act_symbol,
  earnings_date,
  predicted_move,
  days_to_earnings,
  generated_at
FROM em_scores_latest
WHERE earnings_date >= current_date()
ORDER BY earnings_date
LIMIT 10;
SQL
```

### Check Disk Usage

```bash
# On the VM
du -sh /srv/quantiv-data/*
df -h /srv/quantiv-data
```

## Troubleshooting

### DuckDB Connection Issues

```bash
# Test DuckDB connection
/home/ubuntu/.duckdb/cli/latest/duckdb /srv/quantiv-data/quantiv.duckdb "SELECT 1"

# Recreate views if corrupted
python3 scripts/setup_duckdb_views.py --skip-healthcheck
```

### Missing Predictions

```bash
# Check upcoming earnings
/home/ubuntu/.duckdb/cli/latest/duckdb /srv/quantiv-data/quantiv.duckdb <<'SQL'
SELECT COUNT(*) FROM v_earnings_upcoming;
SELECT * FROM v_earnings_upcoming LIMIT 5;
SQL

# Manually run scoring
python3 scripts/daily_scoring_pipeline.py
```

### Data Quality Issues

```bash
# Run comprehensive validation
python3 scripts/data_healthcheck.py

# Check for data gaps
/home/ubuntu/.duckdb/cli/latest/duckdb /srv/quantiv-data/quantiv.duckdb <<'SQL'
SELECT 
  year(trade_date) as year,
  month(trade_date) as month,
  COUNT(*) as row_count
FROM v_options_norm
GROUP BY 1, 2
ORDER BY 1 DESC, 2 DESC
LIMIT 12;
SQL
```

## Cron Setup

Add these to your crontab on the VM:

```bash
# Edit crontab
crontab -e

# Add these lines:
# Daily scoring at 6 AM ET
0 6 * * * cd /srv/quantiv-data && python3 scripts/daily_scoring_pipeline.py >> logs/daily_scoring.log 2>&1

# Weekly health check on Sundays at 7 AM ET
0 7 * * 0 cd /srv/quantiv-data && python3 scripts/data_healthcheck.py >> logs/weekly_health.log 2>&1

# Monthly model retraining on 1st of month at 8 AM ET
0 8 1 * * cd /srv/quantiv-data && python3 scripts/train_baseline_models.py >> logs/monthly_training.log 2>&1

# Daily backup at 2 AM ET
0 2 * * * cd /srv/quantiv-data && cp quantiv.duckdb backups/quantiv_$(date +\%Y\%m\%d).duckdb
```

## Emergency Procedures

### Complete System Recovery

```bash
# On the VM - if everything is broken
cd /srv/quantiv-data

# 1. Restore from backup
cp backups/quantiv_YYYYMMDD.duckdb quantiv.duckdb

# 2. Recreate all views and tables
python3 scripts/setup_duckdb_views.py
python3 scripts/setup_earnings_calendar.py
python3 scripts/build_em_labels_features.py

# 3. Restore models
tar -xzf backups/models_YYYYMMDD.tar.gz

# 4. Run health check
python3 scripts/data_healthcheck.py
```

### Reset Everything (Nuclear Option)

```bash
# On the VM - complete reset
cd /srv/quantiv-data

# Backup current state
tar -czf emergency_backup_$(date +%Y%m%d_%H%M%S).tar.gz .

# Reset data structure
rm -rf parquet/ duckdb-cache/ quantiv.duckdb models/ outputs/
python3 scripts/setup_data_structure.py

# Restore data from laptop
# (run the daily sync command from laptop)

# Rebuild everything
python3 scripts/setup_ml_pipeline.py
```

## Performance Optimization

### DuckDB Tuning

```sql
-- Run these in DuckDB CLI for better performance
SET memory_limit = '8GB';
SET threads = 8;
SET enable_object_cache = true;
PRAGMA enable_profiling;
```

### Parquet Optimization

```bash
# Check Parquet file sizes
find /srv/quantiv-data/parquet -name "*.parquet" -exec ls -lh {} \; | head -10

# Recompress if files are too large (>100MB)
# This would be done in the postgres_to_parquet.py script
```

## Monitoring Alerts

Set up these checks for alerting:

1. **Data Freshness**: Latest data should be < 2 days old
2. **Prediction Count**: Should have predictions for next 14 days
3. **Disk Usage**: Should stay < 80% full
4. **Model Performance**: MAE should be < 0.15 (15%)
5. **Error Logs**: Check for Python exceptions in logs

## Log Locations

```bash
# Application logs
tail -f /srv/quantiv-data/logs/daily_scoring.log
tail -f /srv/quantiv-data/logs/weekly_health.log
tail -f /srv/quantiv-data/logs/monthly_training.log

# System logs
journalctl -u cron -f
```

This runbook provides all the essential commands for operating the Quantiv ML pipeline in production.
