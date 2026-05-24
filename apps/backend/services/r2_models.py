"""Pull ML model files from Cloudflare R2 to the local Railway volume.

The weekly LightGBM retrain in `.github/workflows/daily-refresh.yml`
writes fresh `lgbm_T*.joblib` + quantile heads + metadata to R2 under
`models/`. Without this sync, the Railway image keeps serving whatever
models were baked into it at build time, and the on-demand re-inference
contract returns stale predictions after every retrain.

Flow:
1. On backend startup (`lifespan`), call `sync_models_from_r2(/data/models)`.
   - Pulls every `*.joblib` / `*.json` under `models/` in the R2 bucket.
   - Skips files whose local copy already has the right size (cheap
     freshness check; LightGBM joblibs are deterministic given the same
     training data, so size collisions on stale-but-equal files are not a
     concern).
2. If the sync wrote any files (or the volume already has models from a
   previous successful sync), main.py sets `ML_MODELS_DIR` to
   `/data/models` so `predict_service._models_dir()` resolves there.
3. If R2 is unconfigured or unreachable, `predict_service` falls back to
   the image-baked models at `/app/apps/ml/models/`. The service still
   answers — just with whatever the last image build shipped.

There's an `/api/admin/sync-models` endpoint that re-runs this without a
deploy; useful right after the Sunday retrain lands fresh models in R2.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Where in the R2 bucket the daily-refresh + weekly retrain push models.
# Matches `scripts/r2_push.sh` / `scripts/r2_pull.sh` layout (`models/`).
R2_MODEL_PREFIX = "models/"

# Only mirror the file kinds predict_service actually loads. Skipping
# experimental or oversized artifacts the trainer might dump alongside.
_KEEP_SUFFIXES = (".joblib", ".json")


def _build_client():
    """Lazy-build a boto3 S3 client pointed at R2. Returns None if either
    boto3 isn't installed or any required credential is missing — the
    caller treats that as "not configured" and proceeds with whatever
    models are already on disk."""
    try:
        import boto3
    except ImportError:
        logger.info("boto3 not installed; R2 model sync disabled")
        return None

    account_id = os.getenv("R2_ACCOUNT_ID")
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    if not (account_id and access_key and secret_key):
        logger.info("R2 credentials not set; R2 model sync disabled")
        return None

    endpoint_url = (
        os.getenv("R2_ENDPOINT")
        or f"https://{account_id}.r2.cloudflarestorage.com"
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def sync_models_from_r2(target_dir: Path) -> int:
    """Download R2 model files into ``target_dir``. Returns the number of
    files actually written (i.e. excludes files whose local copy already
    matched R2 by size).

    Failure modes that return 0 cleanly rather than raising:
      - boto3 not installed
      - R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY missing
      - R2_BUCKET missing
      - any boto3 / network error mid-sync

    The caller (lifespan startup) checks the return value to decide whether
    to point predict_service at this directory or the image-baked fallback.
    """
    bucket = os.getenv("R2_BUCKET")
    if not bucket:
        logger.info("R2_BUCKET not set; skipping model sync")
        return 0

    client = _build_client()
    if client is None:
        return 0

    target_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=R2_MODEL_PREFIX):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if not key.endswith(_KEEP_SUFFIXES):
                    continue
                filename = Path(key).name
                local_path = target_dir / filename
                remote_size = int(obj.get("Size", 0))
                if (
                    local_path.exists()
                    and local_path.stat().st_size == remote_size
                ):
                    skipped += 1
                    continue
                client.download_file(bucket, key, str(local_path))
                written += 1
    except Exception as exc:
        # Don't propagate — boot continues with whatever local copies
        # exist (image-baked at minimum). The route still answers; the
        # operator sees the warning in Railway logs.
        logger.warning("R2 model sync failed mid-stream: %s", exc)
        return written

    logger.info(
        "R2 model sync: %d new, %d unchanged → %s", written, skipped, target_dir
    )
    return written


def configured() -> bool:
    """Cheap check the route layer uses to surface "yes I have R2 wired
    up" vs "I'm running on the image-baked models" in /health."""
    return bool(
        os.getenv("R2_BUCKET")
        and os.getenv("R2_ACCOUNT_ID")
        and os.getenv("R2_ACCESS_KEY_ID")
        and os.getenv("R2_SECRET_ACCESS_KEY")
    )


__all__ = ["sync_models_from_r2", "configured"]
