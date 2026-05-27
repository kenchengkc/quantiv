'use client';

import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ChevronLeft } from 'lucide-react';
import { SignedIn, SignedOut, SignInButton } from '@clerk/nextjs';
import { companyName } from '@/lib/companyNames';
import { listingExchangeLabel } from '@/lib/listingExchanges';
import { useEnsureCompanyNames } from '@/lib/useCompanyNames';
import { useEnsureListingExchanges } from '@/lib/useListingExchanges';
import { useWatchlist } from '@/lib/watchlist';
import { TickerLogo } from '@/components/TickerLogo';

interface Straddle {
  expiration: string;
  dte: number;
  atm_strike: number;
  atm_iv: number | null;
  atm_call_iv: number | null;
  atm_put_iv: number | null;
  straddle_mid: number | null;
  em_straddle: number | null;
  em_straddle_pct: number | null;
  em_iv: number | null;
  em_iv_pct: number | null;
  call_delta: number | null;
  call_gamma: number | null;
  call_vega: number | null;
  call_theta: number | null;
}

interface ExpectedMove {
  earnings_date?: string;
  expiration: string;
  dte: number;
  lead_time_days?: number;
  atm_strike: number;
  atm_iv: number | null;
  straddle_abs: number | null;
  straddle_pct: number | null;
  iv_pct: number | null;
  skew_atm?: number | null;
  term_slope?: number | null;
  total_vega?: number | null;
  timing?: string;
  em_method?: 'options_math' | 'ml_lightgbm' | 'ensemble';
  em_ml_pct?: number | null;
  em_ml_abs?: number | null;
  correction_factor?: number | null;
  model_horizon?: number | null;
  ml_snapshot_date?: string | null;
  p10?: number | null;
  p25?: number | null;
  p50?: number | null;
  p75?: number | null;
  p90?: number | null;
}

interface VolRegime {
  iv_current: number | null;
  iv_rank: number | null;
  iv_year_high: number | null;
  iv_year_low: number | null;
  hv_current: number | null;
  hv_rank: number | null;
  iv_mom_week: number | null;
  iv_mom_month: number | null;
}

interface SymbolDetail {
  symbol: string;
  as_of_date: string;
  spot_price: number | null;
  expected_move?: ExpectedMove;
  straddle_features: Straddle[];
  earnings_history?: Array<{
    date: string;
    timing: string;
    /** Quarter label like "Q1 25" — set by the build from Finnhub
     *  fiscal_year/fiscal_q so it's correct for non-calendar fiscal
     *  years (AAPL, NVDA, ADBE…). Falls back to a date-derived guess. */
    q?: string;
    fiscal_year?: number | null;
    fiscal_q?: string | null;
    /** Implied move at the time, decimal fraction (e.g. 0.045 = 4.5%). */
    implied?: number | null;
    /** Realized close-to-close move, signed decimal fraction. */
    actual?: number | null;
    /** Non-GAAP EPS actual / estimate from Finnhub. */
    eps_actual?: number | null;
    eps_estimate?: number | null;
    /** Signed EPS surprise = (actual - estimate) / |estimate|. */
    eps_surprise_pct?: number | null;
    revenue_actual?: number | null;
    revenue_estimate?: number | null;
    /** Signed revenue surprise = (actual - estimate) / |estimate|. */
    rev_surprise_pct?: number | null;
  }>;
  next_earnings?: string | null;
  next_earnings_timing?: string;
  vol_regime?: VolRegime | null;
}

interface LivePrice {
  symbol: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
  updated: string | null;
  source: 'finnhub' | 'alpaca_iex' | 'mixed' | 'unavailable';
  session?: 'premarket' | 'regular' | 'afterhours' | 'closed';
  marketOpen: boolean;
}

type PredictionMode = 'snapshot' | 'live';
type LivePredictionStatus = 'idle' | 'loading' | 'ready' | 'unavailable';

interface LivePredictionResponse {
  symbol: string;
  horizon_days: number;
  em_ml_pct: number;
  em_ml_abs: number;
  quantiles: Record<string, number>;
  spot_used: number;
  feature_snapshot_date: string | null;
  earnings_date: string | null;
  source: 'live' | 'cached' | 'nightly_fallback';
  fallback_kind?: 'static_ml' | 'straddle';
  fallback_reason?: string;
  served_at: string;
  snapshot_age_days?: number | null;
  forecast_scored_at?: string | null;
  model_version?: string | null;
  model_trained_at?: string | null;
  model_loaded_at?: string | null;
  feature_schema_hash?: string | null;
}

interface LivePredictionState {
  status: LivePredictionStatus;
  key: string | null;
  response: LivePredictionResponse | null;
  error: string | null;
  updatedAt: number;
}

function SkeletonBlock({
  width = '100%',
  height,
  radius = 6,
}: {
  width?: string | number;
  height: number;
  radius?: number;
}) {
  return (
    <div
      aria-hidden="true"
      style={{
        width,
        height,
        borderRadius: radius,
        background: 'var(--bg-3)',
        animation: 'earnings-grid-pulse 1.2s ease-in-out infinite',
      }}
    />
  );
}

function SymbolPageLoading({ symbol }: { symbol: string }) {
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
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18, minWidth: 0 }}>
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

      <div
        className="qv-m-2col"
        style={{
          marginTop: 22,
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 14,
        }}
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="qv-card-hi" style={{ minHeight: 132, padding: '18px 18px' }}>
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

      <div style={{ marginTop: 24 }}>
        <SkeletonBlock height={360} radius={8} />
      </div>
    </div>
  );
}

const EMPTY_LIVE_PREDICTION: LivePredictionState = {
  status: 'idle',
  key: null,
  response: null,
  error: null,
  updatedAt: 0,
};

function normalizeQuantiles(
  quantiles: Record<string, number> | null | undefined,
): { p10: number; p25: number; p50: number; p75: number; p90: number } | null {
  if (!quantiles) return null;
  const p10 = quantiles['10'];
  const p50 = quantiles['50'];
  const p90 = quantiles['90'];
  if (![p10, p50, p90].every((v) => typeof v === 'number' && Number.isFinite(v))) {
    return null;
  }
  const p25 = quantiles['25'];
  const p75 = quantiles['75'];
  return {
    p10,
    p25: typeof p25 === 'number' && Number.isFinite(p25) ? p25 : p10,
    p50,
    p75: typeof p75 === 'number' && Number.isFinite(p75) ? p75 : p90,
    p90,
  };
}

function initialSymbolDetail(value: unknown, symbol: string): SymbolDetail | null {
  if (!value || typeof value !== 'object') return null;
  const detail = value as Partial<SymbolDetail>;
  if (typeof detail.symbol !== 'string') return null;
  if (detail.symbol.toUpperCase() !== symbol) return null;
  if (typeof detail.as_of_date !== 'string') return null;
  if (!Array.isArray(detail.straddle_features)) return null;
  return detail as SymbolDetail;
}

function livePredictionUnavailableMessage(status: number | null): string {
  if (status === 404) {
    return 'No fresh feature snapshot is available for this event yet.';
  }
  if (status === 400 || status === 422) {
    return 'This snapshot is not supported by the live model.';
  }
  return 'Live prediction is unavailable right now.';
}

