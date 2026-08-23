"""Safe LightGBM artifact IO.

Production models use LightGBM's native text format.  Unlike pickle/joblib,
loading this format cannot execute arbitrary Python objects.  Authenticity and
integrity are handled separately by :mod:`ml.model_bundle`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from lightgbm import Booster

MODEL_SUFFIX = ".txt"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_native_model(estimator: Any, path: Path) -> None:
    """Atomically save an sklearn LightGBM wrapper or ``Booster`` as text."""
    if path.suffix != MODEL_SUFFIX:
        raise ValueError(f"native LightGBM artifacts must end in {MODEL_SUFFIX}")
    booster = estimator if isinstance(estimator, Booster) else getattr(estimator, "booster_", None)
    if booster is None:
        raise TypeError("estimator does not expose a fitted LightGBM booster")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    booster.save_model(str(temporary))
    temporary.replace(path)


def load_native_model(path: Path, *, expected_sha256: str | None = None) -> Booster:
    """Load a non-executable LightGBM artifact, optionally checking its digest."""
    if path.suffix != MODEL_SUFFIX:
        raise ValueError(f"refusing non-native model artifact: {path.name}")
    if expected_sha256 is not None:
        actual = sha256_file(path)
        if actual != expected_sha256:
            raise ValueError(
                f"model digest mismatch for {path.name}: expected {expected_sha256}, got {actual}"
            )
    return Booster(model_file=str(path))


def point_model_name(horizon: int) -> str:
    return f"lgbm_T{horizon}.txt"


def quantile_model_name(horizon: int, quantile: int) -> str:
    return f"lgbm_T{horizon}_q{quantile:02d}.txt"


__all__ = [
    "MODEL_SUFFIX",
    "load_native_model",
    "point_model_name",
    "quantile_model_name",
    "save_native_model",
    "sha256_file",
]
