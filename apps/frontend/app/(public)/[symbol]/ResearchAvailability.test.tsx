import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import ResearchAvailability from './ResearchAvailability';

afterEach(cleanup);
it('explains independent missing coverage and shows EPS even without price reactions', () => {
  render(<ResearchAvailability hasModel={false} hasOptions={false} historyCount={0} today="2026-09-24"
    history={[{ date: '2026-02-05', timing: 'amc', eps_actual: 0, eps_estimate: .3 },
      { date: '2026-10-28', timing: 'unknown', eps_estimate: .48 }]} />);
  expect(screen.getByText('ML forecast unavailable')).toBeTruthy();
  expect(screen.getByText('Options and Greeks unavailable')).toBeTruthy();
  expect(screen.getByText('Historical EPS')).toBeTruthy();
  expect(screen.getByText('0.00')).toBeTruthy();
  expect(screen.queryByText('2026-10-28')).toBeNull();
});
it('does not add missing-data messages when coverage is complete', () => {
  const { container } = render(<ResearchAvailability hasModel hasOptions historyCount={4} today="2026-09-24"
    history={[{ date: '2026-02-05', timing: 'amc', eps_actual: .4 }]} />);
  expect(container.textContent).toBe('');
});
it('explains absent actual EPS instead of treating estimates as results', () => {
  render(<ResearchAvailability hasModel={false} hasOptions={false} historyCount={5} today="2026-09-24"
    history={[{ date: '2026-02-05', timing: 'amc', eps_estimate: .3 }]} />);
  expect(screen.getByText('EPS actuals unavailable')).toBeTruthy();
});
