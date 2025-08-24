# ML Pipeline Dockerfile for Quantiv
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PARQUET_ROOT=/app/data/parquet

WORKDIR /app

# LightGBM runtime dependency and build tools for optional wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy pipeline code
COPY pipeline.py /app/pipeline.py

CMD ["python", "/app/pipeline.py"]
