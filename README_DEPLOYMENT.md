# 🚀 Quantiv - Production Ready

**ML-Powered Options Expected Move Forecasting Platform**

![Status](https://img.shields.io/badge/status-production%20ready-brightgreen)
![ML Models](https://img.shields.io/badge/ML%20models-6%20trained-blue)
![Data](https://img.shields.io/badge/training%20data-32%20months-orange)

---

## 📊 What's Built

### ML Models ✅
- **6 LightGBM models** trained on 32 months of data (2023-2025)
- **15,000 earnings events**, **1.2M options records**
- **Average MAE: 1.31%** (best model: T-14 at 0.51%)
- Models bundled in `apps/backend/data/` (1.6MB)

### Backend API ✅
- **FastAPI** with ML serving pipeline
- **6 endpoints** for forecasts, history, ML predictions
- **Redis caching** for performance
- **Hybrid backend** (PostgreSQL + DuckDB)
- **Production-ready** with health checks

### Frontend ✅
- **Next.js 14** with TypeScript
- **Real-time** expected move calculations
- **ML vs Math** comparison UI
- **Responsive design** with Tailwind CSS

---

## 🚀 Deploy in 15 Minutes

### Quick Start

1. **Fork/Clone this repo**
2. **Follow:** `DEPLOY_NOW.md`
3. **Deploy to:**
   - Backend → Railway (free $5 credit)
   - Frontend → Vercel (free tier)
   - Redis → Upstash (free tier)

### Deployment Files Ready

```
✅ apps/frontend/vercel.json       - Vercel configuration
✅ apps/backend/railway.json       - Railway configuration
✅ apps/backend/Procfile           - Start command
✅ apps/backend/runtime.txt        - Python 3.12
✅ apps/backend/prepare_deploy.sh  - Bundle ML models
✅ apps/backend/data/models/       - 6 trained models + metadata
✅ DEPLOY_NOW.md                   - 15-minute deployment guide
✅ DEPLOYMENT_GUIDE.md             - Comprehensive documentation
✅ DEPLOYMENT_CHECKLIST.md         - Step-by-step checklist
```

---

## 📁 Project Structure

```
quantiv/
├── apps/
│   ├── backend/          # FastAPI + ML serving
│   │   ├── main.py       # API endpoints
│   │   ├── services/     # ML service, data backends
│   │   ├── data/         # ML models + bias curves
│   │   └── requirements.txt
│   │
│   ├── frontend/         # Next.js app
│   │   ├── app/          # Routes and pages
│   │   ├── components/   # React components
│   │   └── package.json
│   │
│   └── ml/              # ML training pipeline
│       ├── model_trainer.py
│       ├── feature_engineering.py
│       └── run_full_retrain_2023_2025.py
│
├── data/                # Training data (local)
│   ├── models/          # Trained ML models
│   ├── bias_curves.parquet
│   └── quantiv.duckdb
│
├── docs/                # Documentation
│   ├── ML_FINAL_RESULTS.md
│   ├── PRODUCTION_DEPLOYMENT.md
│   └── DATA_STRATEGY.md
│
└── DEPLOY_NOW.md        # 👈 START HERE
```

---

## 🎯 Features

### ML Forecasting
- **6 horizon models**: T-1, T-2, T-3, T-7, T-14, T-21
- **Live predictions** from options chain data
- **Confidence bands** (P10, P50, P90)
- **Bias calibration** with historical data

### API Endpoints
```bash
GET /health                    # Health check
GET /em/ml-info                # ML pipeline status
GET /em/ml-forecast            # ML prediction for symbol
GET /em/forecast               # Latest forecast
GET /em/history                # Historical forecasts
GET /em/expiries               # Available expiration dates
```

### Frontend Features
- Real-time ticker search
- Expected move visualization
- ML vs Math comparison
- Earnings calendar integration
- Responsive mobile design

---

## 🔧 Technology Stack

**Backend:**
- FastAPI (Python 3.12)
- LightGBM (ML models)
- DuckDB (feature engineering)
- PostgreSQL (data storage)
- Redis (caching)

**Frontend:**
- Next.js 14
- React 18
- TypeScript
- Tailwind CSS
- TanStack Query

**Deployment:**
- Railway (backend hosting)
- Vercel (frontend hosting)
- Upstash (Redis)
- Vercel Postgres (database)

---

## 📈 Performance

### ML Models (Validation Set)
| Model | MAE | RMSE | Samples | Use Case |
|-------|-----|------|---------|----------|
| T-14 | 0.51% | 0.72% | ~9,000 | 2 weeks before |
| T-3 | 1.19% | 2.09% | ~7,000 | 3 days before |
| T-21 | 1.18% | 2.09% | ~9,500 | 3 weeks before |
| T-7 | 1.23% | 1.90% | ~9,500 | 1 week before |
| T-2 | 1.79% | 2.80% | ~9,000 | 2 days before |
| T-1 | 1.98% | 3.10% | ~10,000 | 1 day before |

**Overall Average: 1.31% MAE**

### API Performance
- Response time: <200ms (cached)
- Response time: <1s (uncached ML)
- Cache hit rate: ~80%
- Uptime: 99.9% target

---

## 🚀 Deployment Options

### Option 1: Railway + Vercel (Recommended)
- **Cost:** ~$5-10/month
- **Setup:** 15 minutes
- **Best for:** Production use

### Option 2: Render + Vercel
- **Cost:** Free tier available
- **Setup:** 20 minutes
- **Best for:** Testing/Demo

### Option 3: Docker Compose (Local)
- **Cost:** Free
- **Setup:** 5 minutes
- **Best for:** Development

```bash
# Local development
docker-compose up
# Frontend: http://localhost:3001
# Backend: http://localhost:8000
```

---

## 📚 Documentation

- **[DEPLOY_NOW.md](DEPLOY_NOW.md)** - Quick 15-minute deployment guide
- **[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)** - Comprehensive deployment docs
- **[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)** - Step-by-step checklist
- **[ML_FINAL_RESULTS.md](docs/ML_FINAL_RESULTS.md)** - Model performance details
- **[DATA_STRATEGY.md](docs/DATA_STRATEGY.md)** - Training data strategy

---

## 🔐 Environment Variables

### Backend (Railway)
```bash
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
POLYGON_API_KEY=your_key
FMP_API_KEY=your_key
DATA_BACKEND=hybrid
DATA_DIR=/app/data
FRONTEND_URL=https://quantiv.vercel.app
```

### Frontend (Vercel)
```bash
NEXT_PUBLIC_API_URL=https://your-backend.railway.app
NEXT_PUBLIC_APP_URL=https://quantiv.vercel.app
```

See `.env.example` for full list.

---

## 🧪 Testing

### Local Testing
```bash
# Backend
cd apps/backend
uvicorn main:app --reload
curl http://localhost:8000/health

# Frontend
cd apps/frontend
npm run dev
# Visit http://localhost:3001
```

### Production Testing
```bash
# Health check
curl https://your-backend.railway.app/health

# ML models status
curl https://your-backend.railway.app/em/ml-info

# Test forecast
curl "https://your-backend.railway.app/em/ml-forecast?symbol=AAPL&earnings_date=2025-01-30"
```

---

## 🔄 Retraining Models

Models should be retrained monthly with new data:

```bash
cd apps/ml
python run_full_retrain_2023_2025.py --n-trials 50

# Copy new models to backend
cp models/*.joblib ../backend/data/models/

# Redeploy backend
railway up
```

---

## 📊 Monitoring

### Railway Dashboard
- CPU/Memory usage
- Request rate
- Error logs
- Deployment history

### Vercel Analytics
- Page views
- Core Web Vitals
- API response times
- Geographic distribution

---

## 🛠️ Maintenance

### Daily
- Check error logs
- Monitor API response times

### Weekly
- Review ML model accuracy
- Check database size
- Analyze cache hit rates

### Monthly
- Retrain ML models with new data
- Update dependencies
- Review and optimize queries

---

## 💰 Cost Estimate

### Free Tier (Development)
- Railway: $5 free credit
- Vercel: Free (Hobby)
- Upstash: Free tier (10K req/day)
- **Total: $0/month**

### Production
- Railway: $10/month (backend + DB)
- Vercel: $20/month (Pro)
- Upstash: $10/month
- **Total: ~$40/month**

---

## 🤝 Contributing

This is a production-ready system. To contribute:
1. Fork the repository
2. Create feature branch
3. Test locally with docker-compose
4. Submit pull request

---

## 📝 License

Proprietary - All rights reserved

---

## 🎉 Ready to Deploy?

1. **Start here:** Read `DEPLOY_NOW.md`
2. **Use checklist:** `DEPLOYMENT_CHECKLIST.md`
3. **Need help?** See `DEPLOYMENT_GUIDE.md`

Your ML-powered options platform will be live in ~15 minutes! 🚀

---

**Built with:** FastAPI • Next.js • LightGBM • DuckDB • PostgreSQL • Redis

**Status:** ✅ Production Ready

**Version:** 2.0 (October 2025)
