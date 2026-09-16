'use client';

import type { DisplayForecastMethod } from '@/lib/displayForecast';

function fmtPct(value: number | null | undefined): string | null {
  return value != null && Number.isFinite(value) && value > 0
    ? `±${(value * 100).toFixed(1)}%`
    : null;
}

export default function ForecastProvenance({
  method,
  displayPct,
  mlPct,
  optionsPct,
  historicalEventCount,
  asOf,
}: {
  method: DisplayForecastMethod | null | undefined;
  displayPct: number | null;
  mlPct: number | null;
  optionsPct: number | null;
  historicalEventCount?: number | null;
  asOf?: string | null;
}) {
  if (displayPct == null || !Number.isFinite(displayPct) || displayPct <= 0) return null;

  const headline = fmtPct(displayPct);
  const ml = fmtPct(mlPct);
  const options = fmtPct(optionsPct);
  const title =
    method === 'ml'
      ? 'ML forecast'
      : method === 'options_math' || method === 'options_indicative'
        ? 'Options-implied estimate'
        : method === 'historical'
          ? 'Historical estimate'
          : 'Historical prior';

  return (
    <div className="qv-card" data-testid="forecast-provenance" style={{ marginTop: 18, padding: '18px 20px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 18, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--ink-4)', fontWeight: 700 }}>
            Expected move
          </div>
          <div className="serif tnum" style={{ marginTop: 6, fontSize: 34, lineHeight: 1, fontWeight: 800, color: 'var(--ink)' }}>
            {headline}
          </div>
          <div style={{ marginTop: 7, fontSize: 13, color: 'var(--ink-2)', fontWeight: 650 }}>{title}</div>
        </div>
        <div style={{ minWidth: 230, fontSize: 12, lineHeight: 1.55, color: 'var(--ink-3)' }}>
          {method === 'ml' ? (
            <>
              <div>ML forecast <span className="mono tnum" style={{ color: 'var(--ink)' }}>{ml ?? headline}</span></div>
              {options && <div>Options implied <span className="mono tnum" style={{ color: 'var(--ink-2)' }}>{options}</span></div>}
              {asOf && <div style={{ marginTop: 3, color: 'var(--ink-4)' }}>Research snapshot · {asOf}</div>}
            </>
          ) : (
            <>
              <div><strong style={{ color: 'var(--ink-2)' }}>ML unavailable</strong></div>
              {(method === 'options_math' || method === 'options_indicative') && (
                <div>Market implied <span className="mono tnum" style={{ color: 'var(--ink)' }}>{headline}</span></div>
              )}
              {(method === 'historical' || method === 'historical_prior') && (
                <div>Market-implied estimate unavailable</div>
              )}
            </>
          )}
        </div>
      </div>

      {method === 'options_indicative' && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line)', color: 'var(--ink-3)', fontSize: 12.5, lineHeight: 1.5 }}>
          Indicative market estimate; current option quotes did not meet the stricter ML-input quality threshold.
        </div>
      )}
      {method === 'historical' && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line)', color: 'var(--ink-3)', fontSize: 12.5, lineHeight: 1.5 }}>
          Median absolute move across the last {historicalEventCount ?? 'available'} earnings events.
        </div>
      )}
      {method === 'historical_prior' && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line)', color: 'var(--ink-3)', fontSize: 12.5, lineHeight: 1.5 }}>
          Limited company-specific history; estimate uses Quantiv&apos;s recent earnings-event historical baseline.
        </div>
      )}
    </div>
  );
}
