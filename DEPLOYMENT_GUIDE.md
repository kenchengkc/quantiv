# Quantiv Production Deployment Guide

Complete guide to deploy Quantiv (Next.js + FastAPI + ML) to production.

---

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│   Frontend (Next.js)                        │
│   Platform: Vercel                          │
│   URL: https://quantiv.vercel.app          │
└────────────────┬────────────────────────────┘
                 │ API calls
                 ▼
┌─────────────────────────────────────────────┐
│   Backend (FastAPI + ML Models)            │
│   Platform: Railway or Render              │
│   URL: https://quantiv-api.railway.app     │
└────────────────┬────────────────────────────┘
                 │
    ┌────────────┼────────────┐
    ▼            ▼            ▼
┌─────────┐ ┌─────────┐ ┌─────────┐
│PostgreSQL│ │  Redis  │ │  Data   │
│(Vercel) │ │(Upstash)│ │(Railway)│
└─────────┘ └─────────┘ └─────────┘
```

---

## Step 1: Deploy Backend to Railway

### 1.1 Sign up for Railway
- Go to [railway.app](https://railway.app)
- Sign in with GitHub
- Create new project

### 1.2 Prepare Backend
```bash
cd apps/backend

# Ensure ML models are in the right place
cp -r ../../data/models ./data/models
cp ../../data/bias_curves.parquet ./data/

# Test locally first
uvicorn main:app --reload
```

### 1.3 Deploy to Railway

**Option A: Railway CLI**
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Initialize project
cd apps/backend
railway init

# Deploy
railway up
```

**Option B: GitHub Integration**
1. Push code to GitHub
2. In Railway dashboard, click "New Project" → "Deploy from GitHub repo"
3. Select `quantiv` repository
4. Set root directory to `apps/backend`

### 1.4 Configure Environment Variables in Railway

Add these in Railway dashboard → Variables:

```bash
# Database
DATABASE_URL=postgresql://user:password@host:5432/quantiv_options

# Redis
REDIS_URL=redis://default:password@host:6379

# API Keys
POLYGON_API_KEY=your_polygon_key
FMP_API_KEY=your_fmp_key

# Data paths
DATA_BACKEND=hybrid
DATA_DIR=/app/data
DUCKDB_PATH=/app/data/quantiv.duckdb
ML_MODELS_DIR=/app/data/models

# Python
PYTHONUNBUFFERED=1
```

### 1.5 Add Build Configuration

Railway will auto-detect Python. If needed, customize:

**railway.toml**
```toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "uvicorn main:app --host 0.0.0.0 --port $PORT"
restartPolicyType = "ON_FAILURE"
healthcheckPath = "/health"
```

---

## Step 2: Set Up PostgreSQL

### Option A: Vercel Postgres (Recommended)
```bash
# Install Vercel CLI
npm i -g vercel

# Create Postgres database
vercel postgres create quantiv-db

# Get connection string
vercel env pull
```

