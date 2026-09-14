# Quantiv FastAPI backend image.
#
# Railway's attached /data volume is owned by root. The service therefore keeps
# an explicit, documented root-runtime exception; see
# docs/RAILWAY_RUNTIME_SECURITY.md. The image remains runtime-only and treats
# /app as immutable application content.
#
# Base-image provenance policy: python:3.11-slim-bookworm is deliberately tied
# to the Python minor + Debian family and is reviewed at least monthly and on
# critical upstream CVEs. See docs/RAILWAY_RUNTIME_SECURITY.md.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TMPDIR=/tmp

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY apps/backend/requirements.txt /tmp/backend-requirements.txt
RUN pip install --no-compile --require-hashes -r /tmp/backend-requirements.txt \
    && rm /tmp/backend-requirements.txt

COPY apps/ml /app/apps/ml
RUN pip install --no-compile --no-deps /app/apps/ml

COPY apps/backend /app/apps/backend
COPY lib /app/lib
COPY config/market_sessions.json /app/config/market_sessions.json

# The only application-owned persistent write target is Railway's /data mount.
# /tmp remains the conventional ephemeral scratch area. Mark source content
# read-only to catch accidental application writes even though the platform UID
# must remain root for the mounted volume.
RUN mkdir -p /data \
    && chmod 0700 /data \
    && chmod -R a-w /app

WORKDIR /app/apps/backend

# Explicit platform exception, rather than silently inheriting Docker's root
# default. Do not change this without removing/re-permissioning the Railway
# volume dependency described in the runtime-security ADR.
USER 0:0

EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
