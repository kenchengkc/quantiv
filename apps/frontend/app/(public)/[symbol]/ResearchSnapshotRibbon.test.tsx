import type { ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import ResearchSnapshotRibbon from './ResearchSnapshotRibbon';

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

describe('ResearchSnapshotRibbon', () => {
  it('shows one concise ticker-level lineage without receipt hashes', () => {
    const container = render(
      <ResearchSnapshotRibbon
        evidence={{
          validated_at: '2026-08-30T17:31:26Z',
          receipt_id: 'sha256:do-not-render',
          quality: { status: 'passed', issue_count: 0 },
          coverage: { rows: 91, symbols: 39, events: 39, horizons: [3, 7, 14, 21] },
          controls: { evaluated: 24, exceptions: 0 },
        }}
        optionsDate="2026-08-28"
        earningsDate="2026-09-10"
        earningsTiming="amc"
        modelSnapshotDate="2026-08-28"
        modelHorizon={14}
      />,
    );

    expect(container.textContent).toContain('Research snapshot');
    expect(container.textContent).toContain('Options snapshotAug 28');
    expect(container.textContent).toContain('Earnings eventSep 10after close');
    expect(container.textContent).toContain('Forecast modelT-14 model');
    expect(container.textContent).toContain('Forecast checks24 controls0 exceptions');
    expect(container.textContent).toContain('End-of-day research');
    expect(container.textContent).not.toContain('sha256');
  });
});
