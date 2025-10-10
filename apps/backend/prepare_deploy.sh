#!/bin/bash
# Prepare backend for deployment by copying ML models and data

set -e

echo "🚀 Preparing Quantiv backend for deployment..."

# Create data directory structure
mkdir -p data/models

# Copy ML models
echo "📦 Copying ML models..."
if [ -d "../../data/models" ]; then
    cp ../../data/models/*.joblib data/models/ 2>/dev/null || echo "No .joblib files found"
    cp ../../data/models/*.json data/models/ 2>/dev/null || echo "No .json files found"
    echo "✅ Copied ML models"
else
    echo "⚠️  Warning: ../../data/models not found"
fi

# Copy bias curves
echo "📊 Copying bias curves..."
if [ -f "../../data/bias_curves.parquet" ]; then
    cp ../../data/bias_curves.parquet data/
    echo "✅ Copied bias curves"
else
    echo "⚠️  Warning: ../../data/bias_curves.parquet not found"
fi

# Copy DuckDB if exists
if [ -f "../../data/quantiv.duckdb" ]; then
    echo "🦆 Copying DuckDB..."
    cp ../../data/quantiv.duckdb data/
    echo "✅ Copied DuckDB"
fi

# List what we have
echo ""
echo "📁 Deployment package contents:"
echo "Models:"
ls -lh data/models/ 2>/dev/null || echo "  No models directory"
echo ""
echo "Data files:"
ls -lh data/*.parquet 2>/dev/null || echo "  No parquet files"
echo ""

# Check file sizes
TOTAL_SIZE=$(du -sh data/ 2>/dev/null | cut -f1)
echo "Total data size: $TOTAL_SIZE"

if [ "$TOTAL_SIZE" \> "500M" ]; then
    echo "⚠️  Warning: Data directory is >500MB. Consider using cloud storage for models."
fi

echo ""
echo "✅ Backend ready for deployment!"
echo ""
echo "Next steps:"
echo "1. Railway: railway up (from apps/backend)"
echo "2. Render: git push (if connected)"
echo "3. Manual: zip and upload to hosting platform"
