# Display Forecast Fallbacks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee a finite user-facing expected-move estimate for every published upcoming earnings event using the approved ML → strict options → indicative options → ticker history → universe prior hierarchy, while preserving existing strict research/ML controls.

**Architecture:** Add a dedicated backend display-forecast resolver under `tools/frontend_data/` that owns fallback selection and provenance. Week/screener/symbol payload builders consume that resolver and publish explicit display-method/status fields; frontend surfaces render the canonical display value and provenance rather than inferring state from null ML/options fields. Existing `v_eligible_straddles`, ML scoring, validation, and research-quality coverage remain unchanged.

**Tech Stack:** Python 3.11, DuckDB, pytest, Next.js 15, React 18, TypeScript, Vitest, GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-09-16-display-forecast-fallbacks-design.md`

## Global Constraints

- Preserve `em_ml_pct` purity: only actual ML output may populate it.
- Preserve existing strict option quote-quality thresholds and `v_eligible_straddles` semantics for research/ML eligibility.
- Initial display-only policy values are exactly: leg spread `1.0`, straddle spread `0.75`, ATM delta distance `0.60`, post-event expiry window `30` days, ticker-history minimum `2`, ticker-history window `4`, universe-prior window `730` days.
- AMC EOD observation may be on the earnings date and expiry must be strictly after the event; non-AMC observation must be before the event and expiry may be on/after the event.
- Never fabricate Greeks, IV, quantiles, or option panels when supporting evidence is absent.
- Every upcoming event from `calendar-reference.json` must publish finite positive `display_forecast_pct` and a valid `display_forecast_method`.
- Historical reconstruction must be point-in-time safe; future events cannot contribute to old forecasts.
- Frontend method differentiation must not add horizontal badges/markers to the calendar expected-move cell.

---

### Task 1: Add display-quality policy and canonical backend resolver

**Files:**
- Create: `config/option_display_quality.json`
- Create: `tools/frontend_data/display_forecast.py`
- Create: `tools/tests/test_display_forecast.py`

**Interfaces:**
- Produces `DisplayForecast` dataclass with `pct`, `method`, `as_of`, `ml_status`, `options_status`, `fallback_reason`, `historical_event_count`, and optional `selected_options_details`.
- Produces `load_display_policy(path: Path | None = None) -> DisplayPolicy`.
- Produces `resolve_display_forecast(conn, *, ticker: str, earnings_date: date, timing: str | None, as_of_date: date, ml_forecast: dict | None, strict_options: dict | None = None, universe_prior: dict | None = None) -> DisplayForecast`.
- Produces `build_universe_historical_prior(conn, *, cutoff: date, window_days: int = 730) -> dict`.
- Consumes the existing strict options result from `compute_em_math()` when available; never alters strict views.

- [ ] **Step 1: Add failing resolver tests**

Create `tools/tests/test_display_forecast.py` using an in-memory DuckDB connection with tiny `v_options_chain`, `earnings_events`, and `v_ohlcv` tables/views. Cover:

```python
def test_ml_wins_over_all_fallbacks():
    result = resolve_display_forecast(..., ml_forecast={"em_ml_pct": 0.041}, strict_options={"em_baseline_straddle": 0.08})
    assert result.method == "ml"
    assert result.pct == 0.041
    assert result.ml_status == "available"


def test_strict_options_used_when_ml_missing():
    result = resolve_display_forecast(..., ml_forecast=None, strict_options={"em_baseline_straddle": 0.081})
    assert result.method == "options_math"
    assert result.options_status == "decision_eligible"


def test_payx_like_pair_becomes_indicative():
    # 51.75% call spread: above strict 50%, below display 100%.
    ...
    assert result.method == "options_indicative"
    assert result.fallback_reason == "quote_quality"


def test_ful_like_pair_rejects_190pct_leg_and_uses_history():
    ...
    assert result.method == "historical"
    assert result.options_status == "unavailable"


