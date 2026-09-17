# Independent IV and ML Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use contract-level IV before historical fallbacks, make ML scoring rows independent of strict straddle eligibility, and keep nightly publication moving safely when the latest options candidate is rejected in favor of a verified published fallback.

**Architecture:** Keep strict option views and analytical `em_*` fields untouched. Add an IV-only selector inside the existing display resolver, replace the ML scorer's strict-straddle row spine with an OHLCV snapshot spine plus a left join to strict option features, and distinguish a verified options fallback that is safe for continued refresh from a fresh decision-eligible options candidate.

**Tech Stack:** Python 3.11, DuckDB SQL, pandas, LightGBM-compatible inference, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-17-independent-iv-and-ml-coverage-design.md`

## Global Constraints

- Do not weaken `v_eligible_straddles`, `v_straddle_features`, option quote-quality thresholds, or strict reconciliation coverage.
- Do not backfill `em_straddle_pct` or other strict option fields from IV-only evidence.
- Keep the public display-method enum backward compatible; IV-only estimates publish as `options_indicative` with `selected_options_details.estimator = "atm_iv"`.
- Keep exact trained-horizon model matching. Do not apply a model to a different lead-time feature vector.
- Non-options critical reconciliation failures must remain fatal.
- Point-in-time cutoff and earnings-session expiry rules must remain unchanged.

---

### Task 1: Independent Contract-Level IV Display Fallback

**Files:**
- Modify: `tools/frontend_data/display_forecast.py`
- Modify: `tools/frontend_data/display_payloads.py`
- Create: `tools/tests/test_independent_iv_fallback.py`

**Interfaces:**
- Consumes: `_raw_options_source()`, `_eod_spot()`, `DisplayPolicy`, `_select_indicative_pair()`.
- Produces: `_select_indicative_iv(...) -> tuple[dict[str, Any] | None, FallbackReason]`; resolver output with `method="options_indicative"` and `selected_options_details["estimator"] == "atm_iv"`.

- [ ] **Step 1: Write failing tests for non-paired and single-sided IV evidence**

```python
def test_mismatched_strikes_use_independent_iv_before_history():
    conn = _conn()
    _option(conn, strike=120, side="Call", iv=0.40, delta=0.50, bid=2.0, ask=3.0)
    _option(conn, strike=125, side="Put", iv=0.44, delta=-0.50, bid=5.0, ask=6.0)
    _history(conn, [0.02, 0.04, 0.06, 0.08])

    result = _resolve(conn)

    assert result.method == "options_indicative"
    assert result.fallback_reason == "no_same_strike_pair"
    assert result.selected_options_details["estimator"] == "atm_iv"
    assert result.selected_options_details["sides_used"] == ["C", "P"]
    assert result.pct == pytest.approx(0.42 * math.sqrt((EXPIRY - AS_OF).days / 365.0))


def test_one_clean_side_can_supply_indicative_iv():
    conn = _conn()
    _option(conn, strike=120, side="Call", iv=0.40, delta=0.50, bid=2.0, ask=3.0)
    _option(conn, strike=120, side="Put", iv=0.44, delta=-0.50, bid=0.01, ask=5.01)

    result = _resolve(conn)

    assert result.method == "options_indicative"
    assert result.selected_options_details["estimator"] == "atm_iv"
    assert result.selected_options_details["sides_used"] == ["C"]
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `pytest -q tools/tests/test_independent_iv_fallback.py`

Expected: FAIL because the existing resolver falls through to historical/prior when no relaxed same-strike pair exists and has no IV-only selector.

- [ ] **Step 3: Implement `_select_indicative_iv`**

Implement a raw-contract query with these normalized columns: `expiry_date`, `strike`, `side`, `bid`, `ask`, `iv`, `delta`. Filter to event-spanning expiries inside `policy.max_post_event_expiry_days`, finite positive strike, and `0 < iv <= 5`. For each expiry, independently select the best call and put using valid delta distance to ±0.5, otherwise spot moneyness. Reject a side whose valid quote spread exceeds `policy.max_leg_relative_spread` or whose ATM metric exceeds `policy.max_atm_delta_distance`. Average two surviving IVs or use the sole surviving IV and compute `iv_em_pct = avg_iv * sqrt(dte / 365.0)`.

Return diagnostics shaped like:

```python
{
    "estimator": "atm_iv",
    "expiry_date": expiry,
    "dte": dte,
    "avg_iv": avg_iv,
    "iv_em_pct": iv_em_pct,
    "sides_used": ["C", "P"],
    "call_strike": ..., "call_iv": ..., "call_atm_metric": ...,
    "put_strike": ..., "put_iv": ..., "put_atm_metric": ...,
    "spot": ..., "spot_source": ...,
}
```

- [ ] **Step 4: Insert IV fallback ahead of history and preserve pair failure provenance**

