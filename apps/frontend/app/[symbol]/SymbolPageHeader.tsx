'use client';

import { useEffect, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import { ChevronLeft } from 'lucide-react';
import { SignedIn, SignedOut, SignInButton } from '@clerk/nextjs';
import type { MetricKey } from '@/lib/metricGlossary';
import { useWatchlist } from '@/lib/watchlist';
import { MetricHelp } from '@/components/MetricExplainer';
import { TickerLogo } from '@/components/TickerLogo';
import type { ProviderEnrichment } from './symbolPageTypes';
import { parseLocalDate } from './symbolPageUtils';

export function SkeletonBlock({
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
              } as CSSProperties
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

export function Toast({ message, onDone }: { message: string; onDone: () => void }) {
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
export function KpiCard({
  label,
  value,
  sub,
  accent,
  kicker,
  metric,
  helpAlign = 'right',
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
  kicker?: string;
  metric: MetricKey;
  helpAlign?: 'left' | 'right';
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
        className="qv-metric-label-row"
        style={{
          fontSize: 10,
          letterSpacing: '0.16em',
          textTransform: 'uppercase',
          color: 'var(--ink-3)',
          marginTop: kicker ? 2 : 0,
        }}
      >
        <span>{label}</span>
        <MetricHelp metric={metric} align={helpAlign} />
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

function compactNumber(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '–';
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value);
}

function ratioValue(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || value <= 0) return '–';
  return `${value.toFixed(2)}x`;
}

function ProviderSignalMetric({
  label,
  value,
  sub,
  tone,
  metric,
  helpAlign = 'right',
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: string;
  metric: MetricKey;
  helpAlign?: 'left' | 'right';
}) {
  return (
    <div
      style={{
        padding: '12px 14px',
        borderRadius: 8,
        border: '1px solid var(--line)',
        background: 'color-mix(in oklab, var(--bg-2) 72%, transparent)',
        minWidth: 0,
      }}
    >
      <div
        className="qv-metric-label-row"
        style={{
          fontSize: 9.5,
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          color: 'var(--ink-4)',
        }}
      >
        <span>{label}</span>
        <MetricHelp metric={metric} align={helpAlign} />
      </div>
      <div
        className="serif tnum"
        style={{
          marginTop: 7,
          fontSize: 24,
          fontWeight: 700,
          lineHeight: 1,
          color: tone || 'var(--ink)',
        }}
      >
        {value}
      </div>
      {sub && (
        <div
          className="mono tnum"
          style={{
            marginTop: 8,
            fontSize: 11,
            color: 'var(--ink-4)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={sub}
        >
          {sub}
        </div>
      )}
    </div>
  );
}

export function ProviderSignalsPanel({ enrichment }: { enrichment?: ProviderEnrichment | null }) {
  if (!enrichment) return null;
  const short = enrichment.short_interest;
  const flow = enrichment.options_flow;
  const actions = enrichment.corporate_actions;
  const hasSignals = short || flow || actions;
  if (!hasSignals) return null;

  const daysToCover = short?.days_to_cover ?? null;
  const pcVolume = flow?.put_call_volume_ratio ?? null;
  const pcOi = flow?.put_call_open_interest_ratio ?? null;
  const shortTone =
    daysToCover != null && daysToCover >= 5
      ? 'var(--flag)'
      : daysToCover != null && daysToCover >= 3
        ? 'var(--accent-hi)'
        : undefined;
  const flowTone =
    pcVolume != null && pcVolume >= 1.25
      ? 'var(--down)'
      : pcVolume != null && pcVolume <= 0.75
        ? 'var(--up)'
        : undefined;
  const sourceText = [
    ...(enrichment.sources ?? []),
    short?.settlement_date ? `short ${short.settlement_date}` : null,
    flow?.iv_coverage_pct != null ? `IV coverage ${flow.iv_coverage_pct.toFixed(0)}%` : null,
  ].filter(Boolean).join(' · ');

  return (
    <div className="qv-card" style={{ marginTop: 22 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: 16,
          marginBottom: 16,
        }}
      >
        <div>
          <span className="qv-pill">Market context</span>
          <h3
            className="serif"
            style={{
              margin: '10px 0 0',
              fontSize: 20,
              fontWeight: 700,
              letterSpacing: '-0.01em',
            }}
          >
            Short interest, options flow, and corporate actions
          </h3>
        </div>
        {enrichment.signal_score != null && (
          <div
            className="mono tnum qv-signal-score"
            style={{ fontSize: 11, color: 'var(--ink-4)', textAlign: 'right' }}
          >
            <span>Positioning score {(enrichment.signal_score * 100).toFixed(0)}/100</span>
            <MetricHelp metric="providerSignalScore" />
          </div>
        )}
      </div>
      <div
        className="qv-m-2col"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 12,
        }}
      >
        <ProviderSignalMetric
          label="Days to cover"
          value={daysToCover == null ? '–' : daysToCover.toFixed(1)}
          sub={short?.shares != null ? `${compactNumber(short.shares)} shares short` : undefined}
          tone={shortTone}
          metric="daysToCover"
          helpAlign="left"
        />
        <ProviderSignalMetric
          label="P/C volume"
          value={ratioValue(pcVolume)}
          sub={
            flow?.total_put_volume != null || flow?.total_call_volume != null
              ? `${compactNumber(flow?.total_put_volume)} puts / ${compactNumber(flow?.total_call_volume)} calls`
              : undefined
          }
          tone={flowTone}
          metric="putCallVolume"
          helpAlign="left"
        />
        <ProviderSignalMetric
          label="P/C OI"
          value={ratioValue(pcOi)}
          sub={
            flow?.total_put_open_interest != null || flow?.total_call_open_interest != null
              ? `${compactNumber(flow?.total_put_open_interest)} puts / ${compactNumber(flow?.total_call_open_interest)} calls`
              : undefined
          }
          metric="putCallOpenInterest"
        />
        <ProviderSignalMetric
          label="Dividends / splits"
          value={`${actions?.dividend_events ?? 0}/${actions?.split_events ?? 0}`}
          sub="recent provider events"
          metric="corporateActions"
        />
      </div>
      {sourceText && (
        <div className="mono" style={{ marginTop: 14, fontSize: 11, color: 'var(--ink-4)' }}>
          {sourceText}
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
export function usePrevAppLocation(): { label: string; path: string } {
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
export function DetailHero({
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
  // The sparkline and its % both track the authoritative day change (vs the
  // official previous close, from batch-price) so the ticker page matches the
  // calendar. The IEX bars provide the intraday shape only — they previously
  // drove a first-bar→last-bar % that excluded the overnight earnings gap and
  // disagreed with the calendar's LIVE %.
  const sparkUp = up;
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
                        changePct == null || flat
                          ? 'var(--ink-4)'
                          : changePct > 0
                            ? 'var(--up)'
                            : 'var(--down)',
                    }}
                  >
                    {/* Authoritative day change vs official previous close (the
                        same number the calendar's LIVE % shows). */}
                    {changePct == null
                      ? '--'
                      : `${changePct >= 0 ? '+' : ''}${(changePct * 100).toFixed(2)}%`}
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
