# Quantiv FastAPI backend image.
#
# Build context is the repository root. Railway selects this Dockerfile via
# railway.toml. Keep the image runtime-only: compilers, linters, test runners,
# and notebook tooling belong in CI/research environments, not production.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# LightGBM's manylinux wheel needs the OpenMP runtime. asyncpg and
# psycopg2-binary install tested wheels on Python 3.11, so no compiler toolchain
# or libpq development headers are retained in the production image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Backend runtime dependencies first for stable Docker layer caching. This is a
# hash-pinned lock generated from backend requirements.in + the ML serving
# package, so the image never resolves a second dependency graph.
COPY apps/backend/requirements.txt /tmp/backend-requirements.txt
RUN pip install --no-compile --require-hashes -r /tmp/backend-requirements.txt \
    && rm /tmp/backend-requirements.txt

# Install the shared ML package non-editably and explicitly without dependencies:
# every project dependency was already admitted by the lock above. Keep the
# source/artifact directory in /app/apps/ml for the baked-in model fallback until
# model materialization is fully externalized from the image.
COPY apps/ml /app/apps/ml
RUN pip install --no-compile --no-deps /app/apps/ml

# Application code last (changes most often).
COPY apps/backend /app/apps/backend
# Shared security master / quote-worker publication inputs. These remain here
# until the frontend publication corpus is materialized as an explicit release.
COPY lib /app/lib
COPY apps/frontend/public /app/apps/frontend/public
COPY apps/frontend/lib/marketHolidays.generated.ts /app/apps/frontend/lib/marketHolidays.generated.ts

WORKDIR /app/apps/backend

EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