### Option B: Supabase (Free tier)
1. Go to [supabase.com](https://supabase.com)
2. Create new project
3. Copy PostgreSQL connection string
4. Run migrations:
```bash
psql $DATABASE_URL < scripts/init_postgres.sql
```

### Option C: Railway Postgres
1. In Railway project, click "+ New"
2. Select "Database" → "PostgreSQL"
3. Copy `DATABASE_URL` to backend environment

---

## Step 3: Set Up Redis (Upstash)

### 3.1 Create Upstash Redis
1. Go to [upstash.com](https://upstash.com)
2. Create account → Create database
3. Select region: US East (iad1) for lowest latency
4. Copy Redis URL

### 3.2 Add to Backend
In Railway environment variables:
```bash
REDIS_URL=redis://default:your_password@redis-host.upstash.io:6379
```

---

## Step 4: Deploy Frontend to Vercel

### 4.1 Prepare Frontend
```bash
cd apps/frontend

# Update API URL in code if hardcoded
# Should use NEXT_PUBLIC_API_URL from env
```

### 4.2 Deploy to Vercel

**Option A: Vercel CLI**
```bash
# Install Vercel CLI
npm i -g vercel

# Deploy
cd apps/frontend
vercel

# Set production
vercel --prod
```

**Option B: GitHub Integration (Recommended)**
1. Push code to GitHub
2. Go to [vercel.com/new](https://vercel.com/new)
3. Import `quantiv` repository
4. Framework Preset: Next.js
5. Root Directory: `apps/frontend`
6. Click "Deploy"

### 4.3 Configure Environment Variables in Vercel

In Vercel Dashboard → Settings → Environment Variables:

```bash
# Production
NEXT_PUBLIC_API_URL=https://quantiv-api.railway.app
NEXT_PUBLIC_APP_URL=https://quantiv.vercel.app

# Environment: Production
```

### 4.4 Add Build Settings

In Vercel project settings:
- **Build Command:** `npm run build`
- **Output Directory:** `.next`
- **Install Command:** `npm install`
- **Root Directory:** `apps/frontend`

---

## Step 5: Data Migration

### 5.1 Upload Data to Backend

**Option A: Include in Docker image (Recommended for Railway)**
```bash
cd apps/backend

# Data already included in repository
# Railway will copy everything during build
```

**Option B: Cloud Storage (for larger datasets)**
```bash
# Upload to S3/R2
aws s3 sync ../../data s3://quantiv-data/

# Update backend to download on startup
```

### 5.2 Verify ML Models
Ensure these files are in `apps/backend/data/models/`:
```
lgbm_T1.joblib (32 KB)
lgbm_T2.joblib (45 KB)
lgbm_T3.joblib (40 KB)
lgbm_T7.joblib (35 KB)
lgbm_T14.joblib (51 KB)
lgbm_T21.joblib (45 KB)
metadata_T*.json (6 files)
```

### 5.3 Upload Bias Curves
Ensure `data/bias_curves.parquet` is present in backend.

---

## Step 6: Database Setup

### 6.1 Run Migrations
```bash
# Connect to production database
psql $DATABASE_URL

# Create tables
\i scripts/init_postgres.sql

# Verify
\dt
```

### 6.2 Seed Initial Data (Optional)
```bash
# Load earnings calendar
psql $DATABASE_URL < scripts/seed_earnings.sql

# Or use Python script
python scripts/load_initial_data.py
```

---

## Step 7: Testing & Verification

### 7.1 Test Backend Health
```bash
curl https://quantiv-api.railway.app/health
# Expected: {"status": "healthy"}

curl https://quantiv-api.railway.app/em/ml-info
# Expected: {"models_loaded": 6, "status": "operational"}
```

### 7.2 Test Frontend
1. Visit https://quantiv.vercel.app
2. Search for a ticker (e.g., AAPL)
3. Verify expected move calculations load
4. Check ML forecasts display

### 7.3 Test ML Endpoints
```bash
curl "https://quantiv-api.railway.app/em/ml-forecast?symbol=AAPL&earnings_date=2025-01-30"
```

---

## Step 8: Domain Setup (Optional)

### 8.1 Frontend Domain
In Vercel Dashboard → Settings → Domains:
- Add custom domain: `quantiv.app`
- Update DNS records as instructed
- SSL automatically provisioned

### 8.2 Backend Domain
In Railway Dashboard → Settings:
- Add custom domain: `api.quantiv.app`
- Update DNS CNAME record
- SSL automatically provisioned

### 8.3 Update Environment Variables
```bash
# In Vercel
NEXT_PUBLIC_API_URL=https://api.quantiv.app
NEXT_PUBLIC_APP_URL=https://quantiv.app
```

---

## Troubleshooting

### Backend not starting
**Check Railway logs:**
```bash
railway logs
```

**Common issues:**
- Missing environment variables
- ML models not found (check DATA_DIR path)
- Database connection failed
- Redis connection failed

**Fix:**
1. Verify all environment variables set
2. Check `DATA_DIR=/app/data` matches actual path
3. Test database connection string
4. Ensure ML models copied to backend

### Frontend API calls failing
**Check browser console:**
- CORS errors → Add frontend domain to backend CORS origins
- 404 errors → Verify `NEXT_PUBLIC_API_URL` is correct
- Timeout → Check backend is running and accessible

**Fix in backend main.py:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://quantiv.vercel.app",
        "https://quantiv.app",  # your custom domain
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### ML models not loading
**Check backend logs for:**
```
INFO: Loaded T-1 model
INFO: Loaded 6 ML models
```

**If missing:**
1. Verify models in `apps/backend/data/models/`
2. Check `ML_MODELS_DIR` environment variable
3. Ensure models are included in deployment

---

## Cost Estimates

### Free Tier (Development)
- **Vercel:** Free (Hobby plan)
- **Railway:** $5/month free credit
- **Upstash Redis:** Free tier (10K requests/day)
- **Vercel Postgres:** Free tier (256 MB storage)

**Total:** ~$0-5/month

### Production (Scaling)
- **Vercel:** $20/month (Pro plan)
- **Railway:** $10-50/month (depends on usage)
- **Upstash Redis:** $10/month (1GB, 1M requests/day)
- **Database:** $20/month (managed Postgres)

**Total:** ~$60-100/month

---

## Performance Optimization

### 1. Enable Caching
Already configured in backend:
- Redis cache: 5-minute TTL
- Response caching for `/em/forecast`

### 2. Database Indexes
```sql
CREATE INDEX idx_em_forecasts_symbol ON em_forecasts(act_symbol);
CREATE INDEX idx_em_forecasts_date ON em_forecasts(quote_ts);
```

### 3. CDN Configuration
Vercel automatically uses edge network for frontend.

### 4. Backend Scaling
Railway auto-scales. For manual control:
- Dashboard → Settings → Resources
- Increase memory if ML models need more RAM (recommend 2GB)

---

## Monitoring

### 1. Backend Health
Use Railway metrics dashboard:
- CPU usage
- Memory usage
- Request rate
- Error rate

### 2. Frontend Performance
Vercel Analytics:
- Core Web Vitals
- Page load times
- API response times

### 3. Custom Monitoring
Add to backend:
```python
# Sentry for error tracking
import sentry_sdk
sentry_sdk.init(dsn="your_sentry_dsn")

# Prometheus for metrics
from prometheus_client import Counter
ml_requests = Counter('ml_requests_total', 'Total ML requests')
```

---

## Maintenance

### Monthly Tasks
1. Review Railway usage and costs
2. Check error logs in Railway/Vercel
3. Monitor ML model accuracy
4. Update dependencies

### Retraining ML Models
```bash
# Locally
cd apps/ml
python run_full_retrain_2023_2025.py

# Copy new models to backend
cp models/*.joblib ../backend/data/models/

# Redeploy backend
railway up
```

### Database Backups
Railway Postgres auto-backups. To manually backup:
```bash
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql
```

---

## Security Checklist

- [ ] Environment variables not committed to git
- [ ] API keys stored in Railway/Vercel secrets
- [ ] CORS restricted to production domain
- [ ] Database uses SSL connections
- [ ] Redis requires authentication
- [ ] Sensitive endpoints require auth (if applicable)
- [ ] Rate limiting enabled
- [ ] HTTPS enforced on all endpoints

---

## Deployment Checklist

### Pre-deployment
- [ ] Test locally with production-like environment
- [ ] ML models trained and saved
- [ ] Database migrations ready
- [ ] Environment variables documented
- [ ] Frontend builds successfully
- [ ] Backend tests pass

### Deployment
- [ ] PostgreSQL database created
- [ ] Redis instance created
- [ ] Backend deployed to Railway
- [ ] Environment variables configured
- [ ] ML models accessible
- [ ] Database migrations run
- [ ] Frontend deployed to Vercel
- [ ] Environment variables configured
- [ ] Custom domains configured (optional)

### Post-deployment
- [ ] Health check passes
- [ ] ML models loaded successfully
- [ ] Frontend can reach backend
- [ ] Test key user flows
- [ ] Monitor errors for 24 hours
- [ ] Set up alerts and monitoring

---

## Quick Reference

### Railway Commands
```bash
railway login
railway link
railway up
railway logs
railway run 'uvicorn main:app'
```

### Vercel Commands
```bash
vercel login
vercel link
vercel
vercel --prod
vercel logs
```

### Database
```bash
# Connect
psql $DATABASE_URL

# Backup
pg_dump $DATABASE_URL > backup.sql

# Restore
psql $DATABASE_URL < backup.sql
```

---

## Support Resources

- **Railway:** https://docs.railway.app
- **Vercel:** https://vercel.com/docs
- **Upstash:** https://docs.upstash.com/redis
- **Next.js:** https://nextjs.org/docs
- **FastAPI:** https://fastapi.tiangolo.com

---

**Last Updated:** October 9, 2025  
**Version:** 1.0  
**Status:** Production Ready ✅
