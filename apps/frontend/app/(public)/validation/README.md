# Validation route

The `/validation` route is the public research due-diligence surface. It reads only generated, browser-safe artifacts from `apps/frontend/public/` and does not call the protected `/api/ml/status` route.

Primary inputs:

- `evidence/model-validation.json` — model-vs-straddle validation and calibration, generated from the active champion metadata when available;
- `evidence/forecast.json` — current validated forecast receipt projection;
- `control-plane.json` — current publication/data/model status and advisory exceptions.

The page is intentionally descriptive. It does not expose administrative controls and it does not turn end-of-day research evidence into a live-trading claim.