def test_no_same_strike_pair_uses_history():
    ...
    assert result.method == "historical"
    assert result.fallback_reason == "no_same_strike_pair"


def test_one_prior_event_uses_universe_prior():
    ...
    assert result.method == "historical_prior"
    assert result.fallback_reason == "insufficient_ticker_history"


def test_crossed_quote_is_never_indicative():
    ...
    assert result.method in {"historical", "historical_prior"}


def test_amc_same_day_expiry_rejected_but_bmo_same_day_expiry_allowed():
    ...
```

- [ ] **Step 2: Run the resolver test and verify it fails**

Run in CI-equivalent environment:

```bash
PYTHONPATH=apps/ml:scripts:tools pytest tools/tests/test_display_forecast.py -q
```

Expected: import/function failures because resolver does not exist yet.

- [ ] **Step 3: Add display policy**

Create `config/option_display_quality.json` exactly:

```json
{
  "schema": "quantiv.option-display-quality.v1",
  "max_leg_relative_spread": 1.0,
  "max_straddle_relative_spread": 0.75,
  "max_atm_delta_distance": 0.6,
  "max_post_event_expiry_days": 30,
  "min_ticker_history_events": 2,
  "ticker_history_window_events": 4,
  "universe_prior_window_days": 730
}
```

- [ ] **Step 4: Implement resolver core**

In `tools/frontend_data/display_forecast.py`, define typed literals and dataclasses:

```python
DisplayMethod = Literal["ml", "options_math", "options_indicative", "historical", "historical_prior"]
MLStatus = Literal["available", "unavailable_inputs", "unavailable_model", "unavailable_event"]
OptionsStatus = Literal["decision_eligible", "indicative", "unavailable"]
FallbackReason = Literal[
    "quote_quality",
    "no_same_strike_pair",
    "no_event_expiry",
    "insufficient_ticker_history",
] | None
```

Implement structural pair selection from `v_options_chain` without touching `v_eligible_straddles`: nearest event-spanning expiry first, same-strike C/P pairs, finite nonnegative bid/ask, non-crossed sides, positive combined midpoint, positive strike. Compute leg and straddle relative spread from midpoints, use delta ATM distance when both deltas are finite, otherwise strike/spot distance, and enforce display-only ceilings.

Use `straddle_mid / spot` for indicative percentage. Prefer a valid EOD spot field from the chain/view when present; otherwise use the same estimated spot proxy used by the existing options pipeline. Keep selected pair details for diagnostics.

- [ ] **Step 5: Implement point-in-time historical fallbacks**

Add helpers that query timing-aware realized earnings moves strictly before the target event, take up to four most recent absolute moves, require at least two, and return the median. Add `build_universe_historical_prior()` using the trailing 730-day active-event window as of cutoff, returning:

```python
{
    "as_of_date": cutoff.isoformat(),
    "window_start": (cutoff - timedelta(days=730)).isoformat(),
    "window_end": cutoff.isoformat(),
    "median_abs_move": value,
    "event_count": n,
    "symbol_count": m,
}
```

- [ ] **Step 6: Implement hierarchy and statuses**

`resolve_display_forecast()` must apply exactly:

```python
if finite_positive(ml_pct): return ML
if finite_positive(strict_straddle_pct): return OPTIONS_MATH
if indicative_pair: return OPTIONS_INDICATIVE
if ticker_history_count >= policy.min_ticker_history_events: return HISTORICAL
return HISTORICAL_PRIOR
```

If the supplied universe prior is missing/invalid, compute it point-in-time from the connection. If even the prior cannot produce a finite positive number, raise `DisplayForecastError`; do not return a null/dash state for upcoming published events.

- [ ] **Step 7: Run resolver tests**

Run:

```bash
PYTHONPATH=apps/ml:scripts:tools pytest tools/tests/test_display_forecast.py -q
ruff check tools/frontend_data/display_forecast.py tools/tests/test_display_forecast.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add config/option_display_quality.json tools/frontend_data/display_forecast.py tools/tests/test_display_forecast.py
git commit -m "feat: add display forecast resolver"
```

---

### Task 2: Integrate canonical display forecasts into week, screener, and symbol payloads

**Files:**
- Modify: `tools/frontend_data/payloads.py`
- Modify: `tools/build_frontend_data.py`
- Modify: `tools/tests/test_frontend_data_contract.py`
- Modify: `tools/tests/test_published_calendar_rekey.py`
- Modify: `tools/tests/test_reported_event_preservation.py`
- Modify: `tools/validate_public_contracts.py`
- Modify: `tools/tests/test_validate_public_contracts.py`

**Interfaces:**
- Consumes `resolve_display_forecast()` from Task 1.
- Adds fields to each upcoming event: `display_forecast_pct`, `display_forecast_method`, `display_forecast_as_of`, `ml_status`, `options_status`, `fallback_reason`, `historical_event_count`.
- Adds same provenance fields to symbol `expected_move`.
- Produces `validate_upcoming_display_forecasts(calendar_events, week_payloads, today)` build-time invariant helper.

- [ ] **Step 1: Add failing payload contract tests**

Extend `test_frontend_data_contract.py` so every upcoming event in committed/current week payloads requires finite positive `display_forecast_pct`, allowed method enum, and status fields. Add a unit test for the invariant helper that passes with valid rows and raises on missing/zero/NaN display forecasts.

Extend published-calendar rekey tests so a revised published identity with no exact ML still receives a non-null display forecast via fallback and never reuses the superseded event's ML.

Extend reported-event preservation tests to assert retained rows keep their prior `display_forecast_*` metadata unchanged.

- [ ] **Step 2: Run focused tests and verify failure**

```bash
PYTHONPATH=apps/ml:scripts:tools pytest \
  tools/tests/test_frontend_data_contract.py \
  tools/tests/test_published_calendar_rekey.py \
  tools/tests/test_reported_event_preservation.py -q
