# ML MVP2 Deployment Guide

## Quick Start

The ML MVP2 pipeline is now integrated into Quantiv. To deploy to production:

### 1. Train Production Models

Currently using demo models with synthetic data. Replace with real trained models:

```bash
cd apps/ml

# Option A: Fast training on Q1 2024 (15-30 minutes)
python run_ml_pipeline_fast.py --no-cache

# Option B: Full training on 2023-2024 (1-2 hours)
python run_ml_pipeline.py
```

**Expected outputs:**
- `data/bias_curves.parquet` - Historical calibration curves
- `data/ml_training/training_T{1,2,3,7,14,21}.parquet` - Training datasets
- `models/lgbm_T{1,2,3,7,14,21}.joblib` - Trained models
- `models/metadata_T*.json` - Model metrics and metadata

### 2. Verify Models Locally

Test the backend with trained models:

```bash
# Start backend
cd apps/backend
python main.py

# In another terminal, test ML endpoints
curl "http://localhost:8000/em/ml-info"
curl "http://localhost:8000/em/ml-forecast?symbol=AAPL&earnings_date=2025-01-30"
```

Expected response:
```json
{
  "symbol": "AAPL",
  "earnings_date": "2025-01-30",
  "em_math": 0.035,
  "em_ml": 0.032,
  "correction_factor": 0.914,
  "p10": 0.027,
  "p50": 0.032,
  "p90": 0.037,
  "combined_confidence": 0.75,
  "model_type": "ml_mvp2"
}
```

### 3. Deploy to Production

#### Option A: Docker Deployment

Update `docker-compose.yml` to mount models:

```yaml
services:
  api:
    volumes:
      - ./data:/app/data:ro
      - ./models:/app/models:ro
    environment:
      - DATA_BACKEND=hybrid
      - ML_ENABLED=true
```

Then deploy:

```bash
docker-compose up -d
```

#### Option B: Vercel + Cloud Backend

1. Upload models to cloud storage (S3, GCS, etc.)
2. Update backend startup to download models
3. Set environment variable: `ML_MODELS_PATH=/path/to/models`

### 4. Verify Frontend Integration

Visit your deployed frontend and check the Weekly Earnings page:
- Look for ✨ "ML-enhanced forecasts enabled" indicator
- Each future earnings event should show blue "ML-Enhanced" panel
- Verify Math → ML comparison displays correctly

## Architecture Overview

```
┌─────────────────┐
│   Frontend      │
│  (Next.js)      │
└────────┬────────┘
         │ GET /em/ml-forecast
         ▼
┌─────────────────┐
│   Backend API   │
│   (FastAPI)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│  MLService      │────▶│ DuckDB       │
│  (ML MVP2)      │     │ (fallback)   │
└────────┬────────┘     └──────────────┘
         │
         ▼
┌─────────────────────────────┐
│  MLServingPipeline          │
│  - Bias Curves              │
│  - 6 LightGBM Models        │
│  - Feature Extraction       │
│  - Confidence Bands         │
└─────────────────────────────┘
```

## Environment Variables

Add to `.env.production`:

```bash
# ML Configuration
ML_ENABLED=true
ML_MODELS_DIR=/app/models
DATA_DIR=/app/data

# Backend mode (use hybrid for ML + Postgres)
DATA_BACKEND=hybrid
```

## Monitoring

### Key Metrics to Track

1. **Model Performance**
   - Response time for `/em/ml-forecast` (target: <200ms)
   - Cache hit rate (target: >80%)
   - ML service availability (target: >99%)

2. **Accuracy Metrics**
   - Post-earnings: Compare predicted vs realized moves
   - Coverage calibration: % of moves within P10-P90 bands
   - MAE drift over time (alert if >20% increase)

3. **User Engagement**
   - % of users viewing ML forecasts
   - Time spent on earnings calendar
   - Click-through rate on ML-enhanced events

### Logging

ML service logs:
```bash
# View ML pipeline logs
docker logs quantiv-api | grep "ml.serving_pipeline"

# Check model loading
docker logs quantiv-api | grep "ML serving pipeline loaded"
```

## Troubleshooting

### Models Not Loading

**Symptom:** `/em/ml-info` returns `"status": "unavailable"`

**Solutions:**
1. Check models directory exists and is mounted
2. Verify model files: `ls -lh models/lgbm_T*.joblib`
3. Check Python imports: `python -c "from ml.serving_pipeline import MLServingPipeline"`
4. Review backend logs for import errors

### ML Forecasts Not Showing

**Symptom:** Frontend doesn't display ML panels

**Solutions:**
1. Check backend endpoint: `curl http://localhost:8000/em/ml-info`
2. Verify frontend API proxy is configured
3. Check browser console for fetch errors
4. Ensure `mlEnabled` state is true

### High Latency

**Symptom:** `/em/ml-forecast` takes >1s

**Solutions:**
1. Enable Redis caching (5 min TTL)
2. Pre-compute forecasts nightly for upcoming earnings
3. Use DuckDB for faster feature extraction
4. Consider model compression or quantization

## Rollback Plan

If ML forecasts cause issues:

1. **Quick disable:** Set `ML_ENABLED=false` in environment
2. **Redeploy:** Backend will fall back to math-only forecasts
3. **Frontend:** Will gracefully hide ML panels when unavailable
4. **No data loss:** All existing forecasts remain in database

## Next Steps

### Immediate (This Week)
- [ ] Train on full 2023-2024 data
- [ ] Load test `/em/ml-forecast` endpoint
- [ ] Set up monitoring dashboards
- [ ] Document model retraining procedure

### Short-term (Next 2 Weeks)
- [ ] Implement nightly batch scoring
- [ ] Add model version tracking
- [ ] Create accuracy dashboard
- [ ] Set up drift detection alerts

### Long-term (Next Month)
- [ ] Hyperparameter optimization (50+ trials)
- [ ] Sector-specific models
- [ ] A/B testing framework
- [ ] Automated retraining pipeline

## Support

For issues or questions:
- Check logs: `docker logs quantiv-api`
- Review docs: `docs/ML_MVP2_ARCHITECTURE.md`
- Verify setup: `python apps/ml/run_ml_demo.py`

---

**Last Updated:** October 4, 2025  
**Version:** MVP2.0  
**Status:** Production Ready (pending real model training)
