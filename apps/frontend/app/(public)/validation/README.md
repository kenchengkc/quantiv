# Validation route

The `/validation` route is the public research due-diligence surface. It reads only generated, browser-safe artifacts from `apps/frontend/public/` and does not call the protected `/api/ml/status` route.

Primary inputs:

- `evidence/model-validation.json` — active champion performance and calibration. Historical admission results contain one row per admitted horizon/cohort, with its matched market-straddle or historical-move benchmark; legacy artifacts retain the straddle comparison;
- `evidence/forecast.json` — current validated forecast receipt projection;
- `control-plane.json` — current publication/data/model status and advisory exceptions.

The page is intentionally descriptive. It does not expose administrative controls and it does not turn end-of-day research evidence into a live-trading claim.

Historical out-of-sample metrics come from the signed admission receipt. The page counts unique supported horizons separately from cohort rows and distinguishes matched benchmark rows used for MAE from all holdout rows used for calibration. Development walk-forward checks remain separate from the reserved evaluation results. The audit trail shows the reserved calendar window, exclusive development-label cutoff, signed evidence identities, and the limits of historical source-vintage claims. Historical moves are a benchmark only; they do not provide a future-forecast fallback.