In `resolve_display_forecast()`, call `_select_indicative_iv()` only after `_select_indicative_pair()` fails. Return `DisplayForecast(method="options_indicative", options_status="indicative", pct=iv_em_pct, fallback_reason=failure_reason, selected_options_details=...)`.

Update `display_payloads.py` validation so `options_indicative` accepts `fallback_reason in {"quote_quality", "no_same_strike_pair"}` while strict and historical invariants remain unchanged.

- [ ] **Step 5: Run focused display tests GREEN**

Run: `pytest -q tools/tests/test_independent_iv_fallback.py tools/tests/test_display_forecast.py tools/tests/test_display_payloads.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/frontend_data/display_forecast.py tools/frontend_data/display_payloads.py tools/tests/test_independent_iv_fallback.py
git commit -m "feat: add independent IV display fallback"
```

---

### Task 2: Remove Strict-Straddle Row Dependency from Live ML Scoring

**Files:**
- Modify: `scripts/daily_score.py`
- Create: `apps/ml/tests/test_daily_score_option_independence.py`
- Verify: `apps/ml/tests/test_daily_score_postprocessing.py`

**Interfaces:**
- Consumes: `v_earnings`, `v_ohlcv`, `v_straddle_features`, `v_realized_vol`, `v_volhist`, `v_vix`.
- Produces: `get_upcoming_features()` dataframe with the same feature columns, but rows may exist with null option features.

- [ ] **Step 1: Write a failing SQL-contract test**

```python
def test_upcoming_feature_spine_does_not_require_strict_straddle():
    connection = _CapturingConnection()
    get_upcoming_features(connection, 21)

    assert "snapshot_spine AS" in connection.sql
    assert "LEFT JOIN v_straddle_features sf" in connection.sql
    assert "WHERE sf.atm_iv > 0" not in connection.sql
    assert "COALESCE(rv.close, sp.snapshot_close, sf.atm_strike) AS spot_price" in connection.sql
```

- [ ] **Step 2: Run focused test and confirm RED**

Run: `pytest -q apps/ml/tests/test_daily_score_option_independence.py`

Expected: FAIL because current SQL uses `JOIN v_straddle_features sf` as the row-generating relation and filters on positive strict ATM IV/strike.

- [ ] **Step 3: Build an OHLCV snapshot spine**

Add a `snapshot_spine` CTE after `upcoming`:

```sql
snapshot_spine AS (
    SELECT
        u.act_symbol,
        u.earnings_date,
        u.timing,
        px.date AS snapshot_date,
        px.close AS snapshot_close
    FROM upcoming u
    JOIN v_ohlcv px
      ON px.act_symbol = u.act_symbol
     AND px.date < u.earnings_date
     AND (u.earnings_date - px.date) BETWEEN 1 AND 25
     AND px.close > 0
)
```

Use `snapshot_spine sp` as the final row spine. LEFT JOIN `v_straddle_features sf` by symbol/date plus the existing event-spanning expiry rules. Join realized-vol/macro/vol-history on `sp.snapshot_date`. Preserve all current option columns as nullable expressions from `sf`. Use `COALESCE(rv.close, sp.snapshot_close, sf.atm_strike)` for spot.

- [ ] **Step 4: Keep option-derived ratios nullable rather than filtering rows**

Remove the final `WHERE sf.atm_iv > 0 AND sf.atm_strike > 0`. Keep `NULLIF` protections in option ratios and correction factors so LightGBM receives NaN for absent option features.

- [ ] **Step 5: Run live-scoring tests GREEN**

Run: `pytest -q apps/ml/tests/test_daily_score_option_independence.py apps/ml/tests/test_daily_score_postprocessing.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/daily_score.py apps/ml/tests/test_daily_score_option_independence.py
git commit -m "fix: decouple ML rows from strict straddles"
```

---

### Task 3: Continue Refresh Safely on a Verified Published Options Fallback

**Files:**
- Modify: `scripts/options_snapshot_resilience.py`
- Modify: `scripts/tests/test_options_snapshot_resilience.py`
- Verify: `.github/workflows/data-refresh.yml`

**Interfaces:**
- Consumes: published data-release pointer, reconciliation manifest, local options partitions.
- Produces: `FinalizationResult.can_score=True` for both accepted candidates and a successfully restored published fallback; state still differentiates `accepted` vs `fallback`.

- [ ] **Step 1: Change the fallback test expectation first**

For a soft options-only failure with a valid published fallback, assert:

```python
result = finalize_snapshot(...)
assert result.state == "fallback"
assert result.can_score is True
status = json.loads((data_dir / "validation/options_snapshot_status.json").read_text())
assert status["policy"]["strict_options_candidate_accepted"] is False
assert status["policy"]["refresh_scoring_allowed"] is True
```

Keep unrelated-critical-failure tests asserting blocked/fatal behavior.

- [ ] **Step 2: Run resilience tests and confirm RED**