function parseLocalDate(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}
function shortDate(iso: string | undefined | null) {
  if (!iso) return '—';
  return parseLocalDate(iso).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

/** Tighter date label for chart axes / greeks-table expiry column.
 *  Drops the weekday prefix so labels read as "Jun 26" instead of
 *  "Fri, Jun 26" — the longer form crowds the term-structure fan's
 *  x-axis when expiries cluster within a few days of each other. */
function axisDate(iso: string | undefined | null) {
  if (!iso) return '—';
  return parseLocalDate(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
}
function daysFromToday(iso?: string | null): number | null {
  if (!iso) return null;
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  if (!y || !m || !d) return null;
  const target = new Date(y, m - 1, d);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}
function timingText(t?: string | null) {
  const k = (t || '').toLowerCase();
  if (k === 'bmo' || k === 'before_market_open' || k === 'before_open') return 'Before open';
  if (k === 'amc' || k === 'after_market_close' || k === 'after_close') return 'After close';
  return null;
}

type EarningsHistoryRow = NonNullable<SymbolDetail['earnings_history']>[number];

function quarterNumber(q?: string | null): number | null {
  const m = /^Q([1-4])$/i.exec((q ?? '').trim());
  return m ? Number(m[1]) : null;
}

function fullYear(year?: number | string | null): number | null {
  if (year == null) return null;
  const n = typeof year === 'number' ? year : Number(year);
  if (!Number.isFinite(n)) return null;
  return n < 100 ? 2000 + n : n;
}

function quarterFromRow(row?: EarningsHistoryRow | null): { q: number; year: number } | null {
  if (!row) return null;

  const fiscalQ = quarterNumber(row.fiscal_q);
  const fiscalYear = fullYear(row.fiscal_year);
  if (fiscalQ && fiscalYear) return { q: fiscalQ, year: fiscalYear };

  const m = /Q([1-4])\s*(\d{2,4})/i.exec(row.q ?? '');
  if (!m) return null;
  return { q: Number(m[1]), year: fullYear(m[2]) ?? 2000 + Number(m[2]) };
}

function incrementQuarter(q: { q: number; year: number }): { q: number; year: number } {
  if (q.q >= 4) return { q: 1, year: q.year + 1 };
  return { q: q.q + 1, year: q.year };
}

function formatQuarterLabel(q: { q: number; year: number }): string {
  return `Q${q.q} ${q.year}`;
}

function eventLabelFor(data: SymbolDetail, earningsDate: string | null): string {
  const history = data.earnings_history ?? [];
  const eventDate = earningsDate?.slice(0, 10) ?? null;

  if (eventDate) {
    const exact = history.find((row) => row.date.slice(0, 10) === eventDate);
    const exactQuarter = quarterFromRow(exact);
    if (exactQuarter) return `${formatQuarterLabel(exactQuarter)} Earnings`;
  }

  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const future = history
    .filter((row) => parseLocalDate(row.date).getTime() >= today)
    .sort((a, b) => parseLocalDate(a.date).getTime() - parseLocalDate(b.date).getTime())
    .find((row) => quarterFromRow(row));
  const futureQuarter = quarterFromRow(future);
  if (futureQuarter) return `${formatQuarterLabel(futureQuarter)} Earnings`;

  const latestBeforeEvent = history
    .filter((row) => !eventDate || row.date.slice(0, 10) < eventDate)
    .sort((a, b) => parseLocalDate(b.date).getTime() - parseLocalDate(a.date).getTime())
    .find((row) => quarterFromRow(row));
  const previousQuarter = quarterFromRow(latestBeforeEvent);
  if (previousQuarter) return `${formatQuarterLabel(incrementQuarter(previousQuarter))} Earnings`;

  return 'Upcoming earnings';
}

/** One-line summary of where the spot price came from. Distinguishes
 *  live regular-hours vs. last-close vs. extended-hours IEX so the hero
 *  is honest about what the user is looking at. */
function quoteSourceLabel(live: LivePrice | null, ticker: string, asOfDate: string): string {
  if (!live) return `Snapshot · ${asOfDate}`;
  if (live.source === 'alpaca_iex') {
    return live.session === 'premarket' || live.session === 'afterhours'
      ? 'Live extended hours · IEX'
      : 'Last quote · IEX';
  }
  if (live.source === 'finnhub') {
    const exchange = listingExchangeLabel(ticker);
    return live.marketOpen ? `Live · ${exchange}` : `Last close · ${exchange}`;
  }
  if (live.source === 'mixed') return 'Live · mixed venues';
  return `As of ${asOfDate}`;
}

// ---------- math helpers ----------
function erf(x: number) {
  const sign = x < 0 ? -1 : 1;
  x = Math.abs(x);
  const a1 = 0.254829592,
    a2 = -0.284496736,
    a3 = 1.421413741,
    a4 = -1.453152027,
    a5 = 1.061405429,
    p = 0.3275911;
  const t = 1 / (1 + p * x);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
  return sign * y;
}
function normCDF(z: number) {
  return 0.5 * (1 + erf(z / Math.SQRT2));
}
function formatSvgNumber(value: number) {
  if (!Number.isFinite(value)) return '0';
  return value.toFixed(4).replace(/\.?0+$/, '');
}

// ---------- Watchlist + Toast ----------
function WatchlistButton({
  ticker,
  onToast,
}: {
  ticker: string;
  onToast: (msg: string) => void;
}) {
  const { symbols, add, remove } = useWatchlist();
  const added = symbols.includes(ticker);
  const [animating, setAnimating] = useState(false);
  const [hovered, setHovered] = useState(false);

  const toggle = () => {
    if (animating) return;
    if (added) {
      onToast(`${ticker} removed from watchlist`);
      remove(ticker);
    } else {
      setAnimating(true);
      onToast(`${ticker} added to watchlist`);
      setTimeout(() => setAnimating(false), 700);
      add(ticker);
    }
  };

  const size = 30;
  const dots = Array.from({ length: 6 }, (_, i) => {
    const angle = (i / 6) * Math.PI * 2;
    return {
      dx: `${Math.cos(angle) * 22}px`,
      dy: `${Math.sin(angle) * 22}px`,
      delay: i * 10,
    };
  });

  return (
    <button
      onClick={toggle}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      title={added ? 'Remove from watchlist' : 'Add to watchlist'}
      aria-pressed={added}
      style={{
        position: 'relative',
        width: size,
        height: size,
        display: 'grid',
        placeItems: 'center',
        borderRadius: '50%',
        background: 'transparent',
        padding: 0,
        border: 'none',
        cursor: 'pointer',
      }}
    >
      {!added && (
        <svg
          width={size}
          height={size}
          viewBox="0 0 24 24"
          fill="none"
          style={{
            position: 'absolute',
            inset: 0,
            color: hovered ? 'var(--ink)' : 'var(--ink-3)',
            transition: 'color 160ms ease',
          }}
        >
          <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.6" />
          <path
            d="M12 8v8M8 12h8"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
      )}
      {animating &&
        dots.map((d, i) => (
          <span
            key={i}
            style={
              {
                position: 'absolute',
                width: 5,
                height: 5,
                borderRadius: 999,
                background: 'var(--up)',
                top: '50%',
                left: '50%',
                marginTop: -2.5,
                marginLeft: -2.5,
                '--dx': d.dx,
                '--dy': d.dy,
                animation: `splash-dot 550ms cubic-bezier(.2,.6,.3,1) ${d.delay}ms forwards`,
              } as React.CSSProperties
            }
          />
        ))}
      {animating && (
        <span
          style={{
            position: 'absolute',
            inset: 0,
            borderRadius: '50%',
            border: '2px solid var(--up)',
            animation: 'splash-ring 550ms cubic-bezier(.2,.6,.3,1) forwards',
          }}
        />
      )}
      {added && (
        <span
          style={{
            position: 'absolute',
            inset: 0,
            background: 'var(--up)',
            borderRadius: '50%',
            display: 'grid',
            placeItems: 'center',
            animation: animating
              ? 'pop-in 450ms cubic-bezier(.2,.8,.3,1.2) 180ms both'
              : 'none',
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path
              d="M5 12l5 5 9-11"
              stroke="#0b0e14"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{
                strokeDasharray: 22,
                animation: animating ? 'check-draw 320ms ease-out 280ms both' : 'none',
              }}
            />
          </svg>
        </span>
      )}
    </button>
  );
}

function Toast({ message, onDone }: { message: string; onDone: () => void }) {
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    const t1 = setTimeout(() => setLeaving(true), 2000);
    const t2 = setTimeout(onDone, 2280);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [onDone]);
  return (
    <div
      style={{
        position: 'fixed',
        top: 72,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 200,
        background: 'var(--bg-3)',
        border: '1px solid var(--line-2)',
        borderRadius: 8,
        padding: '10px 16px 10px 12px',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        fontSize: 12.5,
        color: 'var(--ink-2)',
        boxShadow: '0 16px 48px rgba(0,0,0,.5)',
        animation: leaving
          ? 'toast-out 280ms ease forwards'
          : 'toast-in 260ms cubic-bezier(.2,.8,.3,1) both',
      }}
    >
      <span
        style={{
          width: 18,
          height: 18,
          borderRadius: 999,
          background: 'var(--up)',
          display: 'grid',
          placeItems: 'center',
          flexShrink: 0,
        }}
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
          <path
            d="M5 12l5 5 9-11"
            stroke="#0b0e14"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      {message}
    </div>
  );
}

// ---------- KPI card ----------
function KpiCard({
  label,
  value,
  sub,
  accent,
  kicker,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
  kicker?: string;
}) {
  return (
    <div
      className="qv-card"
      style={{
        padding: '16px 18px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      {kicker && (
        <span
          className="qv-pill"
          style={{
            alignSelf: 'flex-start',
            fontSize: 9.5,
            padding: '3px 8px',
            background: 'transparent',
            color: 'var(--ink-4)',
            borderColor: 'var(--line)',
          }}
        >
          {kicker}
        </span>
      )}
      <div
        style={{
          fontSize: 10,
          letterSpacing: '0.16em',
          textTransform: 'uppercase',
          color: 'var(--ink-3)',
          marginTop: kicker ? 2 : 0,
        }}
      >
        {label}
      </div>
      <div
        className="serif tnum"
        style={{
          fontSize: 30,
          fontWeight: 700,
          lineHeight: 1,
          letterSpacing: '-0.02em',
          color: accent || 'var(--ink)',
          marginTop: 4,
        }}
      >
        {value}
      </div>
      {sub && (
        <div
          className="mono tnum"
          style={{ fontSize: 12.5, color: 'var(--ink-4)', marginTop: 4 }}
        >
          {sub}
        </div>
      )}
    </div>
  );
}

// ---------- Hero sparkline ----------
//
// Renders the 1D price curve under the ticker symbol + spot price. Draws
// the real Alpaca IEX 5-min bars when available. While bars are loading,
// render a neutral placeholder so the page does not flash a fake red/green
// price path before real data arrives.
type SparkBar = { t: string; c: number };

function buildNeutralSparkPlaceholder(): { x: number; y: number }[] {
  const n = 18;
  const arr: { x: number; y: number }[] = [];
  for (let i = 0; i < n; i++) {
    arr.push({
      x: i / (n - 1),
      y: 0.5 + Math.sin(i * 0.85) * 0.045,
    });
  }
  return arr;
}

function percentile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const pos = (sorted.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  const next = sorted[base + 1];
  if (next == null) return sorted[base];
  return sorted[base] + rest * (next - sorted[base]);
}

function HeroSpark({
  ticker,
  up,
  bars,
  loading,
}: {
  ticker: string;
  up: boolean;
  bars: SparkBar[] | null;
  loading: boolean;
}) {
  const pts = useMemo(() => {
    if (bars && bars.length >= 2) {
      // Normalize real bars with a robust central range. A single odd IEX
      // print should not flatten the rest of the session into a nearly
      // horizontal line.
      const closes = bars.map((b) => b.c);
      const sorted = [...closes].sort((a, b) => a - b);
      const mid = closes.reduce((sum, value) => sum + value, 0) / closes.length;
      let lo = sorted[0];
      let hi = sorted[sorted.length - 1];
      if (sorted.length >= 8) {
        const q10 = percentile(sorted, 0.1);
        const q90 = percentile(sorted, 0.9);
        const innerSpan = q90 - q10;
        if (innerSpan > 0) {
          lo = q10 - innerSpan * 0.32;
          hi = q90 + innerSpan * 0.32;
        }
      }
      const minSpan = Math.max(Math.abs(mid) * 0.0015, 0.01);
      if (hi - lo < minSpan) {
        const pad = (minSpan - (hi - lo)) / 2;
        lo -= pad;
        hi += pad;
      }
      const span = hi - lo || 1;
      const n = bars.length;
      return bars.map((b, i) => ({
        x: n === 1 ? 0.5 : i / (n - 1),
        // Invert so lower price → lower on screen (SVG y grows down).
        y: Math.max(0.04, Math.min(0.96, 1 - (b.c - lo) / span)),
      }));
    }
    return buildNeutralSparkPlaceholder();
  }, [bars]);

  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p.x * 100},${p.y * 36}`).join(' ');
  const area =
    'M 0 36 ' + pts.map((p) => `L ${p.x * 100},${p.y * 36}`).join(' ') + ' L 100 36 Z';
  const isReal = !!bars && bars.length >= 2;
  const stroke = isReal ? (up ? 'var(--up)' : 'var(--down)') : 'var(--ink-4)';
  return (
    <svg
      viewBox="0 0 100 36"
      preserveAspectRatio="none"
      width="100%"
      height="36"
      style={{
        overflow: 'visible',
        opacity: isReal ? 1 : loading ? 0.34 : 0.26,
        transition: 'opacity 220ms ease',
      }}
      aria-label={isReal ? `${ticker} 1D intraday chart` : `${ticker} placeholder chart`}
    >
      <defs>
        <linearGradient id={`spark-fill-${ticker}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.32" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#spark-fill-${ticker})`} />
      <path
        d={d}
        stroke={stroke}
        strokeWidth={isReal ? 1.15 : 0.9}
        fill="none"
        strokeDasharray={isReal ? undefined : '3 4'}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

// ---------- Back-to-calendar button ----------
// Pill button used in the DetailHero left column. Mirrors the qv-card hover
// treatment (border lifts from --line → --line-2, content brightens) so it
// reads as interactive alongside the cards below it.
function BackToCalendarButton({
  onClick,
  label,
}: {
  onClick: () => void;
  label: string;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onFocus={() => setHover(true)}
      onBlur={() => setHover(false)}
      style={{
        alignSelf: 'flex-start',
        marginTop: 'auto',
        padding: '8px 16px',
        border: `1px solid ${hover ? 'var(--line-2)' : 'var(--line)'}`,
        borderRadius: 999,
        fontSize: 12,
        letterSpacing: '0.08em',
        color: hover ? 'var(--ink)' : 'var(--ink-3)',
        background: hover
          ? 'color-mix(in oklab, var(--bg-3) 55%, transparent)'
          : 'transparent',
        display: 'inline-flex',
        gap: 7,
        alignItems: 'center',
        cursor: 'pointer',
        transition:
          'background 160ms ease, color 160ms ease, border-color 160ms ease',
      }}
    >
      <ChevronLeft size={14} /> {label}
    </button>
  );
}

// Reads the previous in-app pathname (recorded by PrevRouteTracker in
// providers.tsx) and maps it to a back-button label + landing path. The
// path is only consulted as a fallback when the browser has no history
// entry to pop — the click handler still prefers router.back() so the
// scroll position and URL params on /screener are preserved.
function usePrevAppLocation(): { label: string; path: string } {
  const [loc, setLoc] = useState<{ label: string; path: string }>({
    label: 'Earnings Calendar',
    path: '/',
  });
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const prev = window.sessionStorage.getItem('quantiv:prevRoute');
    if (prev === '/screener') setLoc({ label: 'Screener', path: '/screener' });
    else if (prev === '/watchlist') setLoc({ label: 'Watchlist', path: '/watchlist' });
    else setLoc({ label: 'Earnings Calendar', path: '/' });
  }, []);
  return loc;
}

