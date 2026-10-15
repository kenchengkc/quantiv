import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import EarningsGrid from './EarningsGrid';

it('lets the calendar advance from offset two into the third upcoming week', () => {
  const html = renderToStaticMarkup(<EarningsGrid initialOffset={2} initialFilter="all" />);
  const container = document.createElement('div');
  container.innerHTML = html;
  const next = container.querySelector('button[aria-label="Next week"]');
  expect(next?.hasAttribute('disabled')).toBe(false);
  expect(container.textContent).toContain('In three weeks');
});