Run: `pytest -q scripts/tests/test_options_snapshot_resilience.py`

Expected: FAIL because fallback currently sets `can_score=False`.

- [ ] **Step 3: Update fallback result semantics after rollback verification**

Only after `_quarantine_unpublished_partitions()` completes and the remaining newest partition is exactly `published_date`, return:

```python
FinalizationResult(
    state="fallback",
    can_score=True,
    active_source_date=published_date,
    ...,
)
```

Update status policy metadata to expose both facts explicitly:

```python
"strict_options_candidate_accepted": state == "accepted",
"refresh_scoring_allowed": state in {"accepted", "fallback"},
```

Retain `fallback_mode="last_published_snapshot"` and the real active source date. Keep the existing `scoring_allowed` field for compatibility, but set it from the same refresh-scoring rule so workflow/output semantics do not contradict the status artifact.

- [ ] **Step 4: Verify workflow compatibility**

The existing workflow already gates scoring/frontend construction on `steps.options_gate.outputs.can_score == 'true'`. Confirm no production YAML threshold changes are required. The fallback rollback happens inside `finalize_snapshot()` before it returns and verifies the published partition is the newest remaining local partition.

- [ ] **Step 5: Run resilience tests GREEN**

Run: `pytest -q scripts/tests/test_options_snapshot_resilience.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/options_snapshot_resilience.py scripts/tests/test_options_snapshot_resilience.py
git commit -m "fix: keep refresh running on verified options fallback"
```

---

### Task 4: Precise ML Availability Diagnostics Without Horizon Substitution

**Files:**
- Modify: `tools/frontend_data/display_forecast.py`
- Modify: `tools/frontend_data/display_payloads.py`
- Modify: `tools/tests/test_display_forecast.py`

**Interfaces:**
- Consumes: optional explicit `ml_status` on the supplied ML metadata/event.
- Produces: resolver preserves a valid caller-provided `unavailable_model` or `unavailable_event` instead of hard-coding every missing forecast to `unavailable_inputs`.

- [ ] **Step 1: Write failing status-propagation tests**

```python
@pytest.mark.parametrize("status", ["unavailable_inputs", "unavailable_model", "unavailable_event"])
def test_missing_ml_preserves_explicit_unavailability_reason(status):
    result = _resolve(
        _conn(),
        ml_forecast={"em_ml_pct": None, "ml_status": status},
        strict_options={"em_baseline_straddle": 0.08},
    )
    assert result.ml_status == status
```

- [ ] **Step 2: Run focused test and confirm RED**

Run: `pytest -q tools/tests/test_display_forecast.py -k unavailability_reason`

Expected: FAIL because the resolver currently hard-codes `unavailable_inputs` on every non-ML branch.

- [ ] **Step 3: Add conservative status normalization**

Add a helper that returns `available` only when a finite ML forecast exists; otherwise accepts only the three explicit unavailable statuses and defaults to `unavailable_inputs`. Use that normalized value in strict options, indicative pair, IV-only, historical, and prior branches.

Pass through an event's optional `ml_status` from `display_payloads.py` into `ml_forecast`.

- [ ] **Step 4: Run display diagnostics tests GREEN**

Run: `pytest -q tools/tests/test_display_forecast.py tools/tests/test_display_payloads.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/frontend_data/display_forecast.py tools/frontend_data/display_payloads.py tools/tests/test_display_forecast.py
git commit -m "fix: preserve ML unavailability diagnostics"
```

---

### Task 5: Full Verification and Pull Request

**Files:**
- Verify all files changed above.
- No production behavior added in this task.

**Interfaces:**
- Produces: CI-verified branch and PR.

- [ ] **Step 1: Run the focused Python suites**

Run:

```bash
pytest -q \
  tools/tests/test_independent_iv_fallback.py \
  tools/tests/test_display_forecast.py \
  tools/tests/test_display_payloads.py \
  apps/ml/tests/test_daily_score_option_independence.py \
  apps/ml/tests/test_daily_score_postprocessing.py \
  scripts/tests/test_options_snapshot_resilience.py
```

Expected: PASS.

- [ ] **Step 2: Run repository validation that covers affected data/publication contracts**

Run the repository's existing CI/test commands for Python model/data tooling and frontend payload/schema checks as configured by GitHub Actions.

Expected: all required checks PASS.

- [ ] **Step 3: Review the diff for semantic invariants**

Confirm:

```text
strict option views/thresholds unchanged
em_straddle_pct not backfilled from IV
IV-only fallback precedes historical fallback
ML rows can exist with null option features
exact horizon scoring unchanged
fallback state retains old options source date
unrelated critical failures remain fatal
```

- [ ] **Step 4: Open/update the PR**

Use title:

`Fix options/ML forecast coverage before historical fallback`

PR body must summarize the root causes, changes, tests, and explicitly call out that strict research eligibility was not loosened.