```

Expected: failures for missing display fields/invariant.

- [ ] **Step 3: Integrate resolver into `build_week_events()`**

Replace the current published-row branch that emits `em_method=None` and null expected-move fields. For every published upcoming row, call the resolver with exact `(ticker, earnings_date)`, timing, `as_of_date`, exact ML lookup, and current strict `compute_em_math()` result.

Keep strict analytical fields untouched:

```python
"em_ml_pct": ml.get("em_ml_pct") if ml else None,
"em_straddle_pct": strict_em.get("em_baseline_straddle") if strict_em else None,
```

Then merge canonical display metadata from the resolver. `em_method` remains analytical (`ml_lightgbm`, `options_math`, or `None`) rather than being overloaded with historical fallback semantics.

For already-reported rows, retain prior published forecast metadata instead of recomputing after the event.

- [ ] **Step 4: Make `build_symbol_detail()` independent of strict option availability**

Remove the early `return None` when `eligible_pairs` is empty. Build earnings history/provider data regardless. Resolve the canonical display forecast for the published upcoming event. When strict options are absent, keep `straddle_features=[]`; do not synthesize ATM/Greeks fields.

When `expected_move` is built, include canonical display metadata plus only the evidence fields genuinely available. For historical fallbacks, `expected_move` can carry event identity and display provenance while options-specific numeric fields are null/absent.

- [ ] **Step 5: Add build-time invariant and method summary**

In `build_frontend_data.py`, after all week payloads are built and before writing final screener/manifest publication, validate every upcoming `calendar-reference.json` identity in the published span has exactly one matching week row and a finite positive display forecast with a valid method/status combination. Abort with nonzero exit if violated.

Print a method mix summary from generated week rows:

```text
forecast methods: ml=N options_math=N options_indicative=N historical=N historical_prior=N
```

- [ ] **Step 6: Extend public-contract validation**

In `validate_public_contracts.py`, allow additive fields but enforce, for upcoming screener rows, finite positive `display_forecast_pct`, allowed method enum, and coherent statuses. Do not require past rows to be recomputed.

- [ ] **Step 7: Run data-contract tests**

```bash
PYTHONPATH=apps/ml:scripts:tools pytest tools/tests -q
ruff check tools
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add tools/frontend_data/payloads.py tools/build_frontend_data.py tools/tests tools/validate_public_contracts.py
git commit -m "feat: publish canonical display forecasts"
```

---

### Task 3: Add display-forecast observability to the control plane

**Files:**
- Modify: `tools/build_frontend_data.py`
- Modify: `tools/build_control_plane_snapshot.py`
- Modify: `tools/tests/test_build_control_plane_snapshot.py`
- Create: `data/validation/.gitkeep` only if the directory is not already retained; do not commit generated status artifacts.

**Interfaces:**
- `build_frontend_data.py` writes runtime artifact `data/validation/display_forecast_status.json` with display coverage and method mix.
- `build_control_plane_snapshot.py` reads the optional artifact and exposes `data.display_forecast_coverage_pct`, `data.display_forecast_events`, and `data.display_forecast_method_mix` without changing publication eligibility.

- [ ] **Step 1: Add failing control-plane test**

Add a test that calls `build_snapshot(..., display_forecast_status=...)` and expects:

```python
snapshot["data"]["display_forecast_coverage_pct"] == 1.0
snapshot["data"]["display_forecast_method_mix"]["options_indicative"] == 3
```

Also assert strict `event_coverage_pct` and `publication_eligible` are unchanged by display fallback coverage.

- [ ] **Step 2: Run focused test and verify failure**

```bash
PYTHONPATH=apps/ml:scripts:tools pytest tools/tests/test_build_control_plane_snapshot.py -q
```

- [ ] **Step 3: Emit display status artifact**

After week resolution, write:

```json
{
  "schema": "quantiv.display-forecast-status.v1",
  "generated_at": "...",
  "published_upcoming_events": 41,
  "with_display_forecast": 41,
  "coverage_pct": 1.0,
  "method_mix": {
    "ml": 30,
    "options_math": 4,
    "options_indicative": 3,
    "historical": 3,
    "historical_prior": 1
  }
}
```

- [ ] **Step 4: Project the optional status into control-plane snapshot**

Add optional `display_forecast_status` input to `build_snapshot()`, load `data/validation/display_forecast_status.json` in CLI/main path, and publish the display metrics under `data`. Missing artifact should degrade only those fields to unavailable/null; it must not alter strict data/model status or publication eligibility.

- [ ] **Step 5: Run control tests**

```bash
PYTHONPATH=apps/ml:scripts:tools pytest tools/tests/test_build_control_plane_snapshot.py -q
ruff check tools/build_control_plane_snapshot.py
```

- [ ] **Step 6: Commit**

```bash
git add tools/build_frontend_data.py tools/build_control_plane_snapshot.py tools/tests/test_build_control_plane_snapshot.py
git commit -m "feat: expose display forecast coverage"
```

---

### Task 4: Add shared frontend display-forecast types and update calendar/screener/watchlist

**Files:**
- Create: `apps/frontend/lib/displayForecast.ts`
- Create: `apps/frontend/lib/displayForecast.test.ts`
- Modify: `apps/frontend/components/EarningsGrid.tsx`
- Modify: `apps/frontend/components/EarningsScreener.tsx`
- Modify: `apps/frontend/lib/screenerResearch.ts`
- Modify: `apps/frontend/app/(authenticated)/watchlist/WatchlistPageClient.tsx`
- Modify: `apps/frontend/app/globals.css`

**Interfaces:**
- `DisplayForecastMethod` mirrors backend enum.
- `displayForecastTone(method)` returns semantic class/tone; components do not infer method from null analytical fields.
- Compact surfaces use `display_forecast_pct` as static value, while live spot-updated ML may still override where that existing feature explicitly applies.

- [ ] **Step 1: Add failing frontend utility tests**

Create tests asserting method → label/tone mapping, including:

```ts
expect(displayForecastLabel('ml')).toBe('ML forecast');
expect(displayForecastLabel('options_indicative')).toBe('Market implied');
expect(displayForecastLabel('historical')).toBe('Historical median');
expect(displayForecastLabel('historical_prior')).toBe('Historical prior');
```

- [ ] **Step 2: Implement shared types/helpers**

Define the method/status unions and helpers for visible label, CSS class, and method predicates. Keep copy centralized so calendar, screener, and watchlist cannot drift.

- [ ] **Step 3: Update calendar expected-move cell**

Extend `EarningsEvent` with display/provenance fields. Replace `ev.em_ml_pct ?? em_straddle_pct ?? em_iv_pct` with canonical `ev.display_forecast_pct` (legacy analytical fallback only for old cached payload compatibility, never as the preferred path).

Keep the same cell width. Apply semantic classes only:

```css
.qv-forecast--ml { color: var(--ink-2); font-weight: 650; }
.qv-forecast--options { color: var(--brand-blue-1); font-weight: 580; }
.qv-forecast--indicative { color: color-mix(...); font-weight: 560; }
.qv-forecast--historical { color: var(--ink-3); font-weight: 540; }
.qv-forecast--prior { color: var(--ink-4); font-weight: 540; }
```

No badge/icon/extra inline text.

Update `ExpectedMoveHover` to render explicit provenance states:
- ML: ML forecast, market implied, typical band.
- options: market implied + `ML forecast Unavailable`.
- historical: historical median + ML unavailable + market implied unavailable.
- prior: historical prior + unavailable lines.

- [ ] **Step 4: Update screener and watchlist compact values**

Add display fields to their event/summary types. Use canonical display value for expected-move display. Preserve true ML-only filters/sorts on `em_ml_pct`; do not redefine `mlOnly` as “has any display forecast.”

For watchlist live spot-updated ML, keep existing live ML override when ready; otherwise use static `display_forecast_pct` rather than rebuilding a fallback chain in React.

- [ ] **Step 5: Run frontend unit/type checks**

```bash
npm run type-check --workspace=apps/frontend
npm run test --workspace=apps/frontend -- --run
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/frontend/lib/displayForecast.ts apps/frontend/lib/displayForecast.test.ts apps/frontend/components/EarningsGrid.tsx apps/frontend/components/EarningsScreener.tsx apps/frontend/lib/screenerResearch.ts 'apps/frontend/app/(authenticated)/watchlist/WatchlistPageClient.tsx' apps/frontend/app/globals.css
git commit -m "feat: render forecast provenance across compact surfaces"
```

---

### Task 5: Make the ticker dashboard a full provenance surface

**Files:**
- Modify: `apps/frontend/app/(public)/[symbol]/symbolPageTypes.ts`
- Modify: `apps/frontend/app/(public)/[symbol]/SymbolPageClient.tsx`
- Modify: `apps/frontend/app/(public)/[symbol]/MoveComparisonChart.tsx`
- Modify: `apps/frontend/app/(public)/[symbol]/MoveComparisonChart.test.tsx`
- Modify or create a focused forecast-state component/test under `apps/frontend/app/(public)/[symbol]/` if keeping all copy in `SymbolPageClient.tsx` would make it harder to test.

**Interfaces:**
- `ExpectedMove` gains display/provenance fields.
- Dashboard headline uses `expected_move.display_forecast_pct`.
- ML quantile/model UI only renders when actual ML exists.
- Options panels only render from genuine options data.

- [ ] **Step 1: Add failing ticker-page behavior tests**

Extend `MoveComparisonChart.test.tsx` and/or add `ForecastProvenance.test.tsx` to cover:

```text
ML: shows ML + options + history
options_indicative: shows options-implied primary, “ML unavailable”, indicative-liquidity note
historical: shows historical estimate, “ML unavailable”, “Market-implied estimate unavailable”
historical_prior: shows historical prior and limited-history explanation
```

- [ ] **Step 2: Extend symbol types and derive dashboard state**

Add the canonical display fields/status enums to `ExpectedMove`. Compute `activeDisplayForecastPct` separately from `activePredictionPct`; live spot-updated ML can override only when an actual live ML response exists.

- [ ] **Step 3: Render provenance copy and preserve analytical panel gating**

Ensure the forecast comparison section can render when `expected_move` exists but has no strict options fields. Pass `optionsMovePct=null` and `modelMovePct=null` where evidence is absent rather than hiding the entire forecast section.

Display copy exactly:
- `ML unavailable`
- Indicative note: `Indicative market estimate; current option quotes did not meet the stricter ML-input quality threshold.`
- Historical note: `Median absolute move across the last N earnings events.`
- Prior note: `Limited company-specific history; estimate uses Quantiv's recent earnings-event historical baseline.`

