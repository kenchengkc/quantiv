# 🚀 Deploy Quantiv to Production - Quick Start

Follow these steps to deploy Quantiv in ~15 minutes.

---

## Prerequisites

- GitHub account
- Railway account (free) - https://railway.app
- Vercel account (free) - https://vercel.com
- Upstash account (free) - https://upstash.com

---

## Step 1: Push to GitHub (if not already)

```bash
cd /Users/ken/Desktop/quantiv

# Initialize git if needed
git init
git add .
git commit -m "Prepare for production deployment"

# Create repo on GitHub, then:
git remote add origin https://github.com/YOUR_USERNAME/quantiv.git
git branch -M main
git push -u origin main
```

---

## Step 2: Deploy Backend to Railway

### 2.1 Create Railway Project
1. Go to https://railway.app/new
2. Click **"Deploy from GitHub repo"**
3. Select your `quantiv` repository
4. Click **"Deploy Now"**

### 2.2 Configure Service
- **Service Name:** quantiv-api
- **Root Directory:** `apps/backend`
- **Start Command:** (auto-detected from Procfile)

### 2.3 Add Environment Variables

In Railway Dashboard → Variables tab, add:

```bash
# Database (we'll create this next)
DATABASE_URL=postgresql://...

# Redis (we'll create this next)
REDIS_URL=redis://...

# API Keys
POLYGON_API_KEY=kR5m4Rm0CnSJBqOwtsZ6xPkSu6pDA0iX
FMP_API_KEY=PAiqgIUMN0lx3YWPKphcSg9huH1hCOeS

# Data paths
DATA_BACKEND=hybrid
DATA_DIR=/app/data
DUCKDB_PATH=/app/data/quantiv.duckdb

# Frontend URL (for CORS)
FRONTEND_URL=https://quantiv.vercel.app

# Python
PYTHONUNBUFFERED=1
```

### 2.4 Add PostgreSQL Database
1. In same Railway project, click **"+ New"**
2. Select **"Database" → "PostgreSQL"**
3. Copy the `DATABASE_URL` connection string
4. Paste it into backend environment variables

### 2.5 Deploy
- Railway will automatically deploy
- Get your backend URL from Settings → Domains
- Should be: `https://quantiv-api-production.up.railway.app`

---

## Step 3: Set Up Redis (Upstash)

1. Go to https://console.upstash.com
2. Create account → **"Create Database"**
3. Name: `quantiv-redis`
4. Region: **US East (Virginia)** - closest to Railway
5. Type: **Free**
6. Copy **Redis URL** (looks like: `redis://default:abc123@us1-xxx.upstash.io:6379`)
7. Add to Railway backend environment variables as `REDIS_URL`

---

## Step 4: Deploy Frontend to Vercel

### 4.1 Import Project
1. Go to https://vercel.com/new
2. Click **"Import Git Repository"**
3. Select your `quantiv` repository
4. Configure:
   - **Framework Preset:** Next.js
   - **Root Directory:** `apps/frontend`
   - **Build Command:** `npm run build` (auto-detected)
   - **Output Directory:** `.next` (auto-detected)

### 4.2 Add Environment Variables

Click **"Environment Variables"** and add:

```bash
NEXT_PUBLIC_API_URL=https://quantiv-api-production.up.railway.app
NEXT_PUBLIC_APP_URL=https://quantiv.vercel.app
```

**Note:** Replace with your actual Railway backend URL!

### 4.3 Deploy
- Click **"Deploy"**
- Wait ~2 minutes
- Your app will be live at `https://quantiv-XXXXX.vercel.app`

### 4.4 Update Backend CORS
Go back to Railway → backend environment variables:
- Update `FRONTEND_URL` with your actual Vercel URL

---

## Step 5: Test Production Deployment

### 5.1 Test Backend Health
```bash
curl https://your-railway-url.up.railway.app/health
# Should return: {"status": "healthy"}
```

### 5.2 Test ML Models
```bash
curl https://your-railway-url.up.railway.app/em/ml-info
# Should return: {"models_loaded": 6, "status": "operational"}
```

### 5.3 Test Frontend
1. Open your Vercel URL in browser
2. Search for a ticker (AAPL, MSFT, etc.)
3. Verify data loads
4. Check browser console for errors

---

## Step 6: Custom Domain (Optional)

### Frontend Domain (Vercel)
1. Buy domain (e.g., `quantiv.app` on Namecheap)
2. In Vercel → Settings → Domains
3. Add your domain
4. Update DNS records as instructed

### Backend Domain (Railway)
1. In Railway → Settings → Domains
2. Add custom domain: `api.quantiv.app`
3. Update DNS with CNAME record

### Update Environment Variables
```bash
# Vercel frontend
NEXT_PUBLIC_API_URL=https://api.quantiv.app
NEXT_PUBLIC_APP_URL=https://quantiv.app

# Railway backend
FRONTEND_URL=https://quantiv.app
```

---

## Troubleshooting

### Backend Won't Start
**Check Railway logs:**
- Click "Deployments" → Latest deployment → "View Logs"

**Common issues:**
- Missing environment variables
- Database connection failed
- ML models not found

**Fix:**
1. Verify all env vars are set
2. Check `DATABASE_URL` format
3. Ensure `DATA_DIR=/app/data` matches code

### Frontend API Calls Failing
**Check browser console:**
- CORS errors → Add frontend URL to backend `FRONTEND_URL`
- 404 errors → Verify `NEXT_PUBLIC_API_URL` is correct

**Fix backend CORS:**
Railway → Variables → Update `FRONTEND_URL` to match Vercel domain

### ML Models Not Loading
**Check Railway logs for:**
```
INFO: Loaded T-1 model
INFO: Loaded 6 ML models
```

**If missing:**
- Models should be in `apps/backend/data/models/`
- Run `./prepare_deploy.sh` before deploying
- Check Railway build logs to see if files were copied

---

## Cost Summary

### Free Tier (First Month)
- ✅ Railway: $5 free credit
- ✅ Vercel: Unlimited deployments (Hobby plan)
- ✅ Upstash: 10K requests/day free
- ✅ Railway Postgres: Included in project

**Total: $0** (first month using credits)

### After Free Credits
- Railway: ~$5-10/month (backend + database)
- Vercel: Free (unless high traffic)
- Upstash: Free tier sufficient

**Total: ~$5-10/month**

---

## Next Steps After Deployment

1. **Monitor Performance**
   - Railway metrics dashboard
   - Vercel analytics
   - Check error logs daily

2. **Set Up Monitoring**
   - Add Sentry for error tracking
   - Set up Uptime monitoring (e.g., UptimeRobot)

3. **Regular Maintenance**
   - Retrain ML models monthly
   - Update dependencies quarterly
   - Monitor database size

4. **Scale as Needed**
   - Railway: Increase resources in settings
   - Vercel: Upgrade to Pro if needed
   - Database: Migrate to managed service if grows large

---

## Quick Command Reference

```bash
# Deploy backend (Railway CLI)
cd apps/backend
railway up

# Deploy frontend (Vercel CLI)
cd apps/frontend
vercel --prod

# Check logs
railway logs
vercel logs

# Test endpoints
curl https://your-backend.railway.app/health
curl https://your-backend.railway.app/em/ml-info
```

---

## Support

- **Railway Docs:** https://docs.railway.app
- **Vercel Docs:** https://vercel.com/docs
- **Deployment Guide:** See `DEPLOYMENT_GUIDE.md` for detailed instructions

---

**Ready to deploy?** Start with Step 1! 🚀