// ---------- Detail hero (gradient split card) ----------
function DetailHero({
  ticker,
  symbol,
  spot,
  change,
  changePct,
  quotePending,
  emPct,
  daysLeft,
  earningsDate,
  earningsTiming,
  eventLabel,
  quoteLabel,
  intradayBars,
  intradaySessionPct,
  intradayLoading,
  intradaySessionDate,
  intradayIsCurrentSession,
  onBack,
  backLabel,
  onToast,
}: {
  ticker: string;
  symbol: string;
  spot: number;
  change: number;
  changePct: number;
  quotePending: boolean;
  emPct: number;
  daysLeft: number | null;
  earningsDate: string | null;
  earningsTiming: string | null;
  eventLabel: string;
  quoteLabel: string;
  /** Real 5-min IEX bars for the 1D sparkline. `null` while loading or
   *  when the API returns no data. The chart renders a neutral placeholder
   *  in that case so it never looks like real red/green price action. */
  intradayBars: SparkBar[] | null;
  /** Real session % computed from bars (firstBar.c → lastBar.c).
   *  `null` when bars aren't available. */
  intradaySessionPct: number | null;
  intradayLoading: boolean;
  intradaySessionDate: string | null;
  intradayIsCurrentSession: boolean | null;
  onBack: () => void;
  backLabel: string;
  onToast: (msg: string) => void;
}) {
  const flat =
    Math.round(change * 100) / 100 === 0 &&
    Math.round(changePct * 10000) / 10000 === 0;
  const up = !flat && change >= 0;
  const sessionPct = intradaySessionPct;
  // The sparkline color tracks the session direction once real bars exist.
  // The placeholder is always neutral.
  const sparkUp = intradaySessionPct != null ? intradaySessionPct >= 0 : up;
  const sparkCaption = (() => {
    if (intradayLoading) return 'IEX bars loading';
    if (!intradayBars || intradayBars.length < 2) return 'IEX bars unavailable';
    if (!intradaySessionDate) return 'IEX · latest session';
    const d = parseLocalDate(intradaySessionDate);
    const dateLabel = d.toLocaleDateString('en-US', { month: 'short', day: '2-digit' });
    return intradayIsCurrentSession
      ? 'IEX · today · 08:00-17:00 ET'
      : `IEX · latest session · ${dateLabel}`;
  })();
  const lower = spot * (1 - emPct);
  const upper = spot * (1 + emPct);
  const earningsLine = (() => {
    if (!earningsDate) return null;
    const d = parseLocalDate(earningsDate);
    const dayLabel = d.toLocaleDateString('en-US', {
      weekday: 'short',
      day: '2-digit',
      month: 'short',
    });
    const timing = earningsTiming ? ` · ${earningsTiming}` : '';
    return `${dayLabel}${timing}`;
  })();
  return (
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
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <TickerLogo
              ticker={symbol}
              size={56}
              radius={10}
              loading="eager"
              fallbackStyle={{
                fontSize: Math.max(14, 56 * 0.32),
                fontWeight: 700,
                letterSpacing: 0,
              }}
            />
            <div style={{ minWidth: 0 }}>
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
                {ticker}
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
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                }}
              >
                {symbol}
                <SignedIn>
                  <WatchlistButton ticker={symbol} onToast={onToast} />
                </SignedIn>
                <SignedOut>
                  <SignInButton mode="modal">
                    <button
                      title="Sign in to add to watchlist"
                      style={{
                        display: 'grid',
                        placeItems: 'center',
                        width: 30,
                        height: 30,
                        borderRadius: '50%',
                        background: 'transparent',
                        border: 'none',
                        padding: 0,
                        cursor: 'pointer',
                        color: 'var(--ink-3)',
                      }}
                    >
                      <svg width="30" height="30" viewBox="0 0 24 24" fill="none">
                        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.4" />
                        <path d="M12 8v8M8 12h8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                      </svg>
                    </button>
                  </SignInButton>
                </SignedOut>
              </div>
            </div>
          </div>

          <div
            aria-busy={quotePending}
            aria-label={quotePending ? 'Loading live quote' : undefined}
            style={{
              display: 'grid',
              gridTemplateColumns: 'auto auto 1fr',
              alignItems: 'baseline',
              columnGap: 14,
              minHeight: 44,
            }}
          >
            {quotePending ? (
              <>
                <SkeletonBlock width={128} height={42} radius={7} />
                <SkeletonBlock width={118} height={15} radius={5} />
                <div style={{ justifySelf: 'end' }}>
                  <SkeletonBlock width={156} height={12} radius={5} />
                </div>
              </>
            ) : (
              <>
                <span
                  className="serif tnum"
                  style={{ fontSize: 36, fontWeight: 700, letterSpacing: '-0.02em' }}
                >
                  ${spot.toFixed(2)}
                </span>
                <span
                  className="mono tnum"
                  style={{
                    fontSize: 13,
                    color: flat ? 'var(--ink-4)' : up ? 'var(--up)' : 'var(--down)',
                  }}
                >
                  {flat ? '–' : up ? '▲' : '▼'} {Math.abs(change).toFixed(2)} (
                  {(Math.abs(changePct) * 100).toFixed(2)}%)
                </span>
                <span
                  title={quoteLabel}
                  style={{
                    justifySelf: 'end',
                    textAlign: 'right',
                    fontSize: 10,
                    color: 'var(--ink-4)',
                    letterSpacing: '0.14em',
                    textTransform: 'uppercase',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {quoteLabel}
                </span>
              </>
            )}
          </div>

          <div style={{ minHeight: 0 }}>
            {/* Real 1D intraday sparkline (Alpaca IEX 5-min bars). Sits
                above the small caption row so the chart reads as a
                visual answer to the session % printed beneath it. */}
            <div style={{ height: 42 }}>
              <HeroSpark ticker={symbol} up={sparkUp} bars={intradayBars} loading={intradayLoading} />
            </div>
            <div
              style={{
                width: '100%',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 16,
                marginTop: 12,
                paddingTop: 8,
                minHeight: 29,
                borderTop: '1px solid color-mix(in oklab, var(--line) 70%, transparent)',
                fontSize: 10,
                color: 'var(--ink-4)',
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
              }}
            >
              {intradayLoading ? (
                <>
                  <SkeletonBlock width={214} height={12} radius={5} />
                  <SkeletonBlock width={60} height={12} radius={5} />
                </>
              ) : (
                <>
                  <span
                    style={{
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      minWidth: 0,
                    }}
                  >
                    {sparkCaption}
                  </span>
                  <span
                    className="mono tnum"
                    style={{
                      flexShrink: 0,
                      color:
                        sessionPct == null || sessionPct === 0
                          ? 'var(--ink-4)'
                          : sessionPct > 0
                            ? 'var(--up)'
                            : 'var(--down)',
                    }}
                  >
                    {sessionPct == null
                      ? '--'
                      : `${sessionPct >= 0 ? '+' : ''}${(sessionPct * 100).toFixed(2)}%`}
                  </span>
                </>
              )}
            </div>
          </div>

          <BackToCalendarButton onClick={onBack} label={backLabel} />
        </div>

        <div
          className="qv-detail-hero-right"
          style={{
            borderLeft: '1px solid color-mix(in oklab, var(--line) 60%, transparent)',
            paddingLeft: 28,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
            gap: 14,
            minWidth: 0,
          }}
        >
          <div>
            <span className="qv-pill">
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: 999,
                  background: 'var(--brand-blue-1)',
                  boxShadow:
                    '0 0 0 3px color-mix(in oklab, var(--brand-blue-1) 25%, transparent)',
                }}
              />
              {eventLabel}
            </span>
            {daysLeft != null && (
              <>
                <div
                  style={{
                    marginTop: 14,
                    fontSize: 11,
                    color: 'var(--ink-3)',
                    letterSpacing: '0.14em',
                    textTransform: 'uppercase',
                  }}
                >
                  Reports in
                </div>
                <div
                  className="serif tnum qv-detail-countdown"
                  style={{
                    display: 'flex',
                    alignItems: 'flex-end',
                    gap: 8,
                    fontSize: 56,
                    fontWeight: 800,
                    lineHeight: 1,
                    letterSpacing: 0,
                    marginTop: 4,
                    background:
                      'linear-gradient(180deg, var(--ink) 0%, color-mix(in oklab, var(--ink) 60%, var(--brand-blue-1)) 100%)',
                    WebkitBackgroundClip: 'text',
                    WebkitTextFillColor: 'transparent',
                    backgroundClip: 'text',
                  }}
                >
                  {daysLeft}
                  <span
                    style={{
                      fontSize: 18,
                      color: 'var(--ink-3)',
                      fontWeight: 500,
                      lineHeight: 1.1,
                      marginBottom: 6,
                      WebkitTextFillColor: 'var(--ink-3)',
                    }}
                  >
                    {daysLeft === 1 ? 'day' : 'days'}
                  </span>
                </div>
              </>
            )}
            {earningsLine && (
              <div
                className="mono tnum"
                style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 8 }}
              >
                {earningsLine}
              </div>
            )}
          </div>

          <div
            style={{
              borderTop: '1px solid color-mix(in oklab, var(--line) 60%, transparent)',
              paddingTop: 18,
            }}
          >
            <div
              style={{
                fontSize: 11,
                color: 'var(--ink-3)',
                letterSpacing: '0.14em',
                textTransform: 'uppercase',
              }}
            >
              Options-implied move
            </div>
            <div
              className="serif tnum qv-detail-em"
              style={{
                fontSize: 64,
                // Bumped from 0.9 to 1.0 so the headline carries its own
                // proper line-box. Combined with the marginTop on the
                // range line below, this gives the gradient number room
                // to breathe instead of being clipped at the bottom.
                fontWeight: 800,
                lineHeight: 1.06,
                letterSpacing: '-0.04em',
                marginTop: 12,
                background: 'linear-gradient(135deg, var(--brand-blue-1), var(--accent-hi))',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
                backgroundClip: 'text',
              }}
            >
              ±{(emPct * 100).toFixed(1)}
              <span
                style={{
                  fontSize: 24,
                  fontWeight: 600,
                  color: 'var(--ink-3)',
                  WebkitTextFillColor: 'var(--ink-3)',
                  marginLeft: 4,
                }}
              >
                %
              </span>
            </div>
            {/* Generous gap so the price-range line is clearly its own
                element, not a sub-line glued to the gradient headline. */}
            <div
              className="mono tnum"
              style={{ fontSize: 11.5, color: 'var(--ink-3)', marginTop: 30 }}
            >
              <span style={{ color: 'var(--down)' }}>${lower.toFixed(2)}</span>
              {' · '}
              <span style={{ color: 'var(--up)' }}>${upper.toFixed(2)}</span>
              {' · via ATM straddle'}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------- Interactive Bar (probability density) ----------
function InteractiveBar({
  spot,
  em,
  emIV,
  atmIV,
  dte,
}: {
  spot: number;
  em: number;
  emIV: number;
  atmIV: number;
  dte: number;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<
    { x: number; pct: number; price: number; prob: number } | null
  >(null);
  const [pinned, setPinned] = useState<
    { x: number; pct: number; price: number; prob: number } | null
  >(null);

  const widest = Math.max(em, emIV, 0.01) * 1.5;
  const minPct = -widest;
  const maxPct = widest;
  const toX = (pct: number) => ((pct - minPct) / (maxPct - minPct)) * 100;

  const sigma = (atmIV || 0.3) * Math.sqrt((dte || 28) / 365);

  // Two-sided log-normal: P(|S/S0 − 1| ≥ |x|)
  //   = P(S ≥ S0(1 + |x|)) + P(S ≤ S0(1 − |x|))
  //   = [1 − Φ(log(1+|x|)/σ)] + Φ(log(1−|x|)/σ)
  // The previous form (`2 (1 − Φ(z_up))`) treated the two tails as
  // symmetric in `pct`, which slightly overstates the probability for
  // any non-trivial move because the downside tail in `pct` is fatter
  // in log-space.
  const probBeyond = useCallback(
    (pct: number) => {
      const ax = Math.abs(pct);
      if (ax === 0) return 1;
      if (ax >= 1) return 0; // can't physically lose 100%+
      const zUp = Math.log(1 + ax) / sigma;
      const zDn = Math.log(1 - ax) / sigma; // negative
      return (1 - normCDF(zUp)) + normCDF(zDn);
    },
    [sigma],
  );

  const onMove = (e: React.MouseEvent) => {
    if (!wrapRef.current) return;
    const rect = wrapRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const pct = minPct + x * (maxPct - minPct);
    const price = spot * (1 + pct);
    const prob = probBeyond(pct);
    setHover({ x: x * 100, pct, price, prob });
  };
  const onLeave = () => setHover(null);
  const onClick = () => {
    if (hover) setPinned(hover);
  };

  // PDF of the *return* `pct = S/S0 − 1` under log-normal. By change of
  // variable from log-return to simple-return we pick up the Jacobian
  // `1 / (1 + pct)`, which makes the curve correctly skewed (the
  // downside tail is fatter in pct-space than the upside). The curve
  // is normalized to peak at 1.0 just for display.
  const density = useMemo(() => {
    const n = 80;
    const arr: { x: number; y: number }[] = [];
    for (let i = 0; i <= n; i++) {
      const x = i / n;
      const pct = minPct + x * (maxPct - minPct);
      // Guard against pct ≤ −1 (would blow up log); our visible
      // window always sits well above −1, so this is just defensive.
      if (1 + pct <= 0) {
        arr.push({ x: x * 100, y: 0 });
        continue;
      }
      const z = Math.log(1 + pct) / sigma;
      const pdf =
        Math.exp(-0.5 * z * z) / Math.sqrt(2 * Math.PI) / sigma / (1 + pct);
      arr.push({ x: x * 100, y: pdf });
    }
    const maxP = Math.max(...arr.map((p) => p.y));
    return arr.map((p) => ({ x: p.x, y: maxP > 0 ? p.y / maxP : 0 }));
  }, [sigma, minPct, maxPct]);
  const densityAreaPath = useMemo(
    () =>
      `M 0,90 ${density
        .map((p) => `L ${formatSvgNumber(p.x)},${formatSvgNumber(90 - p.y * 76)}`)
        .join(' ')} L 100,90 Z`,
    [density],
  );
  const densityStrokePath = useMemo(
    () =>
      density
        .map(
          (p, i) =>
            `${i ? 'L' : 'M'}${formatSvgNumber(p.x)},${formatSvgNumber(90 - p.y * 76)}`,
        )
        .join(' '),
    [density],
  );

  const emLow = toX(-em);
  const emHigh = toX(em);
  const ivLow = toX(-emIV);
  const ivHigh = toX(emIV);
  const spotX = toX(0);

  const current = hover || pinned;

  return (
    <div style={{ marginTop: 18 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 12,
          color: 'var(--ink-4)',
          letterSpacing: '0.1em',
          textTransform: 'uppercase',
          marginBottom: 10,
        }}
      >
        <span className="mono tnum">
          ${(spot * (1 + minPct)).toFixed(2)} · {(minPct * 100).toFixed(1)}%
        </span>
        <span>Spot ${spot.toFixed(2)}</span>
        <span className="mono tnum">
          ${(spot * (1 + maxPct)).toFixed(2)} · +{(maxPct * 100).toFixed(1)}%
        </span>
      </div>

      <div
        ref={wrapRef}
        onMouseMove={onMove}
        onMouseLeave={onLeave}
        onClick={onClick}
        style={{
          position: 'relative',
          height: 110,
          cursor: 'crosshair',
          userSelect: 'none',
        }}
      >
        <svg
          viewBox="0 0 100 110"
          preserveAspectRatio="none"
          width="100%"
          height="110"
          style={{ position: 'absolute', inset: 0 }}
        >
          <defs>
            <linearGradient id="den-grad" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--brand-blue-1)" stopOpacity="0.5" />
              <stop offset="100%" stopColor="var(--brand-blue-1)" stopOpacity="0" />
            </linearGradient>
            <linearGradient id="den-stroke" x1="0" x2="1" y1="0" y2="0">
              <stop offset="0%" stopColor="var(--down)" stopOpacity="0.9" />
              <stop offset="50%" stopColor="var(--brand-blue-1)" />
              <stop offset="100%" stopColor="var(--up)" stopOpacity="0.9" />
            </linearGradient>
          </defs>
          <path
            d={densityAreaPath}
            fill="url(#den-grad)"
          />
          <path
            d={densityStrokePath}
            stroke="url(#den-stroke)"
            strokeWidth="0.8"
            fill="none"
            vectorEffect="non-scaling-stroke"
          />
        </svg>

        {/* Straddle band */}
        <div
          style={{
            position: 'absolute',
            top: 68,
            height: 14,
            left: `${emLow}%`,
            width: `${emHigh - emLow}%`,
            background:
              'linear-gradient(90deg, var(--brand-blue-2), var(--brand-blue-1))',
            borderRadius: 999,
            boxShadow:
              '0 4px 14px color-mix(in oklab, var(--brand-blue-1) 35%, transparent)',
          }}
        />
        {/* IV band ticks */}
        <div
          style={{
            position: 'absolute',
            top: 64,
            height: 22,
            left: `${ivLow}%`,
            width: `${ivHigh - ivLow}%`,
            borderLeft: '1px dashed var(--ink-3)',
            borderRight: '1px dashed var(--ink-3)',
            pointerEvents: 'none',
          }}
        />

        {/* Baseline */}
        <div
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            top: 75,
            height: 1,
            background: 'var(--line)',
          }}
        />

        {/* Spot marker */}
        <div
          style={{
            position: 'absolute',
            left: `${spotX}%`,
            top: 56,
            bottom: 18,
            width: 1,
            background: 'var(--ink-2)',
            transform: 'translateX(-0.5px)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: `${spotX}%`,
            bottom: 0,
            transform: 'translateX(-50%)',
            fontSize: 12,
            color: 'var(--ink-2)',
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            fontWeight: 600,
          }}
        >
          Spot
        </div>

        <div
          className="mono tnum"
          style={{
            position: 'absolute',
            top: 52,
            left: `${emLow}%`,
            transform: 'translateX(-50%)',
            fontSize: 13,
            color: 'var(--down)',
            fontWeight: 600,
          }}
        >
          −{(em * 100).toFixed(1)}%
        </div>
        <div
          className="mono tnum"
          style={{
            position: 'absolute',
            top: 52,
            left: `${emHigh}%`,
            transform: 'translateX(-50%)',
            fontSize: 13,
            color: 'var(--up)',
            fontWeight: 600,
          }}
        >
          +{(em * 100).toFixed(1)}%
        </div>

        {hover && (
          <div
            style={{
              position: 'absolute',
              left: `${hover.x}%`,
              top: 0,
              bottom: 0,
              width: 1,
              background: 'var(--ink)',
              transform: 'translateX(-0.5px)',
              pointerEvents: 'none',
            }}
          />
        )}
        {pinned && !hover && (
          <div
            style={{
              position: 'absolute',
              left: `${pinned.x}%`,
              top: 0,
              bottom: 0,
              width: 1,
              background: 'var(--flag)',
              transform: 'translateX(-0.5px)',
              pointerEvents: 'none',
            }}
          />
        )}

        {current && (
          <div
            style={{
              position: 'absolute',
              left: `${Math.min(82, Math.max(10, current.x))}%`,
              top: -10,
              transform: 'translate(-50%, -100%)',
              background: 'var(--bg-3)',
              border: '1px solid var(--line-2)',
              padding: '8px 12px',
              borderRadius: 8,
              fontSize: 11,
              whiteSpace: 'nowrap',
              boxShadow: '0 12px 36px rgba(0,0,0,.5)',
              pointerEvents: 'none',
            }}
          >
            <div
              className="serif tnum"
              style={{ fontSize: 18, color: 'var(--ink)', lineHeight: 1, fontWeight: 700 }}
            >
              ${current.price.toFixed(2)}
            </div>
            <div
              className="mono tnum"
              style={{
                fontSize: 10.5,
                marginTop: 4,
                color: current.pct >= 0 ? 'var(--up)' : 'var(--down)',
              }}
            >
              {current.pct >= 0 ? '+' : ''}
              {(current.pct * 100).toFixed(2)}% from spot
            </div>
            <div
              className="mono tnum"
              style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 2 }}
            >
              {(current.prob * 100).toFixed(1)}% chance to move this far
            </div>
          </div>
        )}
      </div>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginTop: 16,
          fontSize: 13,
          color: 'var(--ink-3)',
        }}
      >
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 18,
                height: 4,
                borderRadius: 2,
                background:
                  'linear-gradient(90deg, var(--brand-blue-2), var(--brand-blue-1))',
              }}
            />
            Straddle band
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 18,
                height: 10,
                borderLeft: '1px dashed var(--ink-3)',
                borderRight: '1px dashed var(--ink-3)',
              }}
            />
            IV band
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 18,
                height: 4,
                borderRadius: 2,
                background: 'linear-gradient(90deg, var(--down), var(--brand-blue-1), var(--up))',
              }}
            />
            Log-normal density
          </span>
        </div>
        <span style={{ fontStyle: 'italic' }}>Hover for probability · click to pin</span>
      </div>
    </div>
  );
}

