# Aggregate options coverage policy — September 28, 2026

## Decision

Set `min_upcoming_event_coverage` to **0.65** for aggregate upcoming earnings-event coverage. Keep the 70% per-horizon warning, all quote eligibility rules, session freshness, source reconciliation, fallback protections, and model-promotion controls unchanged. This is an operational availability tolerance; it does not establish that 65% maximizes model accuracy.

## Historical counterfactual

Compared 22 distinct archived reconciliation reports spanning 12 options source dates (September 4–25). Inputs came from the most recent 20 daily refresh runs and the retained rejected-candidate archive in [run 36359763771](https://github.com/kenchengkc/quantiv/actions/runs/36359763771). Nineteen of those runs had a reconciliation artifact; duplicate manifest IDs were removed. This is a convenience sample, includes repeat evaluations of the same source date, and is not an independent long-run backtest.

Only the aggregate coverage condition was re-evaluated from the archived covered/expected counts. Every other recorded critical exception was preserved, including failures produced by older versions of the controls. No archived receipt or production release was altered.

| Result | 70% | 65% |
| --- | ---: | ---: |
| Aggregate coverage passes | 11 | 22 |
| All recorded critical checks pass | 7 | 15 |
| Still held | 15 | 7 |

Eight reports become admissible; seven still fail another critical check. Failed horizon coverage remains visible as its existing warning.

| Report generated (UTC) | Options source | Covered / expected | At 70% | At 65% | Other critical exceptions |
| --- | --- | ---: | --- | --- | --- |
| 2026-09-06T05:57:31 | 2026-09-04 | 25/38 | hold | hold | option_quote_quality_below_limit |
| 2026-09-06T14:43:21 | 2026-09-04 | 25/38 | hold | hold | option_quote_quality_below_limit |
| 2026-09-07T16:28:57 | 2026-09-04 | 27/40 | hold | pass | none |
| 2026-09-08T15:02:43 | 2026-09-07 | 40/59 | hold | hold | option_quote_quality_below_limit |
| 2026-09-15T05:45:00 | 2026-09-11 | 30/41 | hold | hold | option_quote_quality_below_limit |
| 2026-09-15T15:36:13 | 2026-09-14 | 39/51 | pass | pass | none |
| 2026-09-15T17:22:59 | 2026-09-14 | 30/41 | pass | pass | none |
| 2026-09-16T16:11:06 | 2026-09-14 | 38/51 | hold | hold | option_quote_quality_below_limit |
| 2026-09-17T15:43:28 | 2026-09-16 | 37/53 | hold | pass | none |
| 2026-09-18T05:13:40 | 2026-09-16 | 30/42 | hold | hold | option_quote_quality_below_limit |
| 2026-09-18T06:16:51 | 2026-09-16 | 30/42 | hold | hold | option_quote_quality_below_limit |
| 2026-09-18T15:06:22 | 2026-09-17 | 40/53 | pass | pass | none |
| 2026-09-19T14:23:34 | 2026-09-18 | 30/42 | pass | pass | none |
| 2026-09-20T15:17:48 | 2026-09-18 | 30/40 | pass | pass | none |
| 2026-09-21T17:09:56 | 2026-09-18 | 30/43 | hold | pass | none |
| 2026-09-22T15:43:59 | 2026-09-21 | 47/60 | pass | pass | none |
| 2026-09-23T18:41:52 | 2026-09-22 | 45/65 | hold | pass | none |
| 2026-09-24T13:09:52 | 2026-09-23 | 63/92 | hold | pass | none |
| 2026-09-25T11:42:12 | 2026-09-24 | 68/95 | pass | pass | none |
| 2026-09-26T11:17:32 | 2026-09-25 | 64/96 | hold | pass | none |
| 2026-09-27T12:20:34 | 2026-09-25 | 64/96 | hold | pass | none |
| 2026-09-28T00:21:22 | 2026-09-25 | 93/143 | hold | pass | none |

## Exact candidate replay

The September 25 candidate from run 36359763771 covers **93/143 events (65.035%)**. Rebuilt its quote views and event coverage from the archived Parquet and published earnings calendar, then passed those results and the other archived control inputs through the reconciliation manifest builder. The revised policy produces `decision_safe=true` with zero critical exceptions. This is an offline replay, not a new production validation or a replacement freshness receipt.

The complete selected feature rows for all **1,953 eligible symbol/expiration pairs** were identical before and after the policy change. SHA-256 of the sorted, JSON-serialized rows: `9f722839a70797009c34be5adba5740eb3628cd8f151484e226e6803240fdf54`. The 50 uncovered events remain excluded from strict option-pair features. Lowering aggregate coverage admits the dataset without admitting their rejected quotes.

## Model-performance evidence and limits

The most recent completed [retrain 34797284888](https://github.com/kenchengkc/quantiv/actions/runs/34797284888) passed training validation, model validation, and purged walk-forward checks. Its challenger was nevertheless **not promoted**: common-holdout MAE regressed the champion by 2.9–4.3% on five horizons, and T-21 calibration also regressed. The recorded action was `retain_champion`. This demonstrates that successful execution and data admission do not imply model promotion.

The aggregate coverage setting is consumed only by the reconciliation gate. It changes neither quote selection nor model features, losses, fitting, or promotion thresholds for a fixed input dataset. Available evidence does **not** establish accuracy equivalence for the newly admitted snapshot population, nor are there realized outcomes yet for Friday’s upcoming events. Any new challenger must pass fresh training, walk-forward, holdout, calibration, drift, shadow-scoring, and signed promotion controls before replacing the champion.

## Regression and rollout checks

Boundary tests exercise real quote views and event-coverage SQL: 13/20 and 93/143 are admitted; 12/20 and 92/143 remain held. In every case, wide-spread pairs remain quarantined and the separate horizon warning remains active. Existing source, freshness, quote-quality, and model-regression tests remain required.

Roll out through the normal scheduled daily refresh. A previous held release remains held until a fresh reconciliation accepts a candidate under the committed policy. Only then may recovery dispatch the weekly retrain. Do not edit old receipts, manually relabel fallback data, or repeat a successful frontend publication.
