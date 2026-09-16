import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ForecastProvenance from './ForecastProvenance';

describe('ForecastProvenance', () => {
  it('explains indicative options without implying the model is broken', () => {
    render(
      <ForecastProvenance
        method="options_indicative"
        displayPct={0.083}
        mlPct={null}
        optionsPct={null}
        asOf="2026-09-14"
      />,
    );
    expect(screen.getByText('Options-implied estimate')).toBeTruthy();
    expect(screen.getByText('ML unavailable')).toBeTruthy();
    expect(
      screen.getByText(
        'Indicative market estimate; current option quotes did not meet the stricter ML-input quality threshold.',
      ),
    ).toBeTruthy();
  });

  it('explains ticker-history fallback', () => {
    render(
      <ForecastProvenance
        method="historical"
        displayPct={0.029}
        mlPct={null}
        optionsPct={null}
        historicalEventCount={4}
      />,
    );
    expect(screen.getByText('Historical estimate')).toBeTruthy();
    expect(screen.getByText('Market-implied estimate unavailable')).toBeTruthy();
    expect(screen.getByText('Median absolute move across the last 4 earnings events.')).toBeTruthy();
  });

  it('explains broad historical prior', () => {
    render(
      <ForecastProvenance
        method="historical_prior"
        displayPct={0.058}
        mlPct={null}
        optionsPct={null}
        historicalEventCount={1}
      />,
    );
    expect(screen.getByText('Historical prior')).toBeTruthy();
    expect(
      screen.getByText(
        "Limited company-specific history; estimate uses Quantiv's recent earnings-event historical baseline.",
      ),
    ).toBeTruthy();
  });
});