// Forecast distribution — quantile band card. Plots P10/P25/P50/P75/P90 of
// the model's |move| distribution against a 0..max axis, with a straddle
// reference tick. A 5-cell grid beneath shows each quintile's value with
// the corresponding price range.
function QuantileBand({
  q,
  straddleAbs,
  spot,
  mode,
  onModeChange,
  liveDisabled,
  liveStatus,
  pointPct,
  modelMeta,
  unavailableReason,
}: {
  q: { p10: number; p25: number; p50: number; p75: number; p90: number };
  straddleAbs: number;
  spot: number;
  mode: PredictionMode;
  onModeChange: (mode: PredictionMode) => void;
  liveDisabled: boolean;
  liveStatus: LivePredictionStatus;
  pointPct: number | null;
  modelMeta: string;
  unavailableReason: string | null;
}) {
  const max = Math.max(q.p90, straddleAbs) * 1.08;
  const pct = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  const cells: Array<[string, number, string]> = [
    ['P10', q.p10, 'var(--ink-3)'],
    ['P25', q.p25, 'var(--ink-2)'],
    ['P50', q.p50, 'var(--brand-blue-1)'],
    ['P75', q.p75, 'var(--ink-2)'],
    ['P90', q.p90, 'var(--ink-3)'],
  ];
  return (
    <div className="qv-card">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: 16,
          // Was 14, which left no room for the STRADDLE callout that sits
          // at `top: -22` above the bar. The callout was punching up into
          // the "LightGBM ensemble · range of plausible absolute moves on
          // print day" line. 32 leaves a clear ~10px gap below the
          // callout's top edge.
          marginBottom: 32,
        }}
      >
        <div>
          <span className="qv-pill warm">
            {mode === 'live' && liveStatus === 'ready' ? 'Live re-score' : 'ML model'}
          </span>
          <h3
            className="serif"
            style={{
              margin: '10px 0 0',
              fontSize: 20,
              fontWeight: 700,
              letterSpacing: '-0.01em',
            }}
          >
            Forecast distribution
          </h3>
          <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 4 }}>
            {modelMeta}
          </div>
          {mode === 'live' && liveStatus === 'unavailable' && unavailableReason && (
            <div style={{ fontSize: 12, color: 'var(--ink-4)', marginTop: 6 }}>
              Live unavailable · showing snapshot. {unavailableReason}
            </div>
          )}
        </div>
        <div style={{ display: 'grid', justifyItems: 'end', gap: 10 }}>
          <div
            role="tablist"
            aria-label="Forecast source"
            style={{
              display: 'inline-grid',
              gridTemplateColumns: '1fr 1fr',
              minWidth: 164,
              padding: 3,
              borderRadius: 8,
              border: '1px solid var(--line)',
              background: 'var(--bg-2)',
            }}
          >
            {(['snapshot', 'live'] as PredictionMode[]).map((item) => {
              const active = mode === item;
              const disabled = item === 'live' && liveDisabled;
              return (
                <button
                  key={item}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  disabled={disabled}
                  onClick={() => onModeChange(item)}
                  title={disabled ? 'No model horizon is available for this snapshot' : undefined}
                  style={{
                    minHeight: 28,
                    border: 'none',
                    borderRadius: 6,
                    background: active ? 'var(--bg-3)' : 'transparent',
                    color: disabled
                      ? 'var(--ink-4)'
                      : active
                        ? 'var(--ink)'
                        : 'var(--ink-3)',
                    fontSize: 12,
                    fontWeight: active ? 700 : 500,
                    cursor: disabled ? 'not-allowed' : 'pointer',
                    fontFamily: 'inherit',
                  }}
                >
                  {item === 'snapshot' ? 'Snapshot' : liveStatus === 'loading' ? 'Live…' : 'Live'}
                </button>
              );
            })}
          </div>
          <div
            className="mono tnum"
            style={{ textAlign: 'right', fontSize: 13, color: 'var(--ink-3)' }}
          >
            {pointPct != null && (
              <div>
                Point <span style={{ color: 'var(--ink-2)' }}>±{(pointPct * 100).toFixed(1)}%</span>
              </div>
            )}
            <div style={{ marginTop: pointPct != null ? 2 : 0 }}>
              Median <span style={{ color: 'var(--ink-2)' }}>±{(q.p50 * 100).toFixed(1)}%</span>
            </div>
            <div style={{ marginTop: 2 }}>
              80% band{' '}
              <span style={{ color: 'var(--ink-2)' }}>
                {(q.p10 * 100).toFixed(1)}–{(q.p90 * 100).toFixed(1)}%
              </span>
            </div>
          </div>
        </div>
      </div>

      <div
        style={{
          position: 'relative',
          height: 44,
          background: 'var(--bg-3)',
          borderRadius: 8,
          overflow: 'visible',
        }}
      >
        <div
          style={{
            position: 'absolute',
            left: pct(q.p10),
            width: `calc(${pct(q.p90)} - ${pct(q.p10)})`,
            top: 0,
            bottom: 0,
            background: 'color-mix(in oklab, var(--brand-blue-1) 18%, transparent)',
            borderRadius: 8,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: pct(q.p25),
            width: `calc(${pct(q.p75)} - ${pct(q.p25)})`,
            top: 0,
            bottom: 0,
            background: 'color-mix(in oklab, var(--brand-blue-1) 38%, transparent)',
            borderRadius: 8,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: pct(q.p50),
            top: -4,
            bottom: -4,
            width: 2,
            background: 'var(--brand-blue-1)',
            boxShadow:
              '0 0 12px color-mix(in oklab, var(--brand-blue-1) 60%, transparent)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: pct(straddleAbs),
            top: -10,
            bottom: -10,
            width: 1,
            background: 'var(--flag)',
          }}
        />
        <div
          className="mono tnum"
          style={{
            position: 'absolute',
            left: pct(straddleAbs),
            top: -22,
            transform: 'translateX(-50%)',
            fontSize: 9.5,
            color: 'var(--flag)',
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
          }}
        >
          Straddle
        </div>
        <div
          className="mono tnum"
          style={{
            position: 'absolute',
            left: pct(q.p50),
            bottom: -22,
            transform: 'translateX(-50%)',
            fontSize: 9.5,
            color: 'var(--brand-blue-1)',
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            fontWeight: 600,
          }}
        >
          P50
        </div>
      </div>

      <div
        style={{
          marginTop: 38,
          display: 'grid',
          gridTemplateColumns: 'repeat(5, 1fr)',
          gap: 12,
          paddingTop: 12,
          borderTop: '1px solid var(--line)',
        }}
      >
        {cells.map(([label, val, tone]) => (
          <div key={label}>
            <div
              style={{
                fontSize: 11.5,
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                color: 'var(--ink-4)',
                fontWeight: 600,
              }}
            >
              {label}
            </div>
            <div
              className="serif tnum"
              style={{
                fontSize: 18,
                color: tone,
                fontWeight: 700,
                marginTop: 2,
              }}
            >
              ±{(val * 100).toFixed(1)}%
            </div>
            <div
              className="mono tnum"
              style={{ fontSize: 12, color: 'var(--ink-4)', marginTop: 2 }}
            >
              ${(spot * (1 - val)).toFixed(0)}–${(spot * (1 + val)).toFixed(0)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Adapter row consumed by TermFan + GreeksPanel. Built once from
 *  `data.straddle_features` and tagged with `isEarnings` so the
 *  earnings expiry can be highlighted. */
type TermRow = {
  expiration: string;
  dte: number;
  iv: number;        // ATM IV (decimal)
  emPct: number;     // straddle-implied move (decimal fraction)
  straddle: number;  // $
  strike: number;    // $
  delta: number | null;
  gamma: number | null;
  vega: number | null;
  theta: number | null;
  isEarnings: boolean;
};

function buildTermRows(
  expiries: Straddle[],
  earningsExpiration: string | null,
): TermRow[] {
  return expiries
    .filter(
      (e) =>
        e.em_straddle_pct != null &&
        Number.isFinite(e.em_straddle_pct) &&
        e.atm_iv != null &&
        Number.isFinite(e.atm_iv),
    )
    .sort(
      (a, b) =>
        parseLocalDate(a.expiration).getTime() -
        parseLocalDate(b.expiration).getTime(),
    )
    .map((e) => ({
      expiration: e.expiration,
      dte: e.dte,
      iv: e.atm_iv as number,
      emPct: e.em_straddle_pct as number,
      straddle: e.straddle_mid ?? 0,
      strike: e.atm_strike,
      delta: e.call_delta,
      gamma: e.call_gamma,
      vega: e.call_vega,
      theta: e.call_theta,
      isEarnings: earningsExpiration != null && e.expiration === earningsExpiration,
    }));
}

// Term-structure fan — two lines from today's spot: spot×(1±EM) at each
// expiry. X axis is days-to-expiry. Hover dots show DTE, IV, EM, straddle.
function TermFan({ rows, spot }: { rows: TermRow[]; spot: number }) {
  const [hovered, setHovered] = useState<number | null>(null);

  // Measure the SVG's rendered width so axis/label/tooltip sizes can be
  // expressed in target screen pixels instead of viewBox units. Without
  // this, the chart's text shrinks dramatically in the 2-col layout (when
  // QuantileBand is rendered alongside, the container drops from ~1000px
  // to ~498px wide, scaling 720-unit text by 0.69×) and explodes in the
  // 1-col layout. useLayoutEffect runs before the browser paints, so the
  // corrected chartScale is in place for the first user-visible frame.
  // A ResizeObserver keeps it in sync on window resize / layout-column
  // flips (e.g. when QuantileBand mounts/unmounts). The W = 720 viewBox
  // width is constant for the chart's geometry; it has to be re-declared
  // here because the const below sits after the early-return.
  const svgRef = useRef<SVGSVGElement>(null);
  const [chartScale, setChartScale] = useState(1);
  useLayoutEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const update = () => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0) setChartScale(rect.width / 720);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  if (rows.length === 0) return null;
  const maxDte = Math.max(...rows.map((r) => r.dte), 1);
  const maxPrice = Math.max(...rows.map((r) => spot * (1 + r.emPct)));
  const minPrice = Math.min(...rows.map((r) => spot * (1 - r.emPct)));
  const pad = (maxPrice - minPrice) * 0.08;
  const yMax = maxPrice + pad;
  const yMin = minPrice - pad;

  const W = 720;
  const H = 320;
  const M = { top: 16, right: 96, bottom: 48, left: 72 };
  const innerW = W - M.left - M.right;
  const innerH = H - M.top - M.bottom;
  // Screen-px → viewBox units. Always returns a finite number so a 0
  // initial scale (before measurement) doesn't propagate NaN into the DOM.
  const px = (screenPx: number) => screenPx / (chartScale || 1);
  const x = (dte: number) => M.left + (dte / maxDte) * innerW;
  const y = (p: number) =>
    M.top + innerH - ((p - yMin) / (yMax - yMin)) * innerH;

  const upPath =
    `M ${x(0)} ${y(spot)} ` +
    rows.map((r) => `L ${x(r.dte)} ${y(spot * (1 + r.emPct))}`).join(' ');
  const dnPath =
    `M ${x(0)} ${y(spot)} ` +
    rows.map((r) => `L ${x(r.dte)} ${y(spot * (1 - r.emPct))}`).join(' ');
  const areaPath =
    upPath +
    ' ' +
    rows
      .slice()
      .reverse()
      .map((r) => `L ${x(r.dte)} ${y(spot * (1 - r.emPct))}`)
      .join(' ') +
    ' Z';

  const yTicks = Array.from(
    { length: 5 },
    (_, i) => yMin + ((yMax - yMin) * i) / 4,
  );

  return (
    <div className="qv-card">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: 16,
          marginBottom: 8,
        }}
      >
        <div>
          <span className="qv-pill">Term structure</span>
          <h3
            className="serif"
            style={{
              margin: '10px 0 0',
              fontSize: 20,
              fontWeight: 700,
              letterSpacing: '-0.01em',
            }}
          >
            Implied range across expiries
          </h3>
          <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 4 }}>
            Spot × (1 ± EM) at each expiry. Hover an expiry for details.
          </div>
        </div>
        <div className="mono tnum" style={{ fontSize: 12.5, color: 'var(--ink-4)' }}>
          {rows.length} {rows.length === 1 ? 'expiry' : 'expiries'}
        </div>
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', height: 'auto', display: 'block', marginTop: 10 }}
      >
        <defs>
          <linearGradient id="fan-up" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="var(--up)" stopOpacity="1" />
            <stop offset="100%" stopColor="var(--up)" stopOpacity="0.6" />
          </linearGradient>
          <linearGradient id="fan-dn" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="var(--down)" stopOpacity="1" />
            <stop offset="100%" stopColor="var(--down)" stopOpacity="0.6" />
          </linearGradient>
          <linearGradient id="fan-area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--brand-blue-1)" stopOpacity="0.16" />
            <stop offset="100%" stopColor="var(--brand-blue-1)" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {yTicks.map((v, i) => (
          <g key={i}>
            <line
              x1={M.left}
              x2={W - M.right}
              y1={y(v)}
              y2={y(v)}
              stroke="var(--line)"
              strokeDasharray={i === 0 || i === yTicks.length - 1 ? 'none' : '2 4'}
              strokeWidth={i === 0 || i === yTicks.length - 1 ? 1 : 0.5}
            />
            <text
              x={M.left - 12}
              y={y(v)}
              textAnchor="end"
              dominantBaseline="central"
              fontSize={px(12.5)}
              fontFamily="ui-monospace, monospace"
              fill="var(--ink-3)"
            >
              ${v.toFixed(0)}
            </text>
          </g>
        ))}

        <path d={areaPath} fill="url(#fan-area)" />

        <line
          x1={M.left}
          x2={W - M.right}
          y1={y(spot)}
          y2={y(spot)}
          stroke="var(--ink-3)"
          strokeDasharray="3 4"
          strokeWidth={1}
        />

        <path d={upPath} fill="none" stroke="url(#fan-up)" strokeWidth={2.2} strokeLinejoin="round" />
        <path d={dnPath} fill="none" stroke="url(#fan-dn)" strokeWidth={2.2} strokeLinejoin="round" />

        <circle cx={x(0)} cy={y(spot)} r={5} fill="var(--ink)" stroke="var(--bg)" strokeWidth={2} />

        {rows.map((r, i) => {
          const cx = x(r.dte);
          const uy = y(spot * (1 + r.emPct));
          const dy = y(spot * (1 - r.emPct));
          const active = hovered === i;
          return (
            <g
              key={r.expiration}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
            >
              <rect
                x={cx - 18}
                y={M.top}
                width={36}
                height={innerH}
                fill="transparent"
                style={{ cursor: 'pointer' }}
              />
              {active && (
                <line
                  x1={cx}
                  x2={cx}
                  y1={M.top}
                  y2={M.top + innerH}
                  stroke="var(--ink-2)"
                  strokeWidth={1}
                  strokeDasharray="3 3"
                  pointerEvents="none"
                />
              )}
              <circle
                cx={cx}
                cy={uy}
                r={active ? 5 : 3.2}
                fill="var(--up)"
                stroke={active ? 'var(--bg)' : 'none'}
                strokeWidth={1.5}
                pointerEvents="none"
              />
              <circle
                cx={cx}
                cy={dy}
                r={active ? 5 : 3.2}
                fill="var(--down)"
                stroke={active ? 'var(--bg)' : 'none'}
                strokeWidth={1.5}
                pointerEvents="none"
              />
              <text
                x={cx}
                y={H - M.bottom + px(18)}
                textAnchor="middle"
                fontSize={px(12.5)}
                fontFamily="ui-monospace, monospace"
                fill={active ? 'var(--ink)' : 'var(--ink-2)'}
                fontWeight={active ? 600 : 500}
                pointerEvents="none"
              >
                {axisDate(r.expiration)}
              </text>
              <text
                x={cx}
                y={H - M.bottom + px(32)}
                textAnchor="middle"
                fontSize={px(11)}
                fontFamily="ui-monospace, monospace"
                fill="var(--ink-4)"
                pointerEvents="none"
              >
                {r.dte}d
              </text>
            </g>
          );
        })}

        {(() => {
          const last = rows[rows.length - 1];
          return (
            <g>
              <text
                x={x(last.dte) + 10}
                y={y(spot * (1 + last.emPct))}
                dominantBaseline="central"
                fontSize={px(12)}
                fontFamily="ui-monospace, monospace"
                fontWeight="700"
                fill="var(--up)"
              >
                ${(spot * (1 + last.emPct)).toFixed(0)}
              </text>
              <text
                x={x(last.dte) + 10}
                y={y(spot * (1 - last.emPct))}
                dominantBaseline="central"
                fontSize={px(12)}
                fontFamily="ui-monospace, monospace"
                fontWeight="700"
                fill="var(--down)"
              >
                ${(spot * (1 - last.emPct)).toFixed(0)}
              </text>
            </g>
          );
        })()}

        {hovered != null &&
          (() => {
            const r = rows[hovered];
            // Tooltip box dimensions in target screen pixels, converted to
            // viewBox units via px() so the box reads the same size whether
            // the chart is at 498px (2-col) or 1004px (1-col) wide.
            const tooltipW = px(200);
            const padX = px(12);
            const padTop = px(18);
            const lineH = px(15);
            const lines: Array<[string, string]> = [
              ['DTE', `${r.dte}d`],
              ['ATM IV', `${(r.iv * 100).toFixed(2)}%`],
              ['EM', `±${(r.emPct * 100).toFixed(2)}%`],
              ['Straddle', `$${r.straddle.toFixed(2)}`],
            ];
            const tooltipH = padTop + px(8) + lines.length * lineH;
            const anchorX = x(r.dte);
            const midY = (y(spot * (1 + r.emPct)) + y(spot * (1 - r.emPct))) / 2;
            const tx = Math.max(
              M.left + 4,
              Math.min(
                anchorX > W - M.right - tooltipW - 24
                  ? anchorX - tooltipW - 14
                  : anchorX + 14,
                W - M.right - tooltipW,
              ),
            );
            const ty = Math.max(
              M.top + 8,
              Math.min(midY - tooltipH / 2, H - M.bottom - tooltipH - 4),
            );
            return (
              <g pointerEvents="none">
                <rect
                  x={tx}
                  y={ty}
                  width={tooltipW}
                  height={tooltipH}
                  rx={px(8)}
                  fill="var(--bg-3)"
                  stroke="var(--line-2)"
                  strokeWidth={1}
                />
                <text
                  x={tx + padX}
                  y={ty + padTop}
                  fontSize={px(12)}
                  fontFamily="Mulish, sans-serif"
                  fontWeight="700"
                  fill="var(--ink)"
                >
                  {r.isEarnings ? 'Earnings · ' : ''}
                  {shortDate(r.expiration)}
                </text>
                {lines.map(([k, v], i) => (
                  <g key={k}>
                    <text
                      x={tx + padX}
                      y={ty + padTop + px(18) + i * lineH}
                      fontSize={px(10.5)}
                      fill="var(--ink-3)"
                      fontFamily="sans-serif"
                    >
                      {k}
                    </text>
                    <text
                      x={tx + tooltipW - padX}
                      y={ty + padTop + px(18) + i * lineH}
                      textAnchor="end"
                      fontSize={px(11)}
                      fill="var(--ink)"
                      fontFamily="ui-monospace, monospace"
                      fontWeight="600"
                    >
                      {v}
                    </text>
                  </g>
                ))}
              </g>
            );
          })()}
      </svg>
    </div>
  );
}