Keep Greeks/term-structure/options-scenario cards gated on real `straddle_features` / strict options evidence.

- [ ] **Step 4: Run symbol-page tests and type check**

```bash
npm run type-check --workspace=apps/frontend
npm run test --workspace=apps/frontend -- --run
```

- [ ] **Step 5: Commit**

```bash
git add 'apps/frontend/app/(public)/[symbol]'
git commit -m "feat: explain forecast fallbacks on symbol pages"
```

---

### Task 6: Document the contract and verify the complete branch through CI

**Files:**
- Modify: `docs/PUBLIC_DATA_CONTRACTS.md`
- Modify: `docs/NUMBER_TO_UI.md`
- Modify: `docs/RECONCILIATION_CONTROL_PLANE.md` if needed to document that display coverage does not replace strict options coverage.
- Modify schemas only if current schemas enumerate event properties strictly; otherwise keep this additive within v1 contracts.

**Interfaces:**
- Documents `display_forecast_pct` as the canonical presentation hierarchy.
- Documents `em_ml_pct` as pure ML output.
- Documents separate strict research coverage vs user-facing display coverage.

- [ ] **Step 1: Update public contract docs**

Add the stable semantic rule:

> `display_forecast_pct` is Quantiv's best currently available presentation estimate according to the documented ML → strict options → indicative options → ticker history → universe prior hierarchy. `em_ml_pct` remains populated only by an actual ML forecast.

