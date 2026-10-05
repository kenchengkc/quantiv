# Model learning architecture repair plan

Design: ../specs/2026-10-05-model-learning-architecture-design.md

1. Add regression tests and repair shared causal target/features, historical
   cohort parity, availability sidecars and versioned scoring compatibility.
2. Repair causal selection and walk-forward boundaries; freeze selection recipes,
   refit every model head on mature recent data, and record fitting/exposure
   evidence without reusing consumed labels as independent tests.
3. Repair fair comparison and cohort drift; retain frozen challenger evidence and
   require protocol-specific prospective vectors before activation.
4. Separate verified historical admission from current-data activation; archive
   signed candidates before live gates and update weekly orchestration, including
   no-event and held-data paths. Preserve all production gates and atomic writes.
   Stage rollback recommendations separately from pointer mutation; only complete
   a validated, freshly eligible handoff, while known live holds retain the
   existing champion and leave historical fitting independent.
5. Integrate protocol/validation contracts and update runbooks. Run targeted
   regressions, all required Python checks and actionlint. Fix failures and obtain
   independent review of both correctness and gate preservation.
6. Commit the reviewed change and publish a reviewable pull request with concrete
   validation evidence. Only perform production recovery actions justified by
   verified current gates; do not bypass a model hold.
