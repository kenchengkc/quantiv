import type { ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SymbolPageLoading, SymbolPageUnavailable } from './SymbolPageStates';

const roots: Root[] = [];

function render(ui: ReactNode): HTMLElement {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  roots.push(root);
  flushSync(() => root.render(ui));
  return container;
}

afterEach(() => {
  roots.splice(0).forEach((root) => {
    flushSync(() => root.unmount());
  });
  document.body.replaceChildren();
});

describe('ticker page states', () => {
  it('keeps the requested symbol visible while detail data loads', () => {
    const container = render(<SymbolPageLoading symbol="AAPL" />);

    expect(container.textContent).toContain('AAPL');
  });

  it('explains missing options coverage without presenting a broken page', () => {
    const onBack = vi.fn();
    const container = render(
      <SymbolPageUnavailable
        symbol="ACME"
        live={null}
        backLabel="Earnings Calendar"
        onBack={onBack}
      />,
    );

    expect(container.textContent).toContain('Options data not tracked');
    expect(container.textContent).toContain("We don't have an options snapshot for ACME yet.");
    const back = container.querySelector('button');
    expect(back?.textContent).toContain('Earnings Calendar');
    back?.click();
    expect(onBack).toHaveBeenCalledOnce();
  });
});
