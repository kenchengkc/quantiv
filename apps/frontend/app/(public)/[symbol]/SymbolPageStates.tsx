'use client';

import { ChevronLeft } from 'lucide-react';
import { TickerLogo } from '@/components/TickerLogo';
import { companyName } from '@/lib/companyNames';
import { SkeletonBlock } from './SymbolPageHeader';
import type { LivePrice } from './symbolPageTypes';

export function SymbolPageLoading({ symbol }: { symbol: string }) {
  const name = companyName(symbol);

  return (
    <div className="qv-m-pad qv-symbol-page-shell" style={{ maxWidth: 1100, margin: '0 auto', padding: '0 28px 80px' }}>
      <div className="qv-card-hi qv-detail-hero" style={{ padding: '26px 28px', marginTop: 18 }}>
        <div
          className="qv-m-stack qv-detail-hero-grid"
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1.1fr) minmax(0, 1fr)',
            gap: 32,
            alignItems: 'stretch',
          }}
        >
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 18,
              minWidth: 0,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
              <SkeletonBlock width={56} height={56} radius={10} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div
                  style={{
                    fontSize: 11,
                    letterSpacing: '0.16em',
                    textTransform: 'uppercase',
                    color: 'var(--ink-3)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {name}
                </div>
                <div
                  className="serif qv-detail-symbol"
                  style={{
                    fontSize: 46,
                    fontWeight: 800,
                    letterSpacing: '-0.03em',
                    lineHeight: 0.92,
                    color: 'var(--ink)',
                    textTransform: 'uppercase',
                    marginTop: 4,
                  }}
                >
                  {symbol}
                </div>
              </div>
            </div>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'auto auto 1fr',
                alignItems: 'baseline',
                columnGap: 14,
                minHeight: 44,
              }}
            >
              <SkeletonBlock width={128} height={42} radius={7} />
              <SkeletonBlock width={118} height={15} radius={5} />
              <div style={{ justifySelf: 'end' }}>
                <SkeletonBlock width={156} height={12} radius={5} />
              </div>
            </div>

            <div>
              <SkeletonBlock height={42} radius={8} />
              <div
                style={{
                  marginTop: 12,
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: 16,
                  paddingTop: 8,
                  minHeight: 29,
                  borderTop: '1px solid color-mix(in oklab, var(--line) 70%, transparent)',
                }}
              >
                <SkeletonBlock width={214} height={12} radius={5} />
                <SkeletonBlock width={60} height={12} radius={5} />
              </div>
            </div>

            <SkeletonBlock width={168} height={34} radius={999} />
          </div>

          <div
            className="qv-detail-hero-right"
            style={{
              borderLeft: '1px solid color-mix(in oklab, var(--line) 60%, transparent)',
              paddingLeft: 28,
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'space-between',
              gap: 20,
              minWidth: 0,
            }}
          >
            <div>
              <SkeletonBlock width={154} height={28} radius={999} />
              <div style={{ marginTop: 18 }}>
                <SkeletonBlock width={82} height={12} />
                <div style={{ marginTop: 8 }}>
                  <SkeletonBlock width={148} height={62} radius={8} />
                </div>
                <div style={{ marginTop: 12 }}>
                  <SkeletonBlock width={132} height={13} />
                </div>
              </div>
            </div>
            <div
              style={{
                borderTop: '1px solid color-mix(in oklab, var(--line) 60%, transparent)',
                paddingTop: 18,
              }}
            >
              <SkeletonBlock width={168} height={12} />
              <div style={{ marginTop: 14 }}>
                <SkeletonBlock width={194} height={72} radius={10} />
              </div>
              <div style={{ marginTop: 30 }}>
                <SkeletonBlock width={210} height={14} />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div style={{ marginTop: 22 }}>
        <SkeletonBlock height={360} radius={8} />
      </div>

      <div
        className="qv-m-2col"
        style={{
          marginTop: 22,
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 14,
        }}
      >
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="qv-card-hi" style={{ minHeight: 132, padding: '18px 18px' }}>
            <SkeletonBlock width="48%" height={12} />
            <div style={{ marginTop: 18 }}>
              <SkeletonBlock width="72%" height={38} radius={8} />
            </div>
            <div style={{ marginTop: 20 }}>
              <SkeletonBlock width="100%" height={12} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function SymbolPageUnavailable({
  symbol,
  live,
  backLabel,
  onBack,
}: {
  symbol: string;
  live: LivePrice | null;
  backLabel: string;
  onBack: () => void;
}) {
  const tick = live?.symbol === symbol ? live : null;
  const knownName = companyName(symbol);
  const hasFriendlyName = knownName !== symbol;
  const pct = tick?.changePct;
  const change = tick?.change;
  const flat = pct != null && Math.round(pct * 10000) / 10000 === 0;
  const upMove = !flat && (pct ?? 0) >= 0;
  const arrow = pct == null ? '' : flat ? '–' : upMove ? '▲' : '▼';
  const tone = pct == null
    ? 'var(--ink-3)'
    : flat
      ? 'var(--ink-4)'
      : upMove
        ? 'var(--up)'
        : 'var(--down)';

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: '40px 28px' }}>
      <button
        type="button"
        onClick={onBack}
        className="chip"
        style={{
          border: 'none',
          color: 'var(--ink-3)',
          paddingLeft: 0,
          cursor: 'pointer',
        }}
      >
        <ChevronLeft size={14} /> {backLabel}
      </button>

      <div
        style={{
          marginTop: 20,
          display: 'flex',
          alignItems: 'center',
          gap: 18,
          padding: '20px 0 24px',
          borderBottom: '1px solid var(--line)',
        }}
      >
        <TickerLogo
          ticker={symbol}
          size={60}
          radius={10}
          loading="eager"
          fallbackStyle={{
            fontSize: Math.max(14, 60 * 0.32),
            fontWeight: 700,
            letterSpacing: 0,
          }}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            className="serif"
            style={{
              fontSize: 36,
              fontWeight: 800,
              letterSpacing: '-0.025em',
              lineHeight: 1,
            }}
          >
            {symbol}
          </div>
          {hasFriendlyName && (
            <div
              style={{
                marginTop: 6,
                fontSize: 13,
                color: 'var(--ink-3)',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {knownName}
            </div>
          )}
        </div>
        {tick?.price != null && (
          <div style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
            <div
              className="serif tnum"
              style={{
                fontSize: 24,
                fontWeight: 700,
                letterSpacing: '-0.015em',
              }}
            >
              ${tick.price.toFixed(2)}
            </div>
            {pct != null && (
              <div className="mono tnum" style={{ fontSize: 12, color: tone, marginTop: 2 }}>
                {arrow} {change != null ? `$${Math.abs(change).toFixed(2)} ` : ''}({Math.abs(pct * 100).toFixed(2)}%)
              </div>
            )}
          </div>
        )}
      </div>

      <div
        style={{
          marginTop: 24,
          padding: '24px 22px',
          border: '1px solid var(--line)',
          borderRadius: 12,
          background: 'var(--bg-2)',
          color: 'var(--ink-2)',
          fontSize: 13.5,
          lineHeight: 1.55,
        }}
      >
        <div
          style={{
            fontSize: 10,
            letterSpacing: '0.18em',
            textTransform: 'uppercase',
            color: 'var(--ink-4)',
            marginBottom: 8,
          }}
        >
          Options data not tracked
        </div>
        <div
          className="serif"
          style={{
            fontSize: 18,
            fontWeight: 600,
            color: 'var(--ink)',
            marginBottom: 8,
          }}
        >
          We don&apos;t have an options snapshot for {symbol} yet.
        </div>
        <div style={{ color: 'var(--ink-3)', fontSize: 13 }}>
          Expected moves, IV rank, straddle quotes, and the historical implied-vs-actual chart all require an
          options-chain ingest for this symbol. Live spot quotes above{' '}
          {tick?.price != null ? 'are working' : 'will appear when the market is open'}.
        </div>
      </div>
    </div>
  );
}