Document the method/status enum and point-in-time rules.

- [ ] **Step 2: Update number-to-UI walkthrough**

Add the display resolver between validated analytical fields and generated frontend payloads. Explain that indicative/historical fallback values are display-only and never feed ML training/scoring or strict quote-quality coverage.

- [ ] **Step 3: Open PR to trigger full CI**

Create a PR from `feature/display-forecast-fallbacks` to `main` with a summary of backend resolver, UI provenance, and control-plane separation.

- [ ] **Step 4: Verify all CI jobs**

Required green checks:

```text
Frontend (lint, type-check, build, vitest)
Frontend (Playwright)
Backend (ruff, pytest)
Data pipeline (ruff, pytest, artifacts)
Quote Worker (type-check, vitest)
Railway image (build, health smoke)
```

If any check fails, fetch the failed job logs, apply the smallest targeted fix, push to the same branch, and repeat until green.

- [ ] **Step 5: Review the final diff against the spec**

Confirm:
- no upcoming published row can resolve to a dash;
- ML and strict options fields keep their original meanings;
- indicative/history do not affect strict control-plane eligibility;
- compact calendar has no method badge clutter;
- ticker page exposes fallback provenance;
- no fabricated options/model analytics appear for historical fallbacks;
- docs and tests cover the public contract.

- [ ] **Step 6: Final commit if documentation fixes remain**

```bash
git add docs schemas
git commit -m "docs: document display forecast fallback contract"
```
