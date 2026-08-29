'use client';

import { AlertTriangle } from 'lucide-react';

export type ScenarioRow = {
  multiplier: number;
  shockPct: number;
  price: number;
  pnlPerShare: number;
  pnlPerContract: number;
};

/**
 * Expiry payoff for one long ATM straddle, expressed in market expected-move
 * units. This is intentionally a payoff proxy: it excludes IV repricing,
 * early exercise, fees, and the path before expiry.
 */
export function buildScenarioRows(
  spot: number,
  strike: number,
  premium: number,
  expectedMovePct: number,
): ScenarioRow[] {
  if (
    ![spot, strike, premium, expectedMovePct].every(Number.isFinite) ||
    spot <= 0 ||
    strike <= 0 ||
    premium < 0 ||
    expectedMovePct <= 0
  ) {
    return [];
  }

  return [-1.5, -1, 0, 1, 1.5].map((multiplier) => {
    const shockPct = multiplier * expectedMovePct;
    const price = spot * (1 + shockPct);
    const intrinsic = Math.max(price - strike, 0) + Math.max(strike - price, 0);
    const pnlPerShare = intrinsic - premium;
    return {
      multiplier,
      shockPct,
      price,
      pnlPerShare,
      pnlPerContract: pnlPerShare * 100,
    };
  });
}

function money(value: number): string {
  const sign = value < 0 ? '-' : '';
  return `${sign}$${Math.abs(value).toFixed(0)}`;
}

export default function ScenarioRiskPanel({
  spot,
  strike,
  premium,
  expectedMovePct,
  modelMovePct,
}: {
  spot: number;
  strike: number;
  premium: number;
  expectedMovePct: number;
  modelMovePct: number | null;
}) {
  const rows = buildScenarioRows(spot, strike, premium, expectedMovePct);
  if (rows.length === 0) return null;
  const maxAbsPnl = Math.max(
    ...rows.map((row) => Math.abs(row.pnlPerContract)),
    1,
  );

  return (
    <section className="qv-card" aria-labelledby="earnings-scenario-title">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: 14,
          marginBottom: 14,
        }}
      >
        <div>
          <span
            className="qv-pill"
            style={{
              background: 'color-mix(in oklab, var(--accent) 14%, transparent)',
              color: 'var(--accent-hi)',
              borderColor:
                'color-mix(in oklab, var(--accent) 30%, transparent)',
            }}
          >
            Practical scenario
          </span>
          <h3
            id="earnings-scenario-title"
            className="serif"
            style={{
              margin: '10px 0 0',
              fontSize: 20,
              fontWeight: 700,
              letterSpacing: '-0.01em',
            }}
          >
            Earnings gap scenarios
          </h3>
          <p
            style={{
              fontSize: 13,
              color: 'var(--ink-3)',
              margin: '4px 0 0',
              lineHeight: 1.45,
            }}
          >
            One long ATM straddle held to expiry · before fees and IV repricing.
          </p>
        </div>
        <div
          className="mono tnum"
          style={{
            color: 'var(--ink-3)',
            fontSize: 11,
            textAlign: 'right',
            whiteSpace: 'nowrap',
          }}
        >
          K ${strike.toFixed(2)} · paid ${premium.toFixed(2)}
        </div>
      </div>

      <div style={{ display: 'grid', gap: 7 }}>
        {rows.map((row) => {
          const width = Math.max(
            3,
            (Math.abs(row.pnlPerContract) / maxAbsPnl) * 50,
          );
          const positive = row.pnlPerContract >= 0;
          const label =
            row.multiplier === 0
              ? 'No move'
              : `${row.multiplier > 0 ? '+' : ''}${row.multiplier}× EM`;
          return (
            <div
              key={row.multiplier}
              style={{
                display: 'grid',
                gridTemplateColumns: '72px minmax(0, 1fr) 82px',
                gap: 10,
                alignItems: 'center',
              }}
            >
              <span
                className="mono"
                style={{
                  color: row.multiplier === 0 ? 'var(--ink)' : 'var(--ink-3)',
                  fontSize: 11,
                }}
              >
                {label}
              </span>
              <div
                style={{
                  position: 'relative',
                  height: 8,
                  borderRadius: 999,
                  background: 'var(--bg-3)',
                }}
              >
                <span
                  aria-hidden="true"
                  style={{
                    position: 'absolute',
                    left: '50%',
                    top: -2,
                    bottom: -2,
                    width: 1,
                    background: 'var(--line-2)',
                  }}
                />
                <div
                  style={{
                    position: 'absolute',
                    left: positive ? '50%' : `${50 - width}%`,
                    width: `${width}%`,
                    height: '100%',
                    borderRadius: 999,
                    background: positive ? 'var(--up)' : 'var(--down)',
                    opacity: 0.82,
                  }}
                />
              </div>
              <span
                className="mono tnum"
                style={{
                  color: positive ? 'var(--up)' : 'var(--down)',
                  fontSize: 12,
                  textAlign: 'right',
                  fontWeight: 600,
                }}
              >
                {money(row.pnlPerContract)}
              </span>
            </div>
          );
        })}
      </div>

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 8,
          alignItems: 'center',
          marginTop: 14,
          paddingTop: 11,
          borderTop: '1px solid var(--line)',
          color: 'var(--ink-4)',
          fontSize: 11,
        }}
      >
        <span className="mono">
          Move unit ±{(expectedMovePct * 100).toFixed(1)}% · spot $
          {spot.toFixed(2)}
        </span>
        {modelMovePct != null && Number.isFinite(modelMovePct) ? (
          <span className="mono">
            Model midpoint ±{(modelMovePct * 100).toFixed(1)}%
          </span>
        ) : null}
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <AlertTriangle size={12} />
          Expiry payoff proxy, not a forecast.
        </span>
      </div>
    </section>
  );
}