// Placeholder kept until the page body's old TermStructureFan callsite
// is removed. Returns null so existing markup quietly drops out.

// ---------- Historical implied vs actual ----------
type HistoryPoint = {
  q: string;
  date: string;
  /** Implied move (always ≥ 0). Null until at-the-time chain data is wired. */
  implied: number | null;
  /** Signed realized move (e.g. -0.034 = -3.4%). */
  actual: number;
  /** Non-GAAP EPS fundamentals from Finnhub. */
  epsActual: number | null;
  epsEstimate: number | null;
  /** Signed EPS surprise (e.g. 0.061 = beat by 6.1%). */
  epsSurprise: number | null;
  revActual: number | null;
  revEstimate: number | null;
  revSurprise: number | null;
};


function pickNum(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}



/** Build the chart series from earnings_history rows that carry an
 *  `actual` close-to-close move. `implied` is optional — when present
 *  the chart draws an implied band around zero; when absent the chart
 *  shows just the realized dot. EPS / revenue surprise come from the
 *  Finnhub overlay and may be null for older rows that predate the
 *  overlay's coverage window. Returns rows in chronological order
 *  (oldest → newest) and clips to the last 8 quarters. */
function buildHistorySeries(
  raw: SymbolDetail['earnings_history'] | undefined,
): HistoryPoint[] {
  if (!raw || raw.length === 0) return [];
  const usable = raw
    .filter(
      (h): h is typeof h & { actual: number } =>
        h.actual != null && Number.isFinite(h.actual),
    )
    .map((h) => {
      const d = new Date(h.date);
      const yy = String(d.getFullYear() % 100).padStart(2, '0');
      const q = h.q ?? `Q${Math.floor(d.getMonth() / 3) + 1} ${yy}`;
      const implied =
        h.implied != null && Number.isFinite(h.implied)
          ? Math.abs(h.implied)
          : null;
      return {
        q,
        date: h.date,
        implied,
        actual: h.actual,
        epsActual: pickNum(h.eps_actual),
        epsEstimate: pickNum(h.eps_estimate),
        epsSurprise: pickNum(h.eps_surprise_pct),
        revActual: pickNum(h.revenue_actual),
        revEstimate: pickNum(h.revenue_estimate),
        revSurprise: pickNum(h.rev_surprise_pct),
      };
    })
    .sort((a, b) => a.date.localeCompare(b.date));
  return usable.slice(-8);
}

// EPS surprise strip — one bar per quarter, sharing column positions with
// HistoryChart so beats / misses line up vertically with their realized
// dots. Bars are green for beat, red for miss, hollow gray for missing.
// Renders nothing when the entire series lacks EPS data, so older AAPL
// payloads (pre-Finnhub overlay) degrade gracefully.
// Compact fundamentals strip showing EPS + revenue surprise as
// colored chips per quarter. Aligned with HistoryChart's columns so
// hover state highlights the same quarter across both. Rendered
// BELOW the realized-move chart (the chart is the hero — surprise
// is supporting context). Hidden entirely when the series has no
// fundamentals data.


