import type { ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import MoveComparisonChart from './MoveComparisonChart';

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

const quantiles = {
  p10: 0.025,
  p25: 0.034,
  p50: 0.047,
  p75: 0.061,
  p90: 0.08,
};

describe('MoveComparisonChart', () => {
  it('compares market, model distribution, and history in one chart', () => {
    const onModeChange = vi.fn();
    const container = render(
      <MoveComparisonChart
        spot={100}
        optionsMovePct={0.072}
        modelMovePct={0.041}
        modelQuantiles={quantiles}
        modelIsSpotUpdated={false}
        historicalMovePct={0.053}
        historyCount={8}
        ivRank={0.36}
        mode="snapshot"
        onModeChange={onModeChange}
        spotUpdateDisabled={false}
        spotUpdateStatus="idle"
        modelMeta="Nightly model"
        unavailableReason={null}
      />,
    );

    expect(container.textContent).toContain('Market vs model vs history');
    expect(container.textContent).toContain('Options-implied±7.2%$93–$107');
    expect(container.textContent).toContain('Nightly model±4.1%$96–$104');
    expect(container.textContent).toContain('Historical median±5.3%$95–$105');
    expect(container.textContent).toContain(
      'Options price 3.1 percentage points more movement than the model.',
    );
    expect(container.textContent).toContain('Straddle exceedance ≈16%');
    expect(container.textContent).toContain(
      'Quantile interpolation · before spreads, fees, and post-event IV change',
    );
    expect(container.textContent).toContain('Light band P10–P90');

    const spotUpdatedTab = Array.from(
      container.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
    ).find((button) => button.textContent === 'Spot-updated');
    spotUpdatedTab?.click();
    expect(onModeChange).toHaveBeenCalledWith('live');
  });

  it('omits the model row when no forecast exists', () => {
    const container = render(
      <MoveComparisonChart
        spot={100}
        optionsMovePct={0.039}
        modelMovePct={null}
        modelQuantiles={null}
        modelIsSpotUpdated={false}
        historicalMovePct={0.019}
        historyCount={8}
        ivRank={0.46}
        mode="snapshot"
        onModeChange={() => undefined}
        spotUpdateDisabled
        spotUpdateStatus="unavailable"
        modelMeta=""
        unavailableReason={null}
      />,
    );

    expect(container.textContent).toContain('Options-implied±3.9%');
    expect(container.textContent).toContain('Historical median±1.9%');
    expect(container.textContent).not.toContain('model±');
    expect(container.textContent).not.toContain('Unavailable');
  });
});
