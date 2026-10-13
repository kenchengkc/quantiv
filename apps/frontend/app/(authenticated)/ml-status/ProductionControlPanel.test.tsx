import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import snapshotData from '../../../public/control-plane.json';
import ProductionControlPanel from './ProductionControlPanel';
import type { ControlHistory, ControlSnapshot } from './controlPlaneTypes';

afterEach(cleanup);

const history: ControlHistory = {
  schema: 'quantiv.control-plane-history.v1', generated_at: '2026-10-13T10:00:00Z', runs: [],
};
const retained = snapshotData as ControlSnapshot;

function coverageSnapshot(): ControlSnapshot {
  return {
    ...retained, status: 'advisory', publication_eligible: true,
    data: { ...retained.data, status: 'passed', event_coverage_pct: .5,
      expected_events: 100, covered_events: 50, missing_events: 50, source_session_lag: 0 },
    model: { ...retained.model, status: 'passed' }, exceptions: [],
  };
}

it('shows low aggregate options coverage as an advisory while publication remains eligible', () => {
  render(<ProductionControlPanel snapshot={coverageSnapshot()} history={history} />);
  const metric = screen.getByText('Eligible options coverage').closest('div');
  expect(metric?.textContent).toContain('50.0%');
  expect(metric?.textContent).toContain('50 of 100 events');
  expect(metric?.querySelector('strong')?.style.color).toBe('var(--flag)');
  expect(screen.getByText('Publication eligible · covered events only')).toBeTruthy();
});

it('keeps critical source failure blocked when low options coverage is advisory', () => {
  const snapshot = coverageSnapshot();
  snapshot.status = 'failed';
  snapshot.publication_eligible = false;
  snapshot.data = { ...snapshot.data, status: 'failed', source_session_lag: 3 };
  render(<ProductionControlPanel snapshot={snapshot} history={history} />);
  const metric = screen.getByText('Eligible options coverage').closest('div');
  expect(metric?.querySelector('strong')?.style.color).toBe('var(--flag)');
  const overall = screen.getByText('Publication blocked');
  expect(overall.parentElement?.style.color).toBe('var(--down)');
});