// Owns the shared hoveredIndex so HistoryChart + SurpriseStrip light up
// the same quarter together — hovering a column in either highlights
// the matching column in the other. Header summarizes implied-beat and
// EPS-beat rates so the eye can scan the headline before reading the
// chart. Layout: chart (hero, the realized moves are the primary
// signal) → SurpriseStrip (secondary, fundamentals context).
// ---------- History block (chart + EPS strip combined) ----------
function HistoryBlock({ history }: { history: HistoryPoint[] }) {
  const [hovered, setHovered] = useState<number | null>(null);
  if (history.length === 0) return null;

  const hasImplied = history.some((h) => h.implied != null);
  const hasEps = history.some((h) => h.epsSurprise != null);

  const W = 700;
  const H = 240;
  const P = 48;
  const colW = (W - P * 2) / history.length;
  const max =
    Math.max(
      ...history.map((h) =>
        Math.max(h.implied != null ? h.implied : 0, Math.abs(h.actual)),
      ),
    ) * 1.18 || 0.05;
  const y = (v: number) => H / 2 - (v / max) * (H / 2 - 26);

  const epsH = 72;
  const epsCap = 0.20;
  const epsY = (v: number) =>
    epsH / 2 - (Math.max(-epsCap, Math.min(epsCap, v)) / epsCap) * (epsH / 2 - 10);

  const hovered_ = hovered != null ? history[hovered] : null;

  const beatImplied = hasImplied
    ? history.filter(
        (h) => h.implied != null && Math.abs(h.actual) > (h.implied as number),
      ).length
    : 0;
  const epsBeats = hasEps
    ? history.filter((h) => (h.epsSurprise ?? 0) >= 0).length
    : 0;

  return (
    <div className="qv-card">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 8,
          gap: 16,
        }}
      >
        <div>
          <span
            className="qv-pill"
            style={{
              background: 'color-mix(in oklab, var(--up) 14%, transparent)',
              color: 'var(--up)',
              borderColor: 'color-mix(in oklab, var(--up) 30%, transparent)',
            }}
          >
            Historical
          </span>
          <h3
            className="serif"
            style={{
              margin: '10px 0 0',
              fontSize: 20,
              fontWeight: 700,
              letterSpacing: '-0.01em',
            }}
          >
            {hasImplied
              ? 'Implied vs realized · last '
              : 'Realized moves · last '}
            {history.length} {history.length === 1 ? 'quarter' : 'quarters'}
          </h3>
          <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 4 }}>
            {hasImplied
              ? 'Realized moves overlaid on what options priced going in.'
              : 'Close-to-close moves; implied range pending historical option chains.'}
          </div>
        </div>
        <div
          className="mono tnum"
          style={{ fontSize: 13, color: 'var(--ink-3)', textAlign: 'right', lineHeight: 1.6 }}
        >
          {hasImplied && (
            <div>
              Beat implied{' '}
              <span style={{ color: 'var(--ink-2)' }}>
                {beatImplied}/{history.length}
              </span>
            </div>
          )}
          {hasEps && (
            <div>
              EPS beat{' '}
              <span style={{ color: 'var(--up)' }}>
                {epsBeats}/{history.length}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Wrap the chart in a relative container so the hover tooltip can
          anchor to the chart itself, regardless of whether the EPS strip
          below renders. (Earlier, the tooltip lived inside the EPS strip
          and was double-gated on EPS data, so for most tickers hover did
          nothing.) */}
      <div style={{ position: 'relative', marginTop: 10 }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', height: 'auto' }}
      >
        {/* Alternating column bands give the chart visual rhythm,
            especially important when the implied bands are missing and
            the only marks are realized dots floating on the zero line. */}
        {history.map((_, i) =>
          i % 2 === 1 ? (
            <rect
              key={`band-${i}`}
              x={P + colW * i}
              y={0}
              width={colW}
              height={H - 22}
              fill="var(--ink)"
              opacity={0.018}
            />
          ) : null,
        )}

        <line x1={P} x2={W - P} y1={H / 2} y2={H / 2} stroke="var(--line)" />
        <line
          x1={P}
          x2={W - P}
          y1={y(max)}
          y2={y(max)}
          stroke="var(--line)"
          strokeDasharray="2 4"
          opacity={0.5}
        />
        <line
          x1={P}
          x2={W - P}
          y1={y(-max)}
          y2={y(-max)}
          stroke="var(--line)"
          strokeDasharray="2 4"
          opacity={0.5}
        />

        {/* Faint trajectory line connecting realized dots — only when
            implied bands are absent, so the chart still feels composed
            in the no-options-history case. */}
        {!hasImplied && history.length > 1 && (
          <path
            d={history
              .map((h, i) => {
                const cx = P + colW * i + colW / 2;
                const cy = y(h.actual);
                return `${i === 0 ? 'M' : 'L'} ${cx} ${cy}`;
              })
              .join(' ')}
            stroke="var(--ink-4)"
            strokeWidth="1"
            strokeDasharray="3 4"
            fill="none"
            opacity={0.6}
          />
        )}
        <text
          x={P - 8}
          y={y(max) + 3}
          textAnchor="end"
          fill="var(--ink-3)"
          fontSize="9"
          style={{ fontFamily: 'var(--font-jetbrains-mono), ui-monospace, monospace' }}
        >
          +{(max * 100).toFixed(0)}%
        </text>
        <text
          x={P - 8}
          y={y(-max) + 3}
          textAnchor="end"
          fill="var(--ink-3)"
          fontSize="9"
          style={{ fontFamily: 'var(--font-jetbrains-mono), ui-monospace, monospace' }}
        >
          −{(max * 100).toFixed(0)}%
        </text>
        <text
          x={P - 8}
          y={H / 2 + 3}
          textAnchor="end"
          fill="var(--ink-3)"
          fontSize="9"
          style={{ fontFamily: 'var(--font-jetbrains-mono), ui-monospace, monospace' }}
        >
          0%
        </text>

        {history.map((h, i) => {
          const cx = P + colW * i + colW / 2;
          const impliedDecimal = h.implied ?? 0;
          const actualDecimal = h.actual;
          const up = actualDecimal >= 0;
          const moveColor = up ? 'var(--up)' : 'var(--down)';
          const beat = h.implied != null && Math.abs(actualDecimal) > impliedDecimal;
          const active = hovered === i;
          return (
            <g
              key={`${h.q}-${h.date}`}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
              style={{ cursor: 'default' }}
            >
              <rect x={cx - colW / 2} y={0} width={colW} height={H} fill="transparent" />
              {h.implied != null && (
                <>
                  <rect
                    x={cx - 13}
                    y={y(impliedDecimal)}
                    width="26"
                    height={Math.max(1, y(-impliedDecimal) - y(impliedDecimal))}
                    fill={
                      active
                        ? 'color-mix(in oklab, var(--brand-blue-1) 32%, transparent)'
                        : 'color-mix(in oklab, var(--brand-blue-1) 18%, transparent)'
                    }
                    rx="2"
                  />
                  <line
                    x1={cx - 13}
                    x2={cx + 13}
                    y1={y(impliedDecimal)}
                    y2={y(impliedDecimal)}
                    stroke="var(--brand-blue-1)"
                    strokeWidth="1.2"
                  />
                  <line
                    x1={cx - 13}
                    x2={cx + 13}
                    y1={y(-impliedDecimal)}
                    y2={y(-impliedDecimal)}
                    stroke="var(--brand-blue-1)"
                    strokeWidth="1.2"
                  />
                </>
              )}
              {beat && (
                <circle
                  cx={cx}
                  cy={y(actualDecimal)}
                  r="7"
                  fill="none"
                  stroke={moveColor}
                  strokeWidth="1.2"
                  opacity="0.5"
                />
              )}
              <circle
                cx={cx}
                cy={y(actualDecimal)}
                // Larger dots when no implied bands so the realized
                // marker reads as the chart's primary signal.
                r={active ? (hasImplied ? 4.6 : 5.4) : hasImplied ? 3.8 : 4.6}
                fill={moveColor}
                style={{ transition: 'r 160ms ease' }}
              />
              <text
                x={cx}
                y={H - 8}
                textAnchor="middle"
                fill={active ? 'var(--ink)' : 'var(--ink-2)'}
                fontSize="9.5"
                style={{ fontFamily: 'var(--font-jetbrains-mono), ui-monospace, monospace' }}
                fontWeight={active ? 700 : 500}
              >
                {h.q}
              </text>
            </g>
          );
        })}
      </svg>

      {hovered_ && hovered != null && (() => {
        const cx = P + colW * hovered + colW / 2;
        const cy = y(hovered_.actual);
        const placeRight = cx < W * 0.64;
        return (
          <div
            style={{
              position: 'absolute',
              left: `${(cx / W) * 100}%`,
              top: `${Math.max(16, Math.min(82, (cy / H) * 100))}%`,
              transform: placeRight
                ? 'translate(12px, -50%)'
                : 'translate(calc(-100% - 12px), -50%)',
              width: 172,
              background: 'var(--bg-3)',
              border: '1px solid var(--line-2)',
              padding: '7px 9px',
              borderRadius: 8,
              fontSize: 9.5,
              lineHeight: 1.35,
              color: 'var(--ink-2)',
              pointerEvents: 'none',
              boxShadow: '0 12px 36px rgba(0,0,0,.5)',
            }}
          >
            <div
              style={{
                fontSize: 8.5,
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
                color: 'var(--ink-4)',
              }}
            >
              {hovered_.q}
            </div>
            <div
              className="serif tnum"
              style={{
                fontSize: 13,
                color: hovered_.actual >= 0 ? 'var(--up)' : 'var(--down)',
                marginTop: 2,
              }}
            >
              {hovered_.actual >= 0 ? '+' : ''}
              {(hovered_.actual * 100).toFixed(1)}%
            </div>
            <div
              className="mono tnum"
              style={{ fontSize: 8.5, color: 'var(--ink-4)', marginTop: 1 }}
            >
              realized
            </div>
            {hovered_.epsSurprise != null && (
              <div
                className="mono tnum"
                style={{
                  borderTop: '1px solid var(--line)',
                  marginTop: 6,
                  paddingTop: 5,
                  fontSize: 9,
                }}
              >
                <div style={{ color: 'var(--ink-3)' }}>
                  EPS {hovered_.epsActual?.toFixed(2) ?? '–'} vs{' '}
                  {hovered_.epsEstimate?.toFixed(2) ?? '–'}
                </div>
                <div
                  style={{
                    color: hovered_.epsSurprise >= 0 ? 'var(--up)' : 'var(--down)',
                    marginTop: 2,
                  }}
                >
                  {hovered_.epsSurprise >= 0 ? '+' : ''}
                  {(hovered_.epsSurprise * 100).toFixed(1)}% surprise
                </div>
              </div>
            )}
          </div>
        );
      })()}
      </div>

      {/* EPS surprise strip (hidden when no fundamentals data) */}
      {hasEps && (
        <div style={{ marginTop: 14, position: 'relative' }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 12,
              color: 'var(--ink-3)',
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              marginBottom: 6,
            }}
          >
            <span>EPS surprise</span>
            <span style={{ color: 'var(--ink-4)', letterSpacing: '0.06em', textTransform: 'none' }}>
              vs sell-side consensus
            </span>
          </div>
          <svg
            viewBox={`0 0 ${W} ${epsH}`}
            style={{ width: '100%', height: 'auto' }}
          >
            <line x1={P} x2={W - P} y1={epsH / 2} y2={epsH / 2} stroke="var(--line)" />
            {history.map((h, i) => {
              const cx = P + colW * i + colW / 2;
              const s = h.epsSurprise ?? 0;
              const beat = s >= 0;
              const top = beat ? epsY(s) : epsH / 2;
              const bot = beat ? epsH / 2 : epsY(s);
              const height = Math.max(2, bot - top);
              const active = hovered === i;
              return (
                <g
                  key={`eps-${h.q}-${h.date}`}
                  onMouseEnter={() => setHovered(i)}
                  onMouseLeave={() => setHovered(null)}
                >
                  <rect
                    x={cx - 12}
                    y={top}
                    width="24"
                    height={height}
                    fill={beat ? 'var(--up)' : 'var(--down)'}
                    opacity={hovered == null || active ? 0.85 : 0.4}
                    rx="2"
                  />
                </g>
              );
            })}
          </svg>
        </div>
      )}

      <div
        style={{
          marginTop: 14,
          display: 'flex',
          gap: 18,
          fontSize: 13,
          color: 'var(--ink-3)',
          flexWrap: 'wrap',
        }}
      >
        {hasImplied && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 18,
                height: 8,
                background: 'color-mix(in oklab, var(--brand-blue-1) 22%, transparent)',
                borderTop: '1px solid var(--brand-blue-1)',
                borderBottom: '1px solid var(--brand-blue-1)',
              }}
            />
            Implied range
          </span>
        )}
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: 999, background: 'var(--up)' }} />
          Realized (up)
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: 999, background: 'var(--down)' }} />
          Realized (down)
        </span>
        {hasImplied && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 12,
                height: 12,
                borderRadius: 999,
                border: '1.2px solid var(--ink-3)',
                display: 'inline-block',
              }}
            />
            Beat implied
          </span>
        )}
      </div>
    </div>
  );
}

