# Quantiv FastAPI backend image.
#
# Build context is the REPO ROOT (not apps/backend/) so we can pip-install
# apps/ml as an editable package alongside the backend's requirements.
# Railway is configured to build this Dockerfile from repo root via
# railway.toml (`dockerfilePath = "apps/backend/Dockerfile"`).
#
# Local build (for parity with Railway):
#   docker build -f apps/backend/Dockerfile -t quantiv-backend .
#
# Run:
#   docker run -p 8000:8000 \
#     -e DATABASE_URL=... -e REDIS_URL=... -e BACKEND_SHARED_SECRET=... \
#     quantiv-backend

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps:
#   build-essential, gcc, libpq-dev → asyncpg / psycopg-binary wheels
#   libgomp1                        → LightGBM runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libgomp1 \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 1) Backend Python deps. Layer this first so a code-only change doesn't
#    invalidate the pip-install cache.
COPY apps/backend/requirements.txt /tmp/backend-requirements.txt
RUN pip install -r /tmp/backend-requirements.txt

# 2) ML serving package. Installed editable so the native LightGBM artifact
#    loader and bundle-verification modules are importable without path hacks.
COPY apps/ml /app/apps/ml
RUN pip install -e /app/apps/ml

# 3) Backend code last (changes most often).
COPY apps/backend /app/apps/backend
# lib/ holds the SP500 JSON shared between Next.js + the backend's sync
# scripts. The quote worker also reads frontend public week files and generated
# market holidays so it can rank earnings reporters without calling Vercel.
COPY lib /app/lib
COPY apps/frontend/public /app/apps/frontend/public
COPY apps/frontend/lib/marketHolidays.generated.ts /app/apps/frontend/lib/marketHolidays.generated.ts

WORKDIR /app/apps/backend

EXPOSE 8000
# Run from /app/apps/backend so `import main` resolves the FastAPI app.
# Railway sets $PORT; default to 8000 for local docker runs.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
