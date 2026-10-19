import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import control from '@/public/control-plane.json';
import forecast from '@/public/evidence/forecast.json';

const coverage = {
  p10: 0.1,
  p25: 0.25,
  p50: 0.5,
  p75: 0.75,
  p90: 0.9,
  interval_50: 0.5,
  interval_80: 0.8,
};
const legacyValidation = {
  schema: 'quantiv.public-model-validation.v1',
  generated_at: '2026-10-15T21:20:00Z',
  model_source: {
    kind: 'signed_champion',
    bundle_id: 'f'.repeat(64),
    artifact_sha256: null,
  },
  summary: {
    supported_horizons: [1, 2, 3, 7, 14, 21],
    validation_row_observations: 1500,
    weighted_model_mae: 0.04,
    weighted_straddle_mae: 0.06,
    weighted_relative_mae_improvement: 1 / 3,
    min_relative_mae_improvement: 1 / 3,
    max_relative_mae_improvement: 1 / 3,
    weighted_coverage: coverage,
  },
  horizons: [1, 2, 3, 7, 14, 21].map((horizon_days) => ({
    horizon_days,
    n_train: 500,
    n_validation: 250,
    model_mae: 0.04,
    straddle_baseline_mae: 0.06,
    relative_mae_improvement: 1 / 3,
    model_rmse: 0.05,
    model_r2: 0.1,
    coverage,
    feature_count: 45,
    model_version: 'v3',
    trained_at: null,
  })),
  validation_protocol: {
    target: 'absolute earnings move magnitude',
    baseline: 'market straddle expected move',
    chronological_holdout: true,
    walk_forward: {
      expanding_windows: 4,
      validation_window_days: 60,
      purge_days: 5,
    },
    promotion_controls: [],
    decision_scope: 'end_of_day_research',
    live_trading_eligible: false,
  },
};

const fixtures = vi.hoisted(() => ({
  artifacts: {} as Record<string, unknown>,
}));
vi.mock('@/lib/researchSnapshot.server', () => ({
  requirePublicJson: (...parts: string[]) =>
    fixtures.artifacts[parts.join('/')],
}));

beforeEach(() => {
  vi.resetModules();
  fixtures.artifacts = {
    'evidence/model-validation.json': structuredClone(legacyValidation),
    'control-plane.json': control,
    'evidence/forecast.json': forecast,
  };
});
afterEach(cleanup);

async function renderPage() {
  const { default: ValidationPage } = await import('./page');
  return render(<ValidationPage />);
}

it('renders admitted cohort results using matched benchmarks and distinct model counts', async () => {
  const base = legacyValidation.horizons[0];
  const supportedHorizons = Array.from(
    { length: 25 },
    (_, index) => index + 1,
  ).filter((horizon) => horizon !== 8 && horizon !== 9);
  const horizons = supportedHorizons.map((horizon_days) => ({
    ...base,
    horizon_days,
    cohort: 'optionless',
    baseline_label: 'Historical move',
    baseline_mae: 0.05,
    model_mae: 0.04,
    straddle_baseline_mae: null,
    n_validation: 250,
    n_matched_baseline: 240,
    metric_scope: 'historical_out_of_sample',
  }));
  horizons.push(
    ...horizons.slice(0, 7).map((row) => ({
      ...row,
      cohort: 'strict_options',
      baseline_label: 'Market straddle',
      baseline_mae: 0.06,
      model_mae: 0.03,
    })),
  );
  fixtures.artifacts['evidence/model-validation.json'] = {
    ...legacyValidation,
    summary: {
      ...legacyValidation.summary,
      supported_horizons: supportedHorizons,
      metric_rows: 30,
      validation_row_observations: 7500,
      matched_baseline_row_observations: 7200,
      weighted_baseline_mae: 0.052,
      weighted_straddle_mae: null,
    },
    horizons,
    validation_protocol: {
      ...legacyValidation.validation_protocol,
      baseline: 'matched cohort benchmarks',
      evaluation_scope: 'historical_out_of_sample',
      walk_forward: {
        ...legacyValidation.validation_protocol.walk_forward,
        evaluation_scope: 'development',
      },
      historical_admission: {
        admission_id: 'a'.repeat(64),
        source_bundle_id: 'b'.repeat(64),
        serving_bundle_id: 'c'.repeat(64),
        reservation_id: 'd'.repeat(64),
        panel_id: 'e'.repeat(64),
        admitted_cohorts: Object.fromEntries(
          supportedHorizons.map((horizon) => [
            String(horizon),
            horizon <= 7 ? ['strict_options', 'optionless'] : ['optionless'],
          ]),
        ),
        protocol: { method: 'globally_purged_historical_holdout' },
        global_temporal_boundary: {
          calendar_start: '2026-04-18T00:00:00',
          calendar_end: '2026-10-14T00:00:00',
          first_test_snapshot: '2026-03-26T00:00:00',
          selection_label_cutoff: '2026-03-26T00:00:00',
        },
        limitations: [
          'Complete calendar and provider delivery vintages are not established.',
          'Prior model-family research exposure is not established.',
        ],
      },
    },
  };

  const { container } = await renderPage();
  expect(container.textContent).toContain(
    '7,500holdout row-observations across 23 horizon models',
  );
  expect(
    screen.getByText(/7,200 matched benchmark row-observations/),
  ).toBeTruthy();
  expect(screen.getByText('Matched cohort benchmarks')).toBeTruthy();
  expect(screen.getByText('5.20%')).toBeTruthy();
  expect(
    screen.queryByText('Every horizon improves', { exact: false }),
  ).toBeNull();

  const table = screen.getByRole('table', { hidden: true });
  const rows = within(table).getAllByRole('row', { hidden: true });
  expect(rows).toHaveLength(31);
  expect(within(table).queryByText('T-8')).toBeNull();
  expect(within(table).queryByText('T-9')).toBeNull();
  expect(within(table).getByText('T-25')).toBeTruthy();
  expect(
    within(table).getByRole('columnheader', {
      name: 'Calibration rows',
      hidden: true,
    }),
  ).toBeTruthy();
  expect(
    within(table).getByRole('columnheader', {
      name: 'Matched rows',
      hidden: true,
    }),
  ).toBeTruthy();
  expect(rows[1].textContent).toContain('Optionless');
  expect(rows[1].textContent).toContain('Historical move');
  expect(rows[1].textContent).toContain('5.00%');
  expect(rows[24].textContent).toContain('Strict options');
  expect(rows[24].textContent).toContain('Market straddle');
  expect(rows[24].textContent).toContain('6.00%');
  expect(container.textContent).toContain('Historical out-of-sample');
  expect(container.textContent).toContain('2026-04-18');
  expect(container.textContent).toContain('2026-10-14');
  expect(container.textContent).toContain('2026-03-26');
  expect(container.textContent).toContain('strictly before 2026-03-26');
});

it('retains the straddle comparison and walk-forward method for legacy validation artifacts', async () => {
  const { container } = await renderPage();
  expect(screen.getByText('Market straddle')).toBeTruthy();
  expect(
    screen.getByText(/holdout row-observations across 6 horizon models/),
  ).toBeTruthy();
  expect(container.textContent).toContain('4 expanding walk-forward windows');
  expect(screen.queryByText('Matched cohort benchmarks')).toBeNull();
  expect(
    screen.queryByText('Historical out-of-sample', { exact: false }),
  ).toBeNull();
  expect(
    screen.queryByRole('columnheader', { name: 'Cohort', hidden: true }),
  ).toBeNull();
});
