# ✅ Quantiv Deployment Checklist

Use this checklist to track your deployment progress.

---

## Pre-Deployment (✅ DONE)

- [x] ML models trained on full 2023-2025 dataset
- [x] All 6 models optimized and saved (248KB total)
- [x] Bias curves generated and saved (3.3KB)
- [x] Backend CORS configured for production
- [x] Frontend environment variables configured
- [x] Deployment scripts created
- [x] ML models bundled in backend (1.6MB total)
- [x] Documentation written (2 comprehensive guides)
- [x] Code committed to git

---

## Backend Deployment

### Railway Setup
- [ ] Sign up for Railway account (https://railway.app)
- [ ] Create new project from GitHub
- [ ] Set root directory to `apps/backend`
- [ ] Add PostgreSQL database to project
- [ ] Copy `DATABASE_URL` connection string

### Backend Environment Variables
Copy these to Railway → Variables tab:

```bash
DATABASE_URL=postgresql://...  # From Railway Postgres
REDIS_URL=redis://...          # From Upstash (next step)
POLYGON_API_KEY=kR5m4Rm0CnSJBqOwtsZ6xPkSu6pDA0iX
FMP_API_KEY=PAiqgIUMN0lx3YWPKphcSg9huH1hCOeS
DATA_BACKEND=hybrid
DATA_DIR=/app/data
DUCKDB_PATH=/app/data/quantiv.duckdb
FRONTEND_URL=https://quantiv.vercel.app
PYTHONUNBUFFERED=1
```

- [ ] All environment variables added
- [ ] Backend deployed successfully
- [ ] Health check passes: `/health`
- [ ] ML info endpoint works: `/em/ml-info`

---

## Redis Setup (Upstash)

- [ ] Sign up for Upstash (https://console.upstash.com)
- [ ] Create Redis database (Free tier)
- [ ] Select region: US East (Virginia)
- [ ] Copy Redis URL
- [ ] Add `REDIS_URL` to Railway backend variables

---

## Frontend Deployment (Vercel)

### Vercel Setup
- [ ] Sign up for Vercel account (https://vercel.com)
- [ ] Import GitHub repository
- [ ] Set framework: Next.js
- [ ] Set root directory: `apps/frontend`

### Frontend Environment Variables
Add these in Vercel → Settings → Environment Variables:

```bash
NEXT_PUBLIC_API_URL=https://YOUR-BACKEND.up.railway.app
NEXT_PUBLIC_APP_URL=https://YOUR-APP.vercel.app
```

**Important:** Replace with your actual URLs after deployment!

- [ ] Environment variables added
- [ ] Frontend deployed successfully
- [ ] Can access at Vercel URL
- [ ] Can search for tickers
- [ ] Expected moves load correctly

---

## Post-Deployment Testing

### Backend Tests
```bash
# Health check
curl https://YOUR-BACKEND.up.railway.app/health
# Expected: {"status":"healthy"}

# ML models loaded
curl https://YOUR-BACKEND.up.railway.app/em/ml-info
# Expected: {"models_loaded": 6, "status": "operational"}

# Test ML forecast
curl "https://YOUR-BACKEND.up.railway.app/em/ml-forecast?symbol=AAPL&earnings_date=2025-01-30"
# Expected: JSON with prediction
```

- [ ] Health check returns healthy
- [ ] ML info shows 6 models loaded
- [ ] ML forecast returns predictions
- [ ] No errors in Railway logs

### Frontend Tests
- [ ] Homepage loads
- [ ] Can search for AAPL
- [ ] Expected move displays
- [ ] ML forecasts display
- [ ] No console errors
- [ ] API calls succeed (check Network tab)

---

## Final Configuration

### Update CORS
After Vercel deployment, update Railway backend:
- [ ] Add actual Vercel URL to `FRONTEND_URL` variable
- [ ] Redeploy backend if needed

### Custom Domain (Optional)
- [ ] Buy domain (e.g., quantiv.app)
- [ ] Add to Vercel in Settings → Domains
- [ ] Add to Railway in Settings → Domains
- [ ] Update environment variables with custom domains
- [ ] SSL certificates provisioned

---

## Monitoring & Maintenance

- [ ] Set up Sentry for error tracking
- [ ] Configure Uptime monitoring (UptimeRobot)
- [ ] Set up alerts for API downtime
- [ ] Schedule monthly model retraining
- [ ] Monitor database size and costs

---

## Deployment URLs

Record your URLs here for reference:

**Backend (Railway):**
- URL: `_________________________________`
- Health: `/health`
- ML Info: `/em/ml-info`

**Frontend (Vercel):**
- URL: `_________________________________`
- App: `https://your-app.vercel.app`

**Database (Railway Postgres):**
- Connection: `postgresql://...`

**Redis (Upstash):**
- Connection: `redis://...`

---

## Estimated Time

- ⏱️ Railway backend setup: 5-10 minutes
- ⏱️ Upstash Redis setup: 2 minutes
- ⏱️ Vercel frontend setup: 5 minutes
- ⏱️ Testing and verification: 5 minutes

**Total: ~20 minutes**

---

## Getting Help

If you get stuck:

1. **Check logs:**
   - Railway: Dashboard → Deployments → View Logs
   - Vercel: Dashboard → Deployments → View Function Logs

2. **Common issues:**
   - See `DEPLOYMENT_GUIDE.md` troubleshooting section
   - Check `DEPLOY_NOW.md` for quick fixes

3. **Documentation:**
   - Railway: https://docs.railway.app
   - Vercel: https://vercel.com/docs

---

## ✅ Deployment Complete!

Once all items are checked:
- [ ] Backend deployed and healthy
- [ ] Frontend deployed and accessible
- [ ] Database connected and working
- [ ] Redis connected and caching
- [ ] ML models loaded and serving
- [ ] All tests passing
- [ ] No critical errors in logs

**Your Quantiv app is live! 🎉**

Next: Share your URL and start monitoring performance!
