#!/usr/bin/env bash
# Regenerate every committed Python lock surface with the pinned resolver.

set -euo pipefail

UV_VERSION="${UV_VERSION:-0.12.10}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"

python -m pip install --disable-pip-version-check --quiet "uv==${UV_VERSION}"

# Scheduled refresh/retrain environment: pipeline inputs plus the installable ML
# package's production and training dependencies.
uv pip compile \
  requirements.in \
  apps/ml/pyproject.toml \
  --extra train \
  --python-version "$PYTHON_VERSION" \
  --generate-hashes \
  --output-file requirements.txt

# Railway API: backend runtime plus the ML serving package. Docker installs this
# lock, then installs apps/ml with --no-deps so nothing can be re-resolved later.
uv pip compile \
  apps/backend/requirements.in \
  apps/ml/pyproject.toml \
  --python-version "$PYTHON_VERSION" \
  --generate-hashes \
  --output-file apps/backend/requirements.txt

# ML developer/research environment. This is not installed into production.
uv pip compile \
  apps/ml/pyproject.toml \
  --all-extras \
  --python-version "$PYTHON_VERSION" \
  --generate-hashes \
  --output-file apps/ml/requirements.txt

echo "Python dependency locks regenerated with uv ${UV_VERSION} for Python ${PYTHON_VERSION}."