// ---------- Greeks panel ----------
function GreeksPanel({ rows }: { rows: TermRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="qv-card">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 16,
        }}
      >
        <div>
          <span
            className="qv-pill"
            style={{
              background: 'color-mix(in oklab, var(--accent) 14%, transparent)',
              color: 'var(--accent-hi)',
              borderColor: 'color-mix(in oklab, var(--accent) 30%, transparent)',
            }}
          >
            ATM greeks
          </span>
          <h3
            className="serif"
            style={{
              margin: '10px 0 0',
              fontSize: 20,
              fontWeight: 700,
              letterSpacing: '-0.01em',
            }}
          >
            Greeks by expiry
          </h3>
          <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 4 }}>
            ATM call · delta hedge ratios, sensitivity to vol and time.
          </div>
        </div>
      </div>

      <div className="qv-m-table-wrap" style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--line)' }}>
              {['Expiry', 'DTE', 'ATM IV', 'Δ Delta', 'Γ Gamma', '𝜈 Vega', 'Θ Theta', 'Straddle'].map(
                (h, i) => (
                  <th
                    key={h}
                    className={i === 0 ? 'qv-m-sticky-cell' : undefined}
                    style={{
                      textAlign: i === 0 ? 'left' : 'right',
                      padding: '10px 12px',
                      fontSize: 11.5,
                      letterSpacing: '0.16em',
                      textTransform: 'uppercase',
                      color: 'var(--ink-3)',
                      fontWeight: 500,
                    }}
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const baseBg = r.isEarnings
                ? 'color-mix(in oklab, var(--brand-blue-1) 5%, transparent)'
                : 'transparent';
              return (
                <tr
                  key={r.expiration}
                  style={{
                    borderBottom: '1px solid var(--line)',
                    background: baseBg,
                    transition: 'background 140ms ease',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = 'var(--bg-3)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = baseBg;
                  }}
                >
                  <td className="qv-m-sticky-cell" style={{ padding: '12px 12px' }}>
                    <div
                      className="serif"
                      style={{
                        fontSize: 14,
                        fontWeight: 600,
                        color: 'var(--ink)',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                      }}
                    >
                      {axisDate(r.expiration)}
                      {r.isEarnings && (
                        <span
                          className="qv-pill warm"
                          style={{ fontSize: 8.5, padding: '2px 6px' }}
                        >
                          Earnings
                        </span>
                      )}
                    </div>
                    <div
                      className="mono"
                      style={{
                        fontSize: 12,
                        color: 'var(--ink-4)',
                        marginTop: 2,
                        letterSpacing: '0.08em',
                        textTransform: 'uppercase',
                      }}
                    >
                      K ${r.strike.toFixed(0)}
                    </div>
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--ink-2)' }}
                  >
                    {r.dte}d
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--ink)' }}
                  >
                    {(r.iv * 100).toFixed(2)}%
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--ink-2)' }}
                  >
                    {r.delta != null ? r.delta.toFixed(3) : '–'}
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--ink-2)' }}
                  >
                    {r.gamma != null ? r.gamma.toFixed(4) : '–'}
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--ink-2)' }}
                  >
                    {r.vega != null ? r.vega.toFixed(2) : '–'}
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--down)' }}
                  >
                    {r.theta != null ? r.theta.toFixed(2) : '–'}
                  </td>
                  <td
                    className="mono tnum"
                    style={{
                      textAlign: 'right',
                      padding: '12px 12px',
                      color: 'var(--ink)',
                      fontWeight: 600,
                    }}
                  >
                    {r.straddle > 0 ? `$${r.straddle.toFixed(2)}` : '–'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}


// ---------- Small bits ----------


/** Lightweight section wrapper. Detail pages used to mount sections hidden
 *  and reveal them after IntersectionObserver ran, which caused a visible
 *  one-frame "hero only" flash on ticker-page loads. Keep the same callsite
 *  shape, but render visible from the first paint. */
function Reveal({
  children,
  as = 'section',
  style,
  className,
}: {
  children: React.ReactNode;
  as?: 'section' | 'div';
  delay?: number;
  style?: React.CSSProperties;
  className?: string;
}) {
  const cls = `reveal in${className ? ' ' + className : ''}`;
  if (as === 'div') {
    return (
      <div className={cls} style={style}>
        {children}
      </div>
    );
  }
  return (
    <section className={cls} style={style}>
      {children}
    </section>
  );
}

