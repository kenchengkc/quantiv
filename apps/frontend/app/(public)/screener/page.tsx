import { Suspense } from 'react';
import type { Metadata } from 'next';
import EarningsScreener from '@/components/EarningsScreener';

export const metadata: Metadata = {
  title: 'Earnings Screener',
};

function ScreenerFallback() {
  const tableHeaders = [
    'Name',
    'Date',
    'DTE',
    'Session',
    'Straddle',
    'Hist avg',
    'Hist edge',
    'ML',
    'Edge',
    'Band',
    'IV',
    'IV Rank',
    'IV crush',
    'Skew',
    '1D',
    'Spot',
    '$ EM',
    'Opt DTE',
  ];

  return (
    <div role="status" aria-busy="true">
      <div style={{ padding: '24px 0 8px' }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-end',
            gap: 16,
            flexWrap: 'wrap',
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div
              style={{
                fontSize: 10,
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                color: 'var(--ink-3)',
                marginBottom: 21,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                minHeight: 22,
              }}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/brand/QuantivIcon.webp"
                alt=""
                width={18}
                height={18}
                style={{
                  display: 'inline-block',
                  objectFit: 'contain',
                  mixBlendMode: 'screen',
                }}
              />
              <span>Options · Earnings</span>
            </div>
            <h1
              className="serif qv-m-h1"
              style={{
                margin: 0,
                fontSize: 66,
                fontWeight: 800,
                letterSpacing: '-0.033em',
                lineHeight: 0.93,
                color: 'var(--ink)',
                textTransform: 'uppercase',
              }}
            >
              Screener
            </h1>
            <div
              style={{
                marginTop: 21,
                fontSize: 16,
                color: 'var(--ink-2)',
                maxWidth: 660,
                lineHeight: 1.55,
                letterSpacing: '-0.005em',
              }}
            >
              Every earnings name on one page. See what options are pricing,
              how it stacks up against recent history and the ML model, and
              where IV sits in its 52-week range.
            </div>
          </div>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'flex-end',
              gap: 4,
              whiteSpace: 'nowrap',
            }}
            aria-hidden="true"
          >
            <div
              className="serif tnum"
              style={{
                fontSize: 32,
                fontWeight: 700,
                letterSpacing: '-0.02em',
                color: 'var(--ink)',
                lineHeight: 1,
                visibility: 'hidden',
                minWidth: 56,
                textAlign: 'right',
              }}
            >
              000
            </div>
            <div
              style={{
                fontSize: 10,
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                color: 'var(--ink-3)',
                fontWeight: 600,
              }}
            >
              names · filtered
            </div>
            <div
              className="mono tnum"
              style={{
                fontSize: 11,
                color: 'var(--ink-4)',
                marginTop: 6,
                visibility: 'hidden',
              }}
            >
              As of 0000-00-00
            </div>
          </div>
        </div>
      </div>

      <div
        className="qv-m-2col"
        aria-hidden="true"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 14,
          marginTop: 10,
        }}
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="qv-screener-insight qv-screener-insight-skeleton"
            style={{
              borderRadius: 14,
              border: '1px solid var(--line)',
              background: 'var(--bg-2)',
              minHeight: 210,
              animation: 'earnings-grid-pulse 1.4s ease-in-out infinite',
              animationDelay: `${i * 90}ms`,
              opacity: 0.55,
            }}
          />
        ))}
      </div>

      <div style={{ padding: '14px 0 0', display: 'flex' }}>
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            color: 'var(--ink-3)',
            fontSize: 11.5,
            fontStyle: 'italic',
          }}
        >
          Sortable table of every upcoming earnings print; stack filters and
          a preset to narrow the universe.
        </span>
      </div>

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 12,
          alignItems: 'center',
          padding: '14px 0 16px',
          borderBottom: '1px solid var(--line)',
        }}
      >
        <div
          className="mono"
          style={{
            width: 100,
            height: 33,
            borderRadius: 8,
            border: '1px solid var(--line)',
            background: 'var(--bg-2)',
          }}
        />
        {['S&P 500', 'ML rows only', 'Min spot ($)', 'All', 'BMO', 'AMC'].map((label) => (
          <span key={label} className="chip" style={{ opacity: 0.72 }}>
            {label}
          </span>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {['Rich vs hist', 'Cheap IV', 'Big movers', 'Tight bands'].map((label) => (
            <span key={label} className="chip" style={{ opacity: 0.72 }}>
              {label}
            </span>
          ))}
        </div>
      </div>

      <div
        className="qv-m-table-wrap qv-screener-table-shell"
        style={{ overflowX: 'auto', marginTop: 0, WebkitOverflowScrolling: 'touch' }}
      >
        <table
          style={{
            tableLayout: 'fixed',
            borderCollapse: 'separate',
            borderSpacing: 0,
            fontSize: 13,
            color: 'var(--ink-2)',
            width: 2030,
          }}
        >
          <thead>
            <tr style={{ borderBottom: '1px solid var(--line)' }}>
              {tableHeaders.map((header) => (
                <th
                  key={header}
                  className="mono"
                  style={{
                    textAlign: header === 'Name' ? 'left' : 'right',
                    padding: '13px 14px',
                    color: 'var(--ink-3)',
                    fontSize: 10.5,
                    letterSpacing: '0.09em',
                    textTransform: 'uppercase',
                    borderBottom: '1px solid var(--line)',
                  }}
                >
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: 12 }).map((_, row) => (
              <tr key={row} style={{ borderBottom: '1px solid var(--line)' }}>
                {tableHeaders.map((header, col) => (
                  <td
                    key={`${header}-${row}`}
                    style={{
                      padding: '16px 14px',
                      borderBottom: '1px solid var(--line)',
                    }}
                  >
                    <div
                      style={{
                        height: col === 0 ? 28 : 12,
                        width: col === 0 ? 172 : 54,
                        marginLeft: col === 0 ? 0 : 'auto',
                        borderRadius: col === 0 ? 7 : 4,
                        background: 'var(--bg-3)',
                        animation: 'earnings-grid-pulse 1.2s ease-in-out infinite',
                        animationDelay: `${row * 45 + col * 10}ms`,
                      }}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function ScreenerPage() {
  return (
    <div className="qv-m-pad" style={{ maxWidth: 1240, margin: '0 auto', padding: '0 28px 60px' }}>
      <Suspense fallback={<ScreenerFallback />}>
        <EarningsScreener />
      </Suspense>
    </div>
  );
}
