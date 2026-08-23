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

MODELS_DIR = REPO_ROOT / "apps" / "ml" / "models"

# If model files aren't checked into the repo for some reason (gitignore
# drift, large-file storage moved out of band, fresh clone in a sandbox),
# skip the model-dependent tests rather than failing CI loudly. The math
# layer tests below that don't touch models still run.
_REQUIRED_MODEL_FILES = [MODELS_DIR / f"lgbm_T{h}.txt" for h in [1, 2, 3, 7, 14, 21]]
_MODELS_AVAILABLE = all(p.exists() for p in _REQUIRED_MODEL_FILES)
requires_models = pytest.mark.skipif(
    not _MODELS_AVAILABLE,
    reason=(
        "LightGBM serving models not found under apps/ml/models/. "
        "Restore from R2 (`scripts/r2_pull.sh`) or check the .gitignore exception "
        "for apps/ml/models/*.txt before re-running these tests."
    ),
)


@pytest.fixture(autouse=True)
def _point_at_repo_models(monkeypatch):
    """The container bakes models at /app/apps/ml/models; the test runs
    from the repo and reads them from apps/ml/models. Override the env var
    that predict_service consults so the tests don't need a Docker context."""
    monkeypatch.setenv("ML_MODELS_DIR", str(MODELS_DIR))
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
    of the right magnitude so the model returns a finite prediction.

    Covers both the v3 schema (`log_spot`, `straddle_pct`, `iv_rank`, …)
    and the older MVP1 schema (`underlying_price`, `log_price`, `atm_*`)
    so this fixture works whichever model the repo currently ships. Only
    the keys that exist in `feature_names` survive into the resulting
    vector projection inside predict().
    """
    base = {name: 0.0 for name in feature_names}
    base.update({
        # Shared
        "earnings_month": 5,
        "earnings_dow": 2,
        "earnings_weekday": 2,
        "timing_amc": 1.0,
        "timing_bmo": 0.0,
        # MVP1 spot features
        "underlying_price": 100.0,
        "log_price": math.log(100.0),
        # v3 spot feature
        "log_spot": math.log(100.0),
        # Volatility / option features (v3)
        "atm_iv": 0.4,
        "straddle_pct": 0.05,
        "em_iv_pct": 0.04,
        "iv_crush_pct": 0.1,
        "event_move_implied": 0.03,
        "event_vol_fraction": 0.5,
        "iv_rv_ratio_20d": 1.5,
        "iv_rv_ratio_60d": 1.4,
        "iv_cc_rv_ratio_20d": 1.3,
        "rv_term_ratio": 1.0,
        "vol_of_vol_20d": 0.04,
        "parkinson_rv_10d": 0.35,
        "parkinson_rv_20d": 0.34,
        "parkinson_rv_60d": 0.32,
        "cc_rv_10d": 0.4,
        "cc_rv_20d": 0.42,
        "iv_rank": 0.5,
        "hv_rank": 0.5,
        "iv_mom_week": 0.0,
        "iv_mom_month": 0.0,
        "vix_current": 18.0,
        "vix_change_30d": 0.0,
        "vix_pct_252d": 0.5,
        "spy_drift_60d": 0.01,
        "tlt_spy_ratio_30d": 0.4,
        # MVP1 option features
        "log_market_cap": math.log(1e11),
        "atm_straddle_price": 5.0,
        "atm_straddle_pct": 0.05,
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
        # Historical (v3)
        "hist_move_avg_4q": 0.07,
        "hist_move_avg_8q": 0.07,
        "hist_move_med_4q": 0.05,
        "hist_move_med_8q": 0.05,
        "hist_move_std_4q": 0.04,
        "hist_event_count": 8,
        "hist_straddle_accuracy": 0.6,
        # Other (v3)
        "drift_5d": 0.0,
        "dte": 7,
        "horizon": 7,
        "symbol_encoded": 1.0,
        "volume_ratio_20d": 1.0,
    })
    return base


class _ConstantEstimator:
    def __init__(self, value):
        self.value = value

    def predict(self, _frame):
        return [self.value]


def test_predict_clips_point_and_rearranges_crossed_quantiles(monkeypatch):
    ps = _import()
    bundle = ps._ModelBundle(
        estimator=_ConstantEstimator(-0.02),
        feature_names=["log_spot"],
        quantile_estimators={
            10: _ConstantEstimator(0.05),
            25: _ConstantEstimator(-0.01),
            50: _ConstantEstimator(0.03),
            75: _ConstantEstimator(0.09),
            90: _ConstantEstimator(0.07),
        },
        loaded_at=ps.datetime.now(ps.timezone.utc),
        model_version="test",
        model_trained_at=None,
        feature_schema_hash="test-schema",
        val_mae=None,
    )
    monkeypatch.setattr(ps, "get_bundle", lambda _horizon: bundle)

    result = ps.predict(
        feature_vector={"log_spot": math.log(100.0)},
        snapshot_date=date(2026, 5, 22),
        horizon=7,
        spot_override=100.0,
    )

    assert result is not None
    assert result.em_ml_pct == 0.0
    assert result.em_ml_abs == 0.0
    assert result.quantiles == {10: 0.0, 25: 0.03, 50: 0.05, 75: 0.07, 90: 0.09}


@requires_models
def test_bundle_loads_for_each_horizon():
    ps = _import()
    for h in [1, 2, 3, 7, 14, 21]:
        bundle = ps.get_bundle(h)
        assert bundle is not None, f"missing T-{h} bundle"
        # v3 models have ~40 features, MVP1 had 20 — accept either rather
        # than pinning to a specific schema version. The schema-flexible
        # _substitute_spot covers both.
        assert len(bundle.feature_names) > 0
        assert bundle.estimator is not None


@requires_models
def test_bundle_missing_horizon_returns_none():
    ps = _import()
    assert ps.get_bundle(999) is None


def test_substitute_spot_overrides_mvp1_features():
    """MVP1 trained models use underlying_price + log_price."""
    ps = _import()
    fv = {"underlying_price": 100.0, "log_price": math.log(100), "atm_iv": 0.4}
    out = ps._substitute_spot(fv, 150.0)
    assert out["underlying_price"] == 150.0
    assert out["log_price"] == pytest.approx(math.log(150.0))
    # Untouched features pass through.
    assert out["atm_iv"] == 0.4
    # Original is not mutated.
    assert fv["underlying_price"] == 100.0


def test_substitute_spot_overrides_v3_log_spot():
    """v3 production models use log_spot (no underlying_price feature)."""
    ps = _import()
    fv = {"log_spot": math.log(100), "atm_iv": 0.4, "straddle_pct": 0.05}
    out = ps._substitute_spot(fv, 150.0)
    assert out["log_spot"] == pytest.approx(math.log(150.0))
    # Schema is v3 so no underlying_price/log_price should be invented.
    assert "underlying_price" not in out
    assert "log_price" not in out


def test_substitute_spot_handles_both_schemas_simultaneously():
    """Defensive: a vector that somehow has both schema's spot keys gets
    both updated. Avoids a partial update silently leaving one stale."""
    ps = _import()
    fv = {
        "underlying_price": 100.0,
        "log_price": math.log(100),
        "log_spot": math.log(100),
    }
    out = ps._substitute_spot(fv, 200.0)
    assert out["underlying_price"] == 200.0
    assert out["log_price"] == pytest.approx(math.log(200.0))
    assert out["log_spot"] == pytest.approx(math.log(200.0))


def test_substitute_spot_ignores_nonpositive():
    ps = _import()
    fv = {"underlying_price": 100.0, "log_price": math.log(100), "log_spot": math.log(100)}
    assert ps._substitute_spot(fv, 0)["underlying_price"] == 100.0
    assert ps._substitute_spot(fv, -5)["underlying_price"] == 100.0
    assert ps._substitute_spot(fv, 0)["log_spot"] == pytest.approx(math.log(100))


@requires_models
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
    assert result.feature_schema_hash
    assert result.model_loaded_at is not None


@requires_models
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


@requires_models
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
    assert result.feature_schema_hash