// ---------- Page ----------
export default function SymbolPage({
  initialData = null,
  initialSymbol,
}: {
  initialData?: unknown;
  initialSymbol?: string;
}) {
  // Triggers EDGAR ticker-names fetch + re-render so the header company
  // name resolves even when the symbol isn't in the S&P 500 or curated map.
  useEnsureCompanyNames();
  useEnsureListingExchanges();

  const params = useParams();
  const router = useRouter();
  const symbol = (initialSymbol ?? (params.symbol as string) ?? '').toUpperCase();
  const prevLoc = usePrevAppLocation();
  const seededData = initialSymbolDetail(initialData, symbol);

  const [data, setData] = useState<SymbolDetail | null>(() => seededData);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(() => seededData === null);
  const [live, setLive] = useState<LivePrice | null>(null);
  const [quoteReady, setQuoteReady] = useState(false);
  const [predictionMode, setPredictionMode] = useState<PredictionMode>('snapshot');
  const [livePrediction, setLivePrediction] =
    useState<LivePredictionState>(EMPTY_LIVE_PREDICTION);
  const [toast, setToast] = useState<{ msg: string; key: number } | null>(null);
  const lastPredictionFetchAtRef = useRef(0);
  const inFlightPredictionKeyRef = useRef<string | null>(null);
  // Intraday sparkline state. Bars come from /api/stocks/intraday which
  // wraps Alpaca's IEX feed; we cache aggressively server-side and
  // refresh once a minute client-side during the regular session.
  const [intraday, setIntraday] = useState<{
    symbol: string;
    bars: { t: string; c: number }[];
    previousClose: number | null;
    asOf: string | null;
    sessionDate: string | null;
    isCurrentSession: boolean;
  } | null>(null);

  // Fetch intraday bars + auto-refresh every 60s during regular hours
  // so the sparkline stays in sync with the live price tick above it.
  // The endpoint caches at the edge for 30s, so polling is cheap.
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | null = null;
    const load = async () => {
      try {
        const res = await fetch(`/api/stocks/intraday?symbol=${symbol}`, {
          cache: 'no-store',
        });
        if (!res.ok || cancelled) return;
        const json = (await res.json()) as {
          bars?: { t: string; c: number }[];
          previousClose?: number | null;
          asOf?: string | null;
          sessionDate?: string | null;
          isCurrentSession?: boolean;
        };
        if (cancelled) return;
        setIntraday({
          symbol,
          bars: Array.isArray(json.bars) ? json.bars : [],
          previousClose: json.previousClose ?? null,
          asOf: json.asOf ?? null,
          sessionDate: json.sessionDate ?? null,
          isCurrentSession: json.isCurrentSession ?? false,
        });
      } catch {
        if (!cancelled) {
          setIntraday({
            symbol,
            bars: [],
            previousClose: null,
            asOf: null,
            sessionDate: null,
            isCurrentSession: false,
          });
        }
      }
    };
    void load();
    // Poll once a minute; only active while the tab is visible to avoid
    // burning quota on backgrounded pages.
    intervalId = setInterval(() => {
      if (document.visibilityState === 'visible') void load();
    }, 60_000);
    const onVisible = () => {
      if (document.visibilityState === 'visible') void load();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    const seeded = initialSymbolDetail(initialData, symbol);
    if (seeded) {
      setData(seeded);
      setError(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`/symbols/${symbol}.json`);
        if (!res.ok) throw new Error(`No local data for ${symbol}`);
        const json = (await res.json()) as SymbolDetail;
        if (!cancelled) setData(json);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initialData, symbol]);

  useEffect(() => {
    if (!symbol) return;
    setLive(null);
    setQuoteReady(false);
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let lastQuoteRefreshActive = true;
    const fetchOnce = async (): Promise<number> => {
      try {
        const res = await fetch(`/api/stocks/batch-price?symbols=${symbol}&context=symbol`, { cache: 'no-store' });
        if (!res.ok) return 0;
        const json = (await res.json()) as {
          pending?: number;
          updated: string | null;
          source: 'finnhub' | 'alpaca_iex' | 'mixed' | 'unavailable';
          session?: 'premarket' | 'regular' | 'afterhours' | 'closed';
          marketOpen?: boolean;
          quoteRefreshActive?: boolean;
          data: Array<{
            symbol: string;
            price: number | null;
            previousClose: number | null;
            change: number | null;
            changePct: number | null;
            source?: 'finnhub' | 'alpaca_iex';
            session?: 'premarket' | 'regular' | 'afterhours';
          }>;
        };
        const open = json.marketOpen ?? true;
        const refreshOn = json.quoteRefreshActive ?? open;
        lastQuoteRefreshActive = refreshOn;
        const tick = json.data?.[0];
        if (!cancelled && tick && tick.price !== null) {
          setLive({
            symbol: (tick.symbol || symbol).toUpperCase(),
            price: tick.price,
            previousClose: tick.previousClose,
            change: tick.change,
            changePct: tick.changePct,
            updated: json.updated,
            source: tick.source ?? json.source ?? 'unavailable',
            session: tick.session ?? json.session,
            marketOpen: open,
          });
        }
        return json.pending ?? 0;
      } catch {
        return 0;
      } finally {
        if (!cancelled) setQuoteReady(true);
      }
    };

    const fastPoll = async (attempt = 0) => {
      if (cancelled) return;
      const pending = await fetchOnce();
      if (pending > 0 && attempt < 30) {
        const delay = attempt < 10 ? 2_000 : 8_000;
        timer = setTimeout(() => void fastPoll(attempt + 1), delay);
      } else {
        // Slow loop: 30 s while quote refresh is active (incl. post-close), 5 min otherwise.
        const slowLoop = () => {
          if (cancelled) return;
          const interval = lastQuoteRefreshActive ? 30_000 : 300_000;
          timer = setTimeout(async () => {
            await fetchOnce();
            slowLoop();
          }, interval);
        };
        slowLoop();
      }
    };
    void fastPoll();

    const onVisible = () => {
      if (document.visibilityState === 'visible' && !cancelled) void fetchOnce();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [symbol]);

  useEffect(() => {
    setPredictionMode('snapshot');
    setLivePrediction(EMPTY_LIVE_PREDICTION);
    lastPredictionFetchAtRef.current = 0;
    inFlightPredictionKeyRef.current = null;
  }, [symbol]);

  const livePredictionRequest = useMemo(() => {
    const em = data?.expected_move;
    const horizon = em?.model_horizon;
    const earningsDate = em?.earnings_date ?? data?.next_earnings ?? null;
    const price = live?.price ?? data?.spot_price ?? null;
    if (!symbol || !data || !em || !horizon || !earningsDate || !price || price <= 0) {
      return null;
    }
    if (![1, 2, 3, 7, 14, 21].includes(horizon)) return null;
    const roundedSpot = Math.round(price * 10) / 10;
    const eventDate = earningsDate.slice(0, 10);
    return {
      key: `${symbol}:${eventDate}:T${horizon}:${roundedSpot.toFixed(1)}`,
      body: {
        symbol,
        horizon_days: horizon,
        spot_override: roundedSpot,
        earnings_date: eventDate,
      },
    };
  }, [data, live?.price, symbol]);

  const loadLivePrediction = useCallback(async (force = false) => {
    if (!livePredictionRequest) return;
    const now = Date.now();
    if (
      !force &&
      livePrediction.key === livePredictionRequest.key &&
      livePrediction.status === 'ready' &&
      now - livePrediction.updatedAt < 30_000
    ) {
      return;
    }
    if (!force && now - lastPredictionFetchAtRef.current < 30_000) return;
    if (inFlightPredictionKeyRef.current === livePredictionRequest.key) return;

    inFlightPredictionKeyRef.current = livePredictionRequest.key;
    lastPredictionFetchAtRef.current = now;
    setLivePrediction((prev) => ({
      status: 'loading',
      key: livePredictionRequest.key,
      response: prev.key === livePredictionRequest.key ? prev.response : null,
      error: null,
      updatedAt: prev.updatedAt,
    }));

    try {
      const res = await fetch('/api/ml/predict', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(livePredictionRequest.body),
        cache: 'no-store',
      });
      const json = await res.json().catch(() => null);
      if (!res.ok) {
        setLivePrediction({
          status: 'unavailable',
          key: livePredictionRequest.key,
          response: null,
          error: livePredictionUnavailableMessage(res.status),
          updatedAt: Date.now(),
        });
        return;
      }
      setLivePrediction({
        status: 'ready',
        key: livePredictionRequest.key,
        response: json as LivePredictionResponse,
        error: null,
        updatedAt: Date.now(),
      });
    } catch {
      setLivePrediction({
        status: 'unavailable',
        key: livePredictionRequest.key,
        response: null,
        error: livePredictionUnavailableMessage(null),
        updatedAt: Date.now(),
      });
    } finally {
      inFlightPredictionKeyRef.current = null;
    }
  }, [livePrediction.key, livePrediction.status, livePrediction.updatedAt, livePredictionRequest]);

  useEffect(() => {
    if (predictionMode === 'live') void loadLivePrediction();
  }, [loadLivePrediction, predictionMode]);

  useEffect(() => {
    if (predictionMode !== 'live') return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') void loadLivePrediction();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [loadLivePrediction, predictionMode]);

  const showToast = useCallback((msg: string) => {
    setToast({ msg, key: Date.now() });
  }, []);

  if (loading) {
    return <SymbolPageLoading symbol={symbol} />;
  }

  if (data && data.symbol.toUpperCase() !== symbol) {
    return <SymbolPageLoading symbol={symbol} />;
  }

  if (error || !data) {
    // Limited view for tickers that exist (search hit, valid ticker)
    // but have no pre-built /symbols/SYM.json (e.g. not in the options
    // universe yet, or in flight to be added). We still know the
    // company name (sp500 lookup) and can usually show a live quote
    // via the batch-price API.
    const tick = live?.symbol === symbol ? live : null;
    const knownName = companyName(symbol);
    const hasFriendlyName = knownName !== symbol;
    const pct = tick?.changePct;
    const chg = tick?.change;
    const flat = pct != null && Math.round(pct * 10000) / 10000 === 0;
    const upMove = !flat && (pct ?? 0) >= 0;
    const arrow = pct == null ? '' : flat ? '–' : upMove ? '▲' : '▼';
    const tone = pct == null ? 'var(--ink-3)' : flat ? 'var(--ink-4)' : upMove ? 'var(--up)' : 'var(--down)';

    return (
      <div style={{ maxWidth: 720, margin: '0 auto', padding: '40px 28px' }}>
        <button
          onClick={() => {
            if (window.history.length > 1) router.back();
            else router.push(prevLoc.path);
          }}
          className="chip"
          style={{ border: 'none', color: 'var(--ink-3)', paddingLeft: 0, cursor: 'pointer' }}
        >
          <ChevronLeft size={14} /> {prevLoc.label}
        </button>

        {/* Header: logo + ticker + company name + live quote (when
            available). Matches the look of the full ticker page header
            so the page doesn't read as "broken". */}
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
              style={{ fontSize: 36, fontWeight: 800, letterSpacing: '-0.025em', lineHeight: 1 }}
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
                style={{ fontSize: 24, fontWeight: 700, letterSpacing: '-0.015em' }}
              >
                ${tick.price.toFixed(2)}
              </div>
              {pct != null && (
                <div className="mono tnum" style={{ fontSize: 12, color: tone, marginTop: 2 }}>
                  {arrow} {chg != null ? `$${Math.abs(chg).toFixed(2)} ` : ''}
                  ({Math.abs(pct * 100).toFixed(2)}%)
                </div>
              )}
            </div>
          )}
        </div>

        {/* Friendly missing-data card — replaces the red error block.
            Tone is neutral, not destructive, because the user reached
            here via the search bar and the ticker is real; we just
            haven't ingested its options data. */}
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
          <div className="serif" style={{ fontSize: 18, fontWeight: 600, color: 'var(--ink)', marginBottom: 8 }}>
            We don&apos;t have an options snapshot for {symbol} yet.
          </div>
          <div style={{ color: 'var(--ink-3)', fontSize: 13 }}>
            Expected moves, IV rank, straddle quotes, and the historical
            implied-vs-actual chart all require an options-chain ingest
            for this symbol. Live spot quotes above {tick?.price != null ? 'are working' : 'will appear when the market is open'}.
          </div>
        </div>
      </div>
    );
  }

  const em = data.expected_move;
  const liveForSymbol = live?.symbol === symbol ? live : null;
  const intradayForSymbol = intraday?.symbol === symbol ? intraday : null;
  const livePrice = liveForSymbol?.price ?? null;
  const quotePending = !quoteReady && livePrice == null;
  const spot = livePrice ?? data.spot_price ?? 0;
  const livePreviousCloseForChange =
    intradayForSymbol === null ? null : liveForSymbol?.previousClose ?? null;
  const previousCloseForChange =
    intradayForSymbol?.previousClose ?? livePreviousCloseForChange;
  const useLiveChangeFallback = intradayForSymbol !== null;
  const change =
    livePrice != null && previousCloseForChange != null && previousCloseForChange > 0
      ? livePrice - previousCloseForChange
      : useLiveChangeFallback
        ? liveForSymbol?.change ?? 0
        : 0;
  const changePct =
    livePrice != null && previousCloseForChange != null && previousCloseForChange > 0
      ? change / previousCloseForChange
      : useLiveChangeFallback
        ? liveForSymbol?.changePct ?? 0
        : 0;

  const straddlePct = em?.straddle_pct ?? 0;
  const ivPct = em?.iv_pct ?? straddlePct;
  const atmIV = em?.atm_iv ?? 0.3;
  const dte = em?.dte ?? 28;

  const earningsDate = em?.earnings_date ?? data.next_earnings ?? null;
  const earningsTiming = timingText(em?.timing ?? data.next_earnings_timing);
  const daysLeft = daysFromToday(earningsDate);
  const eventLabel = eventLabelFor(data, earningsDate);

  const snapshotQuantiles =
    em?.p10 != null && em?.p50 != null && em?.p90 != null
      ? {
          p10: em.p10,
          p25: em.p25 ?? em.p10,
          p50: em.p50,
          p75: em.p75 ?? em.p90,
          p90: em.p90,
        }
      : null;
  const liveQuantiles =
    livePrediction.status === 'ready'
      ? normalizeQuantiles(livePrediction.response?.quantiles)
      : null;
  const showingLivePrediction =
    predictionMode === 'live' &&
    livePrediction.status === 'ready' &&
    livePrediction.response != null;
  const quantiles = showingLivePrediction && liveQuantiles ? liveQuantiles : snapshotQuantiles;
  const activePredictionPct = showingLivePrediction
    ? livePrediction.response?.em_ml_pct ?? null
    : em?.em_ml_pct ?? null;
  const quantileMeta = showingLivePrediction
    ? livePrediction.response?.source === 'nightly_fallback'
      ? 'Static nightly fallback · live backend unavailable'
      : `Re-scored with latest stock price; options snapshot from ${
          livePrediction.response?.feature_snapshot_date ?? 'nightly snapshot'
        }.`
    : em?.ml_snapshot_date
      ? `Nightly LightGBM snapshot from ${em.ml_snapshot_date}.`
      : 'LightGBM ensemble · range of plausible absolute moves on print day';
  const liveUnavailableReason =
    predictionMode === 'live' && livePrediction.status === 'unavailable'
      ? livePrediction.error
      : null;

  const termRows = buildTermRows(
    data.straddle_features,
    em?.expiration ?? null,
  );
  const historySeries = buildHistorySeries(data.earnings_history);

  return (
    <div className="qv-m-pad qv-symbol-page-shell" style={{ maxWidth: 1100, margin: '0 auto', padding: '0 28px 80px' }}>
      {toast && <Toast key={toast.key} message={toast.msg} onDone={() => setToast(null)} />}

      <Reveal as="div" style={{ marginTop: 8 }}>
        <DetailHero
          ticker={companyName(symbol)}
          symbol={symbol}
          spot={spot}
          change={change}
          changePct={changePct}
          quotePending={quotePending}
          emPct={straddlePct}
          daysLeft={daysLeft}
          earningsDate={earningsDate}
          earningsTiming={earningsTiming}
          eventLabel={eventLabel}
          quoteLabel={quoteSourceLabel(liveForSymbol, symbol, data.as_of_date)}
          intradayBars={
            !quotePending && intradayForSymbol?.bars && intradayForSymbol.bars.length >= 2
              ? intradayForSymbol.bars
              : null
          }
          intradayLoading={quotePending || intradayForSymbol === null}
          intradaySessionDate={intradayForSymbol?.sessionDate ?? null}
          intradayIsCurrentSession={intradayForSymbol?.isCurrentSession ?? null}
          // Real session %: first→last close on the displayed IEX session.
          intradaySessionPct={(() => {
            const bars = intradayForSymbol?.bars;
            if (!bars || bars.length < 2) return null;
            const first = bars[0].c;
            const last = bars[bars.length - 1].c;
            if (!first || !last) return null;
            return last / first - 1;
          })()}
          onBack={() => {
            if (window.history.length > 1) router.back();
            else router.push(prevLoc.path);
          }}
          backLabel={prevLoc.label}
          onToast={showToast}
        />
      </Reveal>

      {/* KPI strip */}
      {em && (
        <Reveal delay={80}>
          <div
            className="qv-m-2col"
            style={{
              marginTop: 22,
              display: 'grid',
              gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
              gap: 14,
            }}
          >
            <KpiCard
              label="ATM IV"
              value={em.atm_iv != null ? `${(em.atm_iv * 100).toFixed(1)}%` : '–'}
              sub={
                em.term_slope != null
                  ? `Δ term slope ${(em.term_slope * 100).toFixed(1)} v/30d`
                  : `Front-month, ${em.dte}d`
              }
              accent="var(--brand-blue-1)"
            />
            <KpiCard
              label="IV-based EM"
              value={em.iv_pct != null ? `±${(em.iv_pct * 100).toFixed(1)}%` : '–'}
              sub={
                em.atm_iv != null
                  ? `σ ${(em.atm_iv * 100).toFixed(0)}% · ${em.dte}d`
                  : undefined
              }
            />
            <KpiCard
              label="ATM Straddle"
              value={em.straddle_abs != null ? `$${em.straddle_abs.toFixed(2)}` : '–'}
              sub={`Strike $${em.atm_strike.toFixed(2)}`}
            />
            <KpiCard
              label={data.vol_regime?.iv_rank != null ? 'IV Rank' : 'ATM Skew'}
              value={
                data.vol_regime?.iv_rank != null
                  ? `${Math.round(data.vol_regime.iv_rank * 100)}%`
                  : em.skew_atm != null
                    ? `${(em.skew_atm * 100).toFixed(2)}v`
                    : '–'
              }
              sub={
                data.vol_regime?.iv_rank != null
                  ? data.vol_regime.iv_year_low != null && data.vol_regime.iv_year_high != null
                    ? `52w ${(data.vol_regime.iv_year_low * 100).toFixed(0)}–${(data.vol_regime.iv_year_high * 100).toFixed(0)}%`
                    : undefined
                  : em.total_vega != null
                    ? `Vega $${(em.total_vega * 1000).toFixed(0)}`
                    : undefined
              }
            />
          </div>
        </Reveal>
      )}

      {/* Interactive density bar */}
      {em && spot > 0 && straddlePct > 0 && (
        <Reveal delay={120}>
          <div className="qv-card" style={{ marginTop: 22 }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'flex-start',
                gap: 16,
              }}
            >
              <div>
                <span className="qv-pill">Expected move</span>
                <h3
                  className="serif"
                  style={{
                    margin: '10px 0 0',
                    fontSize: 20,
                    fontWeight: 700,
                    letterSpacing: '-0.01em',
                  }}
                >
                  Probability density around spot
                </h3>
                <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 4 }}>
                  Log-normal model with ATM IV {(atmIV * 100).toFixed(1)}% over {dte} days.
                  Range ${(spot * (1 - straddlePct)).toFixed(2)}–${(spot * (1 + straddlePct)).toFixed(2)}.
                </div>
              </div>
            </div>
            <InteractiveBar
              spot={spot}
              em={straddlePct}
              emIV={ivPct}
              atmIV={atmIV}
              dte={dte}
            />
          </div>
        </Reveal>
      )}

      {/* Quantile band + Term fan side-by-side on wide; stacked otherwise */}
      {(quantiles != null || termRows.length > 0) && (
        <Reveal delay={160}>
          <div
            className="qv-m-stack"
            style={{
              marginTop: 18,
              display: 'grid',
              gridTemplateColumns:
                quantiles != null && termRows.length > 0 ? '1fr 1.1fr' : '1fr',
              gap: 16,
            }}
          >
            {quantiles != null && (
              <QuantileBand
                q={quantiles}
                straddleAbs={straddlePct}
                spot={spot}
                mode={predictionMode}
                onModeChange={setPredictionMode}
                liveDisabled={livePredictionRequest == null}
                liveStatus={livePrediction.status}
                pointPct={activePredictionPct}
                modelMeta={quantileMeta}
                unavailableReason={liveUnavailableReason}
              />
            )}
            {termRows.length > 0 && <TermFan rows={termRows} spot={spot} />}
          </div>
        </Reveal>
      )}

      {/* History + EPS surprise */}
      {historySeries.length >= 2 && (
        <Reveal delay={200}>
          <div style={{ marginTop: 18 }}>
            <HistoryBlock history={historySeries} />
          </div>
        </Reveal>
      )}

      {/* Greeks panel */}
      {termRows.length > 0 && (
        <Reveal delay={240}>
          <div style={{ marginTop: 18 }}>
            <GreeksPanel rows={termRows} />
          </div>
        </Reveal>
      )}

      {/* Footer */}
      <Reveal delay={280}>
        <div
          style={{
            marginTop: 32,
            padding: '18px 0 0',
            borderTop: '1px solid var(--line)',
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: 11,
            color: 'var(--ink-4)',
            flexWrap: 'wrap',
            gap: 10,
          }}
        >
          <span className="mono">
            Options data · as of {data.as_of_date}
          </span>
          <span>
            Method:{' '}
            {em?.em_method === 'ml_lightgbm'
              ? 'ML forecast'
              : em?.em_method === 'ensemble'
                ? 'Math + ML'
                : 'options math baseline'}
          </span>
        </div>
      </Reveal>
    </div>
  );
}
