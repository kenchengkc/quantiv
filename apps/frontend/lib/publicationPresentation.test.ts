import { describe, expect, it } from 'vitest';
import {
  publishedForecastStatus, researchUpdatePresentation, quoteEligibilityExplanation,
  type PublicationControl, type PublishedForecast,
} from './publicationPresentation';

const forecast: PublishedForecast = {
  receipt_id: 'sha256:retained-release', validated_at: '2026-09-02T15:22:40Z',
  quality: { status: 'passed', issue_count: 0 }, controls: { exceptions: 0 },
};
const control: PublicationControl = {
  generated_at: '2026-09-06T06:13:20Z', publication_eligible: false,
  data: { status: 'failed', source_date: '2026-09-01', expected_source_date: '2026-09-04', source_session_lag: 3 },
  model: { status: 'degraded' },
  exceptions: [{ code: 'option_quote_quality_below_limit', severity: 'critical' }],
};

describe('publication presentation (never changes gate semantics)', () => {
  it('separates passed retained checks from held new research', () => {
    expect(publishedForecastStatus(forecast)).toBe('passed');
    expect(researchUpdatePresentation(control, forecast)).toMatchObject({ label: 'Held', tone: 'flag' });
    expect(control.publication_eligible).toBe(false);
    expect(control.data.status).toBe('failed');
  });

  it.each([
    { ...forecast, receipt_id: null },
    { ...forecast, validated_at: 'invalid' },
    { ...forecast, quality: { status: 'passed' } },
    { ...forecast, controls: {} },
  ])('never calls incomplete forecast evidence passed', (incomplete) => {
    expect(publishedForecastStatus(incomplete)).toBe('unavailable');
    expect(researchUpdatePresentation(control, incomplete).label).toBe('Blocked');
  });

  it.each([
    { ...forecast, quality: { status: 'failed', issue_count: 0 } },
    { ...forecast, quality: { status: 'passed', issue_count: 1 } },
    { ...forecast, controls: { exceptions: 1 } },
  ])('keeps failed forecast evidence failed', (failed) => {
    expect(publishedForecastStatus(failed)).toBe('failed');
    expect(researchUpdatePresentation(control, failed).label).toBe('Blocked');
  });

  it.each(['corporate_actions_failed', 'source_replay_mismatch', 'unknown_control'])('does not soften %s to a routine hold', (code) => {
    const mixed = { ...control, exceptions: [...control.exceptions, { code, severity: 'critical' }] };
    expect(researchUpdatePresentation(mixed, forecast).label).toBe('Blocked');
  });

  it('does not soften a model failure or inconsistent eligibility', () => {
    expect(researchUpdatePresentation({ ...control, model: { status: 'failed' } }, forecast).label).toBe('Blocked');
    expect(researchUpdatePresentation({ ...control, publication_eligible: true }, forecast).label).toBe('Blocked');
  });

  it('shows eligible only with a complete, permissive current assessment', () => {
    const eligible = { ...control, publication_eligible: true, data: { status: 'passed' }, exceptions: [] };
    expect(researchUpdatePresentation(eligible, forecast).label).toBe('Eligible');
    expect(researchUpdatePresentation({ ...eligible, generated_at: null }, forecast).label).toBe('Unavailable');
    expect(researchUpdatePresentation({ ...eligible, model: {} }, forecast).label).toBe('Unavailable');
    expect(researchUpdatePresentation({ ...eligible, publication_eligible: null }, forecast).label).toBe('Unavailable');
  });

  it('explains legacy freshness failures without falsely asserting rejection rates exceeded limits', () => {
    const text = quoteEligibilityExplanation(control.data);
    expect(text).toContain('2026-09-01 is 3 market sessions behind expected 2026-09-04');
    expect(text).not.toContain('exceeds');
    expect(quoteEligibilityExplanation({})).toContain('freshness, quote-quality or source-capability');
  });

  it('uses actual gate errors when available', () => {
    expect(quoteEligibilityExplanation({ ...control.data, quote_quality_errors: ['Pair rejection rate exceeds limit'] }))
      .toContain('Pair rejection rate exceeds limit');
  });
});
