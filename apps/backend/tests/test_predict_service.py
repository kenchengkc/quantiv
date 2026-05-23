"""Unit tests for the Phase 1 predict service.

These test the math layer in isolation: model loading, spot substitution,
and the predict() entry point. The route layer (HTTP + Postgres + Upstash)
is covered separately by integration tests once the data pipeline is live.
"""

import math
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "backend"))


@pytest.fixture(autouse=True)
def _point_at_repo_models(monkeypatch):
    """The container bakes models at /app/apps/ml/models; the test runs
    from the repo and reads them from apps/ml/models. Override the env var
    that predict_service consults so the tests don't need a Docker context."""
    monkeypatch.setenv("ML_MODELS_DIR", str(REPO_ROOT / "apps" / "ml" / "models"))
    # Fresh module load to pick up the env var override and reset the
    # in-process cache between tests.
    if "services.predict_service" in sys.modules:
        del sys.modules["services.predict_service"]


def _import():
    from services import predict_service  # noqa: WPS433 — runtime import is intentional
    predict_service.reset_cache()
    return predict_service


def _synthetic_feature_vector(feature_names):
    """Build a roughly realistic feature vector. Values are arbitrary but
    of the right magnitude so the model returns a finite prediction."""
    base = {name: 0.0 for name in feature_names}
    base.update({
        "symbol_encoded": 1.0,
        "horizon": 7,
        "earnings_month": 5,
        "earnings_weekday": 2,
        "underlying_price": 100.0,
        "log_price": math.log(100.0),
        "log_market_cap": math.log(1e11),
        "atm_straddle_price": 5.0,
        "atm_straddle_pct": 0.05,
        "atm_iv": 0.4,
        "atm_delta": 0.5,
        "atm_gamma": 0.02,
        "atm_theta": -0.05,
        "atm_vega": 0.1,
        "skew_25d": 0.0,
        "total_volume": 50000,
        "pc_volume_ratio": 0.6,
        "volume_oi_ratio": 0.4,
        "iv_term_slope": 0.01,
        "tte_earnings": 7.0,
    })
    return base


def test_bundle_loads_for_each_horizon():
    ps = _import()
    for h in [1, 2, 3, 7, 14, 21]:
        bundle = ps.get_bundle(h)
        assert bundle is not None, f"missing T-{h} bundle"
        assert len(bundle.feature_names) == 20
        assert bundle.estimator is not None


def test_bundle_missing_horizon_returns_none():
    ps = _import()
    assert ps.get_bundle(999) is None


def test_substitute_spot_overrides_two_features():
    ps = _import()
    fv = {"underlying_price": 100.0, "log_price": math.log(100), "atm_iv": 0.4}
    out = ps._substitute_spot(fv, 150.0)
    assert out["underlying_price"] == 150.0
    assert out["log_price"] == pytest.approx(math.log(150.0))
    # Untouched features pass through.
    assert out["atm_iv"] == 0.4
    # Original is not mutated.
    assert fv["underlying_price"] == 100.0


def test_substitute_spot_ignores_nonpositive():
    ps = _import()
    fv = {"underlying_price": 100.0, "log_price": math.log(100)}
    assert ps._substitute_spot(fv, 0)["underlying_price"] == 100.0
    assert ps._substitute_spot(fv, -5)["underlying_price"] == 100.0


def test_predict_returns_finite_with_synthetic_features():
    ps = _import()
    bundle = ps.get_bundle(7)
    fv = _synthetic_feature_vector(bundle.feature_names)
    result = ps.predict(
        feature_vector=fv,
        snapshot_date=date(2026, 5, 22),
        horizon=7,
        spot_override=110.0,
    )
    assert result is not None
    assert math.isfinite(result.em_ml_pct)
    assert result.spot_used == 110.0
    # em_ml_abs should track em_ml_pct * spot
    assert result.em_ml_abs == pytest.approx(result.em_ml_pct * 110.0, rel=1e-9)
    assert result.feature_snapshot_date == "2026-05-22"
    assert result.horizon == 7


def test_predict_unknown_horizon_returns_none():
    ps = _import()
    bundle = ps.get_bundle(7)
    fv = _synthetic_feature_vector(bundle.feature_names)
    result = ps.predict(
        feature_vector=fv,
        snapshot_date=date(2026, 5, 22),
        horizon=999,
        spot_override=100.0,
    )
    assert result is None


def test_predict_handles_missing_feature_keys():
    """Schema drift: feature_vector from an older snapshot may be missing
    a column the model expects. LightGBM should still produce a finite
    prediction by treating NaN as missing."""
    ps = _import()
    fv = {"underlying_price": 100.0, "log_price": math.log(100), "atm_iv": 0.4}
    result = ps.predict(
        feature_vector=fv,
        snapshot_date=date(2026, 5, 22),
        horizon=7,
        spot_override=100.0,
    )
    assert result is not None
    assert math.isfinite(result.em_ml_pct)
