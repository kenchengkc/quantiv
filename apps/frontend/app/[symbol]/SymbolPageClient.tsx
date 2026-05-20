'use client';

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ChevronLeft } from 'lucide-react';
import { SignedIn, SignedOut, SignInButton } from '@clerk/nextjs';
import { companyName } from '@/lib/companyNames';
import { listingExchangeLabel } from '@/lib/listingExchanges';
import { useEnsureCompanyNames } from '@/lib/useCompanyNames';
import { useEnsureListingExchanges } from '@/lib/useListingExchanges';
import { useWatchlist } from '@/lib/watchlist';

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
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
  updated: string | null;
  source: 'finnhub' | 'alpaca_iex' | 'mixed' | 'unavailable';
  session?: 'premarket' | 'regular' | 'afterhours' | 'closed';
  marketOpen: boolean;
}

function logoUrl(t: string) {
  return `https://assets.parqet.com/logos/symbol/${t}?format=png`;
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

function quoteSourceLabel(live: LivePrice | null, ticker: string, asOfDate: string): string {
  if (!live) return `As of ${asOfDate}`;
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

// ---------- Logo ----------
function Logo({ ticker, size = 60 }: { ticker: string; size?: number }) {
  const [err, setErr] = useState(false);
  const s = { width: size, height: size };
  if (err) {
    return (
      <div
        className="serif"
        style={{
          ...s,
          borderRadius: 10,
          background: 'var(--bg-3)',
          border: '1px solid var(--line)',
          display: 'grid',
          placeItems: 'center',
          color: 'var(--ink-2)',
          fontSize: Math.max(14, size * 0.32),
          fontWeight: 700,
        }}
      >
        {ticker.slice(0, 3)}
      </div>
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return (
    <img
      src={logoUrl(ticker)}
      alt={ticker}
      onError={() => setErr(true)}
      style={{
        ...s,
        borderRadius: 10,
        objectFit: 'cover',
        background: 'var(--paper)',
        border: '1px solid var(--line)',
      }}
    />
  );
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
        color: 'var(--ink)',
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

// ---------- Interactive Bar ----------
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

  const emLow = toX(-em);
  const emHigh = toX(em);
  const ivLow = toX(-emIV);
  const ivHigh = toX(emIV);
  const spotX = toX(0);

  const current = hover || pinned;

  return (
    <div style={{ marginTop: 32, marginBottom: 10 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 10,
          color: 'var(--ink-4)',
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          marginBottom: 12,
        }}
      >
        <span>{`$${(spot * (1 + minPct)).toFixed(2)} · ${(minPct * 100).toFixed(1)}%`}</span>
        <span>Spot ${spot.toFixed(2)}</span>
        <span>{`$${(spot * (1 + maxPct)).toFixed(2)} · +${(maxPct * 100).toFixed(1)}%`}</span>
      </div>

      <div
        ref={wrapRef}
        onMouseMove={onMove}
        onMouseLeave={onLeave}
        onClick={onClick}
        style={{
          position: 'relative',
          height: 88,
          cursor: 'crosshair',
          userSelect: 'none',
        }}
      >
        <svg
          viewBox="0 0 100 88"
          preserveAspectRatio="none"
          width="100%"
          height="88"
          style={{ position: 'absolute', inset: 0 }}
        >
          <defs>
            <linearGradient id="den" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.35" />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path
            d={
              `M 0,88 ` +
              density.map((p) => `L ${p.x},${88 - p.y * 72}`).join(' ') +
              ` L 100,88 Z`
            }
            fill="url(#den)"
          />
          <path
            d={density.map((p, i) => `${i ? 'L' : 'M'}${p.x},${88 - p.y * 72}`).join(' ')}
            stroke="var(--accent)"
            strokeWidth="0.5"
            fill="none"
            vectorEffect="non-scaling-stroke"
          />
        </svg>

        <div
          style={{
            position: 'absolute',
            top: 56,
            height: 12,
            left: `${emLow}%`,
            width: `${emHigh - emLow}%`,
            background: 'var(--ink)',
            opacity: 0.95,
            borderRadius: 2,
          }}
        />
        <div
          style={{
            position: 'absolute',
            top: 54,
            height: 16,
            left: `${ivLow}%`,
            width: `${ivHigh - ivLow}%`,
            borderLeft: '1px solid var(--line-2)',
            borderRight: '1px solid var(--line-2)',
            pointerEvents: 'none',
          }}
        />

        <div
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            top: 62,
            height: 1,
            background: 'var(--line)',
          }}
        />

        <div
          style={{
            position: 'absolute',
            left: `${spotX}%`,
            top: 46,
            bottom: 16,
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
            fontSize: 10,
            color: 'var(--ink-2)',
            letterSpacing: '0.04em',
          }}
        >
          SPOT
        </div>

        <div
          className="mono tnum"
          style={{
            position: 'absolute',
            top: 44,
            left: `${emLow}%`,
            transform: 'translateX(-50%)',
            fontSize: 10,
            color: 'var(--down)',
          }}
        >
          −{(em * 100).toFixed(1)}%
        </div>
        <div
          className="mono tnum"
          style={{
            position: 'absolute',
            top: 44,
            left: `${emHigh}%`,
            transform: 'translateX(-50%)',
            fontSize: 10,
            color: 'var(--up)',
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
              padding: '8px 10px',
              borderRadius: 6,
              fontSize: 11,
              whiteSpace: 'nowrap',
              boxShadow: '0 10px 30px rgba(0,0,0,.4)',
              pointerEvents: 'none',
            }}
          >
            <div
              className="serif tnum"
              style={{ fontSize: 16, color: 'var(--ink)', lineHeight: 1 }}
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
              {(current.prob * 100).toFixed(1)}% chance to move this far or more
            </div>
          </div>
        )}
      </div>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginTop: 14,
          fontSize: 11,
          color: 'var(--ink-3)',
        }}
      >
        <div style={{ display: 'flex', gap: 16 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 18, height: 4, background: 'var(--ink)' }} /> Straddle band
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 18,
                height: 10,
                borderLeft: '1px solid var(--line-2)',
                borderRight: '1px solid var(--line-2)',
              }}
            />{' '}
            IV band
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 18,
                height: 4,
                background: 'color-mix(in oklab, var(--accent) 60%, transparent)',
              }}
            />{' '}
            Log-normal density
          </span>
        </div>
        <span style={{ fontStyle: 'italic' }}>Hover for probability · click to pin</span>
      </div>
    </div>
  );
}

// ML prediction band — shows P10/P25/P50/P75/P90 quantiles with straddle tick for comparison.
function MLBand({
  p10,
  p25,
  p50,
  p75,
  p90,
  straddle,
}: {
  p10: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
  straddle: number;
}) {
  const max = Math.max(p90, straddle) * 1.1;
  const pct = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  return (
    <div style={{ marginTop: 4 }}>
      <div
        style={{
          position: 'relative',
          height: 34,
          background: 'var(--bg-2)',
          borderRadius: 4,
          overflow: 'hidden',
        }}
      >
        {/* 80% band (p10–p90) */}
        <div
          style={{
            position: 'absolute',
            left: pct(p10),
            width: `calc(${pct(p90)} - ${pct(p10)})`,
            top: 0,
            bottom: 0,
            background: 'color-mix(in srgb, var(--accent) 18%, transparent)',
          }}
        />
        {/* 50% band (p25–p75) */}
        <div
          style={{
            position: 'absolute',
            left: pct(p25),
            width: `calc(${pct(p75)} - ${pct(p25)})`,
            top: 0,
            bottom: 0,
            background: 'color-mix(in srgb, var(--accent) 36%, transparent)',
          }}
        />
        {/* P50 tick */}
        <div
          style={{
            position: 'absolute',
            left: pct(p50),
            top: 0,
            bottom: 0,
            width: 2,
            background: 'var(--accent)',
          }}
        />
        {/* Straddle reference tick */}
        <div
          style={{
            position: 'absolute',
            left: pct(straddle),
            top: -4,
            bottom: -4,
            width: 1,
            background: 'var(--ink-2)',
            borderTop: '6px solid var(--ink-2)',
            borderBottom: '6px solid var(--ink-2)',
          }}
          title={`ATM straddle ±${(straddle * 100).toFixed(2)}%`}
        />
      </div>
      <div
        className="mono tnum"
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 10,
          color: 'var(--ink-4)',
          marginTop: 4,
        }}
      >
        <span>0%</span>
        <span>P10 ±{(p10 * 100).toFixed(1)}%</span>
        <span style={{ color: 'var(--accent)' }}>P50 ±{(p50 * 100).toFixed(1)}%</span>
        <span>P90 ±{(p90 * 100).toFixed(1)}%</span>
      </div>
    </div>
  );
}

// Term-structure chart — two lines from today's spot: spot×(1±EM) at each
// expiry. X axis is days-to-expiry. Hover shows the same fields as the table.
function TermStructureFan({
  asOf,
  spot,
  expiries,
  highlightExpiration,
  onHighlightExpiration,
}: {
  asOf: string;
  spot: number;
  expiries: Straddle[];
  highlightExpiration: string | null;
  onHighlightExpiration: (exp: string | null) => void;
}) {
  // Only plot expiries with a usable straddle %.
  const rows = expiries
    .filter((e) => e.em_straddle_pct !== null && e.em_straddle_pct !== undefined)
    .sort(
      (a, b) =>
        parseLocalDate(a.expiration).getTime() - parseLocalDate(b.expiration).getTime(),
    );
  if (rows.length === 0) return null;

  const asOfDate = parseLocalDate(asOf);
  const points = rows.map((r) => {
    const exp = parseLocalDate(r.expiration);
    const dte = Math.max(
      1,
      Math.round((exp.getTime() - asOfDate.getTime()) / 86_400_000),
    );
    const em = r.em_straddle_pct as number;
    return { row: r, expiration: r.expiration, dte, up: spot * (1 + em), down: spot * (1 - em) };
  });

  const maxDte = points[points.length - 1].dte;
  const maxPrice = Math.max(...points.map((p) => p.up));
  const minPrice = Math.min(...points.map((p) => p.down));
  // 6% vertical padding so labels don't kiss the edge.
  const pad = (maxPrice - minPrice) * 0.06;
  const yMax = maxPrice + pad;
  const yMin = minPrice - pad;

  // Layout.
  const W = 760;
  const H = 280;
  const M = { top: 14, right: 72, bottom: 34, left: 56 };
  const innerW = W - M.left - M.right;
  const innerH = H - M.top - M.bottom;

  const x = (dte: number) => M.left + (dte / maxDte) * innerW;
  const y = (price: number) =>
    M.top + innerH - ((price - yMin) / (yMax - yMin)) * innerH;

  const upPath =
    `M ${x(0)} ${y(spot)} ` +
    points.map((p) => `L ${x(p.dte)} ${y(p.up)}`).join(' ');
  const downPath =
    `M ${x(0)} ${y(spot)} ` +
    points.map((p) => `L ${x(p.dte)} ${y(p.down)}`).join(' ');

  // Y ticks: 4 evenly spaced price values.
  const yTicks = Array.from({ length: 5 }, (_, i) => yMin + ((yMax - yMin) * i) / 4);

  const tip = points.find((p) => p.expiration === highlightExpiration);

  return (
    <div style={{ marginTop: 24 }}>
      <div
        style={{
          fontSize: 10,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color: 'var(--ink-3)',
          marginBottom: 4,
        }}
      >
        Implied range by expiry
      </div>
      <div
        className="mono tnum"
        style={{ fontSize: 10, color: 'var(--ink-4)', marginBottom: 10, lineHeight: 1.35 }}
      >
        Hover a date — same numbers as the table above. Green / red: spot × (1 ± straddle EM).
      </div>
      <div style={{ width: '100%', overflowX: 'auto' }}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label="Implied price range by expiration; hover points for expiry details"
          style={{ width: '100%', maxWidth: W, display: 'block' }}
        >
          {/* Y grid + labels */}
          {yTicks.map((v, i) => (
            <g key={i}>
              <line
                x1={M.left}
                x2={W - M.right}
                y1={y(v)}
                y2={y(v)}
                stroke="var(--line)"
                strokeWidth={i === 0 || i === yTicks.length - 1 ? 1 : 0.5}
                strokeDasharray={i === 0 || i === yTicks.length - 1 ? 'none' : '2 3'}
              />
              <text
                x={M.left - 8}
                y={y(v)}
                textAnchor="end"
                dominantBaseline="central"
                fontSize="10"
                fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                fill="var(--ink-4)"
              >
                ${v.toFixed(2)}
              </text>
            </g>
          ))}

          {/* Spot reference line */}
          <line
            x1={M.left}
            x2={W - M.right}
            y1={y(spot)}
            y2={y(spot)}
            stroke="var(--ink-3)"
            strokeDasharray="3 3"
            strokeWidth={1}
          />

          {/* X axis baseline */}
          <line
            x1={M.left}
            x2={W - M.right}
            y1={H - M.bottom}
            y2={H - M.bottom}
            stroke="var(--line)"
            strokeWidth={1}
          />

          {/* Fan lines */}
          <path d={upPath} fill="none" stroke="var(--up)" strokeWidth={2} />
          <path d={downPath} fill="none" stroke="var(--down)" strokeWidth={2} />

          {/* Origin dot (today, spot) */}
          <circle cx={x(0)} cy={y(spot)} r={4.5} fill="var(--ink)" />

          {/* Per-expiry: wide invisible hit targets + dots + labels */}
          {points.map((p, i) => {
            const showLabel = i === points.length - 1 || i % Math.max(1, Math.floor(points.length / 4)) === 0;
            const hitW = Math.max(22, innerW / Math.max(points.length * 1.2, 8));
            const hx = x(p.dte) - hitW / 2;
            const hy = M.top;
            const hH = H - M.bottom - M.top;
            const active = highlightExpiration === p.expiration;
            return (
              <g key={p.expiration}>
                <rect
                  x={hx}
                  y={hy}
                  width={hitW}
                  height={hH}
                  fill="transparent"
                  style={{ cursor: 'pointer' }}
                  onMouseEnter={() => onHighlightExpiration(p.expiration)}
                  onMouseLeave={() => onHighlightExpiration(null)}
                />
                {active && (
                  <line
                    x1={x(p.dte)}
                    x2={x(p.dte)}
                    y1={hy}
                    y2={hy + hH}
                    stroke="color-mix(in srgb, var(--accent) 55%, transparent)"
                    strokeWidth={2}
                    pointerEvents="none"
                  />
                )}
                <circle
                  cx={x(p.dte)}
                  cy={y(p.up)}
                  r={active ? 4.5 : 3}
                  fill="var(--up)"
                  pointerEvents="none"
                />
                <circle
                  cx={x(p.dte)}
                  cy={y(p.down)}
                  r={active ? 4.5 : 3}
                  fill="var(--down)"
                  pointerEvents="none"
                />
                {showLabel && (
                  <text
                    x={x(p.dte)}
                    y={H - M.bottom + 16}
                    textAnchor="middle"
                    fontSize="10"
                    fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                    fill={active ? 'var(--ink)' : 'var(--ink-4)'}
                    pointerEvents="none"
                  >
                    {shortDate(p.expiration)?.replace(/^\w{3},\s*/, '')}
                  </text>
                )}
              </g>
            );
          })}

          {/* Hover card — pure SVG so we avoid foreignObject / xmlns typing issues */}
          {tip && (() => {
            const tx = Math.min(Math.max(M.left + 4, x(tip.dte) + 10), W - M.right - 188);
            const ty = Math.max(M.top + 6, (y(tip.up) + y(tip.down)) / 2 - 78);
            const rowH = 13;
            const lines: [string, string][] = [
              ['DTE', `${tip.row.dte}d`],
              ['ATM IV', tip.row.atm_iv !== null ? `${(tip.row.atm_iv * 100).toFixed(2)}%` : '—'],
              ['Straddle', tip.row.straddle_mid !== null ? `$${tip.row.straddle_mid.toFixed(2)}` : '—'],
              ['EM math', tip.row.em_straddle_pct !== null ? `±${(tip.row.em_straddle_pct * 100).toFixed(2)}%` : '—'],
              ['EM IV', tip.row.em_iv_pct !== null ? `±${(tip.row.em_iv_pct * 100).toFixed(2)}%` : '—'],
              ['Band', `$${tip.down.toFixed(2)}–$${tip.up.toFixed(2)}`],
            ];
            const title = shortDate(tip.row.expiration)?.replace(/^\w{3},\s*/, '') ?? tip.row.expiration;
            return (
              <g pointerEvents="none">
                <rect
                  x={tx}
                  y={ty}
                  width={184}
                  height={22 + rowH * (lines.length + 1)}
                  rx={8}
                  fill="var(--bg-2)"
                  stroke="var(--line-2)"
                  strokeWidth={1}
                />
                <text
                  x={tx + 10}
                  y={ty + 18}
                  fontSize={11}
                  fontFamily="Mulish, ui-sans-serif, system-ui, sans-serif"
                  fontWeight={700}
                  fill="var(--ink)"
                >
                  {title}
                </text>
                {lines.map(([k, v], i) => (
                  <g key={k}>
                    <text
                      x={tx + 10}
                      y={ty + 36 + i * rowH}
                      fontSize={9.5}
                      fill="var(--ink-3)"
                      fontFamily="ui-sans-serif, system-ui, sans-serif"
                    >
                      {k}
                    </text>
                    <text
                      x={tx + 174}
                      y={ty + 36 + i * rowH}
                      textAnchor="end"
                      fontSize={9.5}
                      fill="var(--ink)"
                      fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                      fontWeight={500}
                    >
                      {v}
                    </text>
                  </g>
                ))}
              </g>
            );
          })()}

          {/* Today label */}
          <text
            x={x(0)}
            y={H - M.bottom + 16}
            textAnchor="middle"
            fontSize="10"
            fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
            fill="var(--ink-3)"
          >
            today
          </text>

          {/* Endpoint value labels (right edge) */}
          {(() => {
            const last = points[points.length - 1];
            return (
              <g>
                <text
                  x={x(last.dte) + 8}
                  y={y(last.up)}
                  dominantBaseline="central"
                  fontSize="10.5"
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                  fill="var(--up)"
                >
                  ${last.up.toFixed(2)}
                </text>
                <text
                  x={x(last.dte) + 8}
                  y={y(last.down)}
                  dominantBaseline="central"
                  fontSize="10.5"
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                  fill="var(--down)"
                >
                  ${last.down.toFixed(2)}
                </text>
              </g>
            );
          })()}
        </svg>
      </div>
      <div
        style={{
          display: 'flex',
          gap: 18,
          marginTop: 8,
          fontSize: 11,
          color: 'var(--ink-4)',
        }}
      >
        <span className="mono">
          <span style={{ color: 'var(--up)' }}>●</span> spot × (1 + EM)
        </span>
        <span className="mono">
          <span style={{ color: 'var(--down)' }}>●</span> spot × (1 − EM)
        </span>
      </div>
    </div>
  );
}

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

function signedPct(v: number, digits = 1) {
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`;
}

function pickNum(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}

function formatRevenue(v: number | null): string {
  if (v == null) return '—';
  const abs = Math.abs(v);
  if (abs >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

function formatEps(v: number | null): string {
  if (v == null) return '—';
  return `$${v.toFixed(2)}`;
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
function SurpriseStrip({
  history,
  hoveredIndex,
  onHover,
}: {
  history: HistoryPoint[];
  hoveredIndex: number | null;
  onHover: (i: number | null) => void;
}) {
  const hasEps = history.some((h) => h.epsSurprise != null);
  const hasRev = history.some((h) => h.revSurprise != null);
  if (!hasEps && !hasRev) return null;

  const rows: { label: string; key: keyof HistoryPoint }[] = [];
  if (hasEps) rows.push({ label: 'EPS', key: 'epsSurprise' });
  if (hasRev) rows.push({ label: 'Rev', key: 'revSurprise' });

  return (
    <div
      style={{
        marginTop: 20,
        paddingTop: 16,
        borderTop: '1px solid var(--line)',
      }}
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: `36px repeat(${history.length}, 1fr)`,
          rowGap: 6,
          alignItems: 'center',
        }}
      >
        {rows.map((row) => (
          <Fragment key={row.key}>
            <div
              style={{
                fontSize: 10,
                letterSpacing: '0.14em',
                textTransform: 'uppercase',
                color: 'var(--ink-3)',
                fontWeight: 500,
              }}
            >
              {row.label}
            </div>
            {history.map((h, i) => {
              const v = h[row.key] as number | null | undefined;
              const isHovered = hoveredIndex === i;
              const dim = hoveredIndex != null && !isHovered;
              if (v == null || !Number.isFinite(v)) {
                return (
                  <div
                    key={`${row.key}-${h.date}-${i}`}
                    onMouseEnter={() => onHover(i)}
                    onMouseLeave={() => onHover(null)}
                    className="mono tnum"
                    style={{
                      textAlign: 'center',
                      fontSize: 10.5,
                      color: 'var(--ink-4)',
                      opacity: dim ? 0.4 : 1,
                      cursor: 'default',
                    }}
                  >
                    —
                  </div>
                );
              }
              const beat = v >= 0;
              const tone = beat ? 'var(--up)' : 'var(--down)';
              return (
                <div
                  key={`${row.key}-${h.date}-${i}`}
                  onMouseEnter={() => onHover(i)}
                  onMouseLeave={() => onHover(null)}
                  className="mono tnum"
                  style={{
                    textAlign: 'center',
                    fontSize: 10.5,
                    color: tone,
                    fontWeight: 600,
                    opacity: dim ? 0.45 : 1,
                    cursor: 'default',
                    letterSpacing: '-0.01em',
                  }}
                >
                  {signedPct(v, 1)}
                </div>
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function HistoryChart({
  history,
  hoveredIndex,
  setHoveredIndex,
}: {
  history: HistoryPoint[];
  hoveredIndex: number | null;
  setHoveredIndex: (i: number | null) => void;
}) {
  const W = 640;
  const H = 220;
  const P = 40;
  const TOP = 22;        // top padding for max label / clearance above dots
  const BOT = 30;        // bottom padding for quarter labels
  if (history.length === 0) return null;

  const hasImplied = history.some((h) => h.implied != null);
  // hasEps no longer needed at chart level — the SurpriseStrip below
  // renders both EPS and revenue surprise explicitly. Keeping that
  // signal off the dots removes the hollow-circle encoding that users
  // found confusing.

  const maxAbs =
    Math.max(
      ...history.map((h) =>
        Math.max(h.implied != null ? h.implied : 0, Math.abs(h.actual)),
      ),
    ) * 1.18 || 0.05;
  const plotH = H - TOP - BOT;
  const midY = TOP + plotH / 2;
  const colW = (W - P * 2) / history.length;
  const y = (v: number) => midY - (v / maxAbs) * (plotH / 2);
  const hovered = hoveredIndex != null ? history[hoveredIndex] : null;

  // Pre-compute label positions so we can flip when a label would
  // collide with the row above/below (cleaner than per-dot fudging).
  const labelOffsetForAbove = -12;
  const labelOffsetForBelow = 18;

  return (
    <div style={{ marginTop: 18, position: 'relative' }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto' }}>
        {/* Subtle alternating column bands for readability — no dashed
            gridlines competing for attention. */}
        {history.map((_, i) =>
          i % 2 === 1 ? (
            <rect
              key={`band-${i}`}
              x={P + colW * i}
              y={TOP}
              width={colW}
              height={plotH}
              fill="var(--ink)"
              opacity={0.018}
            />
          ) : null,
        )}
        {/* Hover column highlight */}
        {hoveredIndex != null && (
          <rect
            x={P + colW * hoveredIndex}
            y={TOP}
            width={colW}
            height={plotH}
            fill="var(--ink)"
            opacity={0.04}
          />
        )}
        {/* Axis labels — only at zero and ±max for orientation. */}
        <text
          x={P - 10}
          y={y(maxAbs) + 3}
          textAnchor="end"
          fill="var(--ink-4)"
          fontSize="9.5"
          fontFamily="JetBrains Mono"
        >
          +{(maxAbs * 100).toFixed(0)}%
        </text>
        <text
          x={P - 10}
          y={midY + 3}
          textAnchor="end"
          fill="var(--ink-3)"
          fontSize="9.5"
          fontFamily="JetBrains Mono"
        >
          0
        </text>
        <text
          x={P - 10}
          y={y(-maxAbs) + 3}
          textAnchor="end"
          fill="var(--ink-4)"
          fontSize="9.5"
          fontFamily="JetBrains Mono"
        >
          −{(maxAbs * 100).toFixed(0)}%
        </text>
        {/* Zero baseline — single crisp line, no dashed clutter. */}
        <line
          x1={P}
          x2={W - P}
          y1={midY}
          y2={midY}
          stroke="var(--line-2)"
          strokeWidth={1}
        />
        {history.map((h, i) => {
          const cx = P + colW * i + colW / 2;
          const actualPositive = h.actual >= 0;
          const cy = y(h.actual);
          const moveColor = actualPositive ? 'var(--up)' : 'var(--down)';
          // Label flips to the OTHER side of zero so it sits in the
          // open vertical space rather than colliding with the next dot.
          const labelY = cy + (actualPositive ? labelOffsetForAbove : labelOffsetForBelow);
          const isHovered = hoveredIndex === i;
          const dim = hoveredIndex != null && !isHovered;
          return (
            <g
              key={`${h.date}-${i}`}
              onMouseEnter={() => setHoveredIndex(i)}
              onMouseLeave={() => setHoveredIndex(null)}
              onFocus={() => setHoveredIndex(i)}
              onBlur={() => setHoveredIndex(null)}
              tabIndex={0}
              role="listitem"
              aria-label={
                `${h.q} realized move ${signedPct(h.actual)}` +
                (h.implied != null
                  ? `, implied ±${(h.implied * 100).toFixed(1)}%`
                  : '')
              }
              style={{ cursor: 'default', outline: 'none', opacity: dim ? 0.45 : 1 }}
            >
              {/* Implied range band — only when data present. */}
              {h.implied != null && (
                <rect
                  x={cx - 14}
                  y={y(h.implied)}
                  width={28}
                  height={Math.max(1, y(-h.implied) - y(h.implied))}
                  fill="color-mix(in srgb, var(--accent) 18%, transparent)"
                  stroke="var(--accent)"
                  strokeWidth={0.8}
                />
              )}
              {/* Drop line from zero to dot — gives each point a body
                  and reinforces direction at a glance. */}
              <line
                x1={cx}
                x2={cx}
                y1={midY}
                y2={cy}
                stroke={moveColor}
                strokeWidth={isHovered ? 2 : 1.2}
                opacity={0.55}
              />
              {/* The dot. */}
              <circle
                cx={cx}
                cy={cy}
                r={isHovered ? 5 : 4}
                fill={moveColor}
              />
              {/* Numeric label. */}
              <text
                x={cx}
                y={labelY}
                textAnchor="middle"
                fill={moveColor}
                fontSize={isHovered ? '11' : '10'}
                fontFamily="JetBrains Mono"
                fontWeight={700}
              >
                {signedPct(h.actual)}
              </text>
              {/* Quarter label. */}
              <text
                x={cx}
                y={H - 10}
                textAnchor="middle"
                fill="var(--ink-3)"
                fontSize="10"
                fontFamily="JetBrains Mono"
                fontWeight={isHovered ? 600 : 400}
              >
                {h.q}
              </text>
            </g>
          );
        })}
      </svg>
      {hovered && (() => {
        const i = hoveredIndex ?? 0;
        const cx = P + colW * i + colW / 2;
        const cy = y(hovered.actual);
        const left = `clamp(110px, ${(cx / W) * 100}%, calc(100% - 110px))`;
        const top = `${(cy / H) * 100}%`;
        const beat =
          hovered.implied != null && Math.abs(hovered.actual) > hovered.implied;
        const hasEpsRow = hovered.epsActual != null || hovered.epsEstimate != null;
        const hasRevRow = hovered.revActual != null || hovered.revEstimate != null;
        return (
          <div
            style={{
              position: 'absolute',
              left,
              top,
              transform: cy < H / 2 ? 'translate(-50%, 18px)' : 'translate(-50%, calc(-100% - 18px))',
              minWidth: 210,
              padding: '9px 10px',
              border: '1px solid var(--line-2)',
              borderRadius: 6,
              background: 'var(--bg-3)',
              boxShadow: '0 14px 32px rgba(0,0,0,.38)',
              pointerEvents: 'none',
              zIndex: 2,
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: 12,
                marginBottom: 6,
                fontSize: 11,
                color: 'var(--ink-3)',
              }}
            >
              <span>{hovered.q}</span>
              <span>{shortDate(hovered.date)}</span>
            </div>
            <div
              className="mono tnum"
              style={{
                fontSize: 16,
                fontWeight: 700,
                color: hovered.actual >= 0 ? 'var(--up)' : 'var(--down)',
                lineHeight: 1,
              }}
            >
              {signedPct(hovered.actual)}
            </div>
            <div className="mono tnum" style={{ marginTop: 6, fontSize: 11, color: 'var(--ink-3)' }}>
              {hovered.implied != null
                ? `Implied ±${(hovered.implied * 100).toFixed(1)}%${beat ? ' · beat' : ''}`
                : 'Implied range unavailable'}
            </div>
            {(hasEpsRow || hasRevRow) && (
              <div
                style={{
                  marginTop: 8,
                  paddingTop: 8,
                  borderTop: '1px solid var(--line)',
                  display: 'grid',
                  gap: 4,
                }}
              >
                {hasEpsRow && (
                  <div
                    className="mono tnum"
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      gap: 8,
                      fontSize: 10.5,
                    }}
                  >
                    <span style={{ color: 'var(--ink-3)' }}>EPS</span>
                    <span style={{ color: 'var(--ink-2)' }}>
                      {formatEps(hovered.epsActual)} vs {formatEps(hovered.epsEstimate)}
                      {hovered.epsSurprise != null && (
                        <span
                          style={{
                            marginLeft: 6,
                            color: hovered.epsSurprise >= 0 ? 'var(--up)' : 'var(--down)',
                          }}
                        >
                          {signedPct(hovered.epsSurprise)}
                        </span>
                      )}
                    </span>
                  </div>
                )}
                {hasRevRow && (
                  <div
                    className="mono tnum"
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      gap: 8,
                      fontSize: 10.5,
                    }}
                  >
                    <span style={{ color: 'var(--ink-3)' }}>Rev</span>
                    <span style={{ color: 'var(--ink-2)' }}>
                      {formatRevenue(hovered.revActual)} vs {formatRevenue(hovered.revEstimate)}
                      {hovered.revSurprise != null && (
                        <span
                          style={{
                            marginLeft: 6,
                            color: hovered.revSurprise >= 0 ? 'var(--up)' : 'var(--down)',
                          }}
                        >
                          {signedPct(hovered.revSurprise)}
                        </span>
                      )}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })()}
      {/* Minimal legend — drops the obsolete EPS-miss / beat-implied
          chips since those encodings moved to the SurpriseStrip below
          the chart. */}
      <div
        style={{
          display: 'flex',
          gap: 18,
          marginTop: 10,
          fontSize: 11,
          color: 'var(--ink-3)',
          flexWrap: 'wrap',
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: 999, background: 'var(--up)' }} />
          Up
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: 999, background: 'var(--down)' }} />
          Down
        </span>
        {hasImplied && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 18,
                height: 8,
                background: 'color-mix(in srgb, var(--accent) 18%, transparent)',
                border: '1px solid var(--accent)',
              }}
            />
            Implied range
          </span>
        )}
      </div>
    </div>
  );
}

// Owns the shared hoveredIndex so HistoryChart + SurpriseStrip light up
// the same quarter together — hovering a column in either highlights
// the matching column in the other. Header summarizes implied-beat and
// EPS-beat rates so the eye can scan the headline before reading the
// chart. Layout: chart (hero, the realized moves are the primary
// signal) → SurpriseStrip (secondary, fundamentals context).
function HistoricalSection({
  series,
  hasImplied,
  beats,
  withImpliedLen,
  withEpsLen,
  epsBeats,
  cardChrome,
}: {
  series: HistoryPoint[];
  hasImplied: boolean;
  beats: number;
  withImpliedLen: number;
  withEpsLen: number;
  epsBeats: number;
  cardChrome?: boolean;
}) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const headline = hasImplied
    ? 'Implied vs. actual'
    : withEpsLen > 0
      ? 'Fundamentals vs. realized'
      : 'Realized moves';
  return (
    <section
      className={cardChrome ? 'qv-card' : undefined}
      style={
        cardChrome
          ? { padding: '22px 24px' }
          : { padding: '40px 0', borderBottom: '1px solid var(--line)' }
      }
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          marginBottom: 4,
          gap: 16,
        }}
      >
        <div>
          <div
            style={{
              fontSize: 10,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              color: 'var(--ink-3)',
            }}
          >
            Historical
          </div>
          <h2
            className="serif"
            style={{
              margin: '4px 0 0',
              fontSize: 22,
              fontWeight: 700,
              letterSpacing: '-0.01em',
            }}
          >
            {headline}, last {series.length}{' '}
            {series.length === 1 ? 'quarter' : 'quarters'}
          </h2>
        </div>
        <div
          className="mono tnum"
          style={{
            fontSize: 11,
            color: 'var(--ink-3)',
            textAlign: 'right',
            lineHeight: 1.55,
          }}
        >
          {hasImplied && (
            <div>
              Beat implied{' '}
              <span style={{ color: 'var(--ink)' }}>
                {beats}/{withImpliedLen}
              </span>{' '}
              times
            </div>
          )}
          {withEpsLen > 0 && (
            <div>
              Beat EPS{' '}
              <span style={{ color: 'var(--ink)' }}>
                {epsBeats}/{withEpsLen}
              </span>{' '}
              times
            </div>
          )}
          {!hasImplied && withEpsLen === 0 && (
            <div style={{ color: 'var(--ink-4)', maxWidth: 220 }}>
              Implied range pending —<br />historical option chains not yet wired
            </div>
          )}
        </div>
      </div>
      {/* Chart is the hero — surprise strip is secondary context. */}
      <HistoryChart
        history={series}
        hoveredIndex={hoveredIndex}
        setHoveredIndex={setHoveredIndex}
      />
      <SurpriseStrip
        history={series}
        hoveredIndex={hoveredIndex}
        onHover={setHoveredIndex}
      />
    </section>
  );
}

// ---------- Small bits ----------
function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div style={{ padding: '18px 0' }}>
      <div
        style={{
          fontSize: 10,
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          color: 'var(--ink-3)',
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      <div
        className="serif tnum"
        style={{ fontSize: 26, fontWeight: 700, lineHeight: 1, letterSpacing: '-0.02em' }}
      >
        {value}
      </div>
      {sub && (
        <div
          className="mono tnum"
          style={{ fontSize: 11, color: 'var(--ink-4)', marginTop: 6 }}
        >
          {sub}
        </div>
      )}
    </div>
  );
}

function Th({ children, align = 'left' }: { children: React.ReactNode; align?: 'left' | 'right' }) {
  return (
    <th
      style={{
        textAlign: align,
        padding: '10px 0',
        fontSize: 10,
        letterSpacing: '0.14em',
        textTransform: 'uppercase',
        color: 'var(--ink-3)',
        fontWeight: 500,
      }}
    >
      {children}
    </th>
  );
}
function Td({
  children,
  align = 'left',
  mono,
  bold,
  tone,
}: {
  children: React.ReactNode;
  align?: 'left' | 'right';
  mono?: boolean;
  bold?: boolean;
  tone?: string;
}) {
  return (
    <td
      className={mono ? 'mono tnum' : ''}
      style={{
        textAlign: align,
        padding: '14px 0',
        fontSize: 13,
        color: tone || 'var(--ink-2)',
        fontWeight: bold ? 500 : 400,
      }}
    >
      {children}
    </td>
  );
}

/** A scroll-triggered fade-up wrapper. The first render mounts the element
 *  as hidden (opacity 0, translated down); an IntersectionObserver flips
 *  the `in` class once it enters the viewport, animating into place via
 *  the global `.reveal` CSS transition. */
function Reveal({
  children,
  as = 'section',
  delay = 0,
  style,
  className,
}: {
  children: React.ReactNode;
  as?: 'section' | 'div';
  delay?: number;
  style?: React.CSSProperties;
  className?: string;
}) {
  const ref = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined') {
      el.classList.add('in');
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            el.classList.add('in');
            io.disconnect();
            break;
          }
        }
      },
      { rootMargin: '-40px 0px -40px 0px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  const cls = `reveal${className ? ' ' + className : ''}`;
  const mergedStyle: React.CSSProperties = delay ? { transitionDelay: `${delay}ms`, ...style } : style ?? {};
  if (as === 'div') {
    return (
      <div ref={ref as React.RefObject<HTMLDivElement>} className={cls} style={mergedStyle}>
        {children}
      </div>
    );
  }
  return (
    <section ref={ref as React.RefObject<HTMLElement>} className={cls} style={mergedStyle}>
      {children}
    </section>
  );
}

const DETAIL_LAYOUT_KEY = 'quantiv-detail-layout-v1';
type DetailLayout = 'cards' | 'editorial';

function useDetailLayout(): [DetailLayout, (next: DetailLayout) => void] {
  const [layout, setLayout] = useState<DetailLayout>('cards');
  useEffect(() => {
    try {
      const v = localStorage.getItem(DETAIL_LAYOUT_KEY);
      if (v === 'editorial' || v === 'cards') setLayout(v);
    } catch {}
  }, []);
  const update = useCallback((next: DetailLayout) => {
    setLayout(next);
    try { localStorage.setItem(DETAIL_LAYOUT_KEY, next); } catch {}
  }, []);
  return [layout, update];
}

// ---------- Page ----------
export default function SymbolPage() {
  // Triggers EDGAR ticker-names fetch + re-render so the header company
  // name resolves even when the symbol isn't in the S&P 500 or curated map.
  useEnsureCompanyNames();
  useEnsureListingExchanges();

  const params = useParams();
  const router = useRouter();
  const symbol = (params.symbol as string)?.toUpperCase();

  const [data, setData] = useState<SymbolDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState<LivePrice | null>(null);
  const [toast, setToast] = useState<{ msg: string; key: number } | null>(null);
  const [fanHoverExpiration, setFanHoverExpiration] = useState<string | null>(null);
  const [detailLayout, setDetailLayout] = useDetailLayout();
  const isCards = detailLayout === 'cards';

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`/symbols/${symbol}.json`, { cache: 'no-store' });
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
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    setLive(null);
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let lastQuoteRefreshActive = true;
    const fetchOnce = async (): Promise<number> => {
      try {
        const res = await fetch(`/api/stocks/batch-price?symbols=${symbol}`, { cache: 'no-store' });
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

  const showToast = useCallback((msg: string) => {
    setToast({ msg, key: Date.now() });
  }, []);

  if (loading) {
    return (
      <div style={{ maxWidth: 960, margin: '0 auto', padding: '80px 28px', textAlign: 'center' }}>
        <div style={{ color: 'var(--ink-3)', fontSize: 13 }}>Loading {symbol}…</div>
      </div>
    );
  }

  if (error || !data) {
    // Limited view for tickers that exist (search hit, valid ticker)
    // but have no pre-built /symbols/SYM.json (e.g. not in the options
    // universe yet, or in flight to be added). We still know the
    // company name (sp500 lookup) and can usually show a live quote
    // via the batch-price API.
    const tick = live;
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
            else router.push('/');
          }}
          className="chip"
          style={{ border: 'none', color: 'var(--ink-3)', paddingLeft: 0, cursor: 'pointer' }}
        >
          <ChevronLeft size={14} /> Return
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
          <Logo ticker={symbol} size={60} />
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
  const livePrice = live?.price ?? null;
  const spot = livePrice ?? data.spot_price ?? 0;
  const change = live?.change ?? 0;
  const changePct = live?.changePct ?? 0;
  // Flat = literally no move at display precision. Don't paint a green/red
  // arrow when the underlying value is just float noise around zero.
  const flat = Math.round(change * 100) / 100 === 0
    && Math.round(changePct * 10000) / 10000 === 0;
  const up = !flat && change >= 0;
  const moveArrow = flat ? '–' : up ? '▲' : '▼';
  const moveColor = flat ? 'var(--ink-4)' : up ? 'var(--up)' : 'var(--down)';

  const straddlePct = em?.straddle_pct ?? 0;
  const ivPct = em?.iv_pct ?? straddlePct;
  const atmIV = em?.atm_iv ?? 0.3;
  const dte = em?.dte ?? 28;
  const lower = spot * (1 - straddlePct);
  const upper = spot * (1 + straddlePct);

  const timingLabel = timingText(em?.timing ?? data.next_earnings_timing);

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '0 28px 60px' }}>
      {toast && <Toast key={toast.key} message={toast.msg} onDone={() => setToast(null)} />}

      <div style={{ paddingTop: 24 }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <button
            onClick={() => {
              if (window.history.length > 1) router.back();
              else router.push('/');
            }}
            className="chip"
            style={{ border: 'none', color: 'var(--ink-3)', paddingLeft: 0, cursor: 'pointer' }}
          >
            <ChevronLeft size={14} /> Return
          </button>
          <div
            role="group"
            aria-label="Detail layout"
            style={{
              display: 'inline-flex',
              border: '1px solid var(--line)',
              borderRadius: 999,
              overflow: 'hidden',
              fontSize: 11,
            }}
          >
            {([
              ['cards', 'Cards'],
              ['editorial', 'Editorial'],
            ] as const).map(([key, label]) => {
              const active = detailLayout === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setDetailLayout(key)}
                  style={{
                    padding: '6px 14px',
                    background: active ? 'var(--ink)' : 'transparent',
                    color: active ? 'var(--bg)' : 'var(--ink-3)',
                    border: 'none',
                    cursor: 'pointer',
                    letterSpacing: '0.04em',
                    fontWeight: active ? 600 : 500,
                  }}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>

        <div
          style={{
            marginTop: 18,
            paddingBottom: 24,
            borderBottom: '1px solid var(--line)',
            display: 'grid',
            gridTemplateColumns: 'auto 1fr auto',
            gap: 24,
            alignItems: 'flex-start',
          }}
        >
          <Logo ticker={symbol} size={60} />
          <div>
            <div
              style={{
                fontSize: 11,
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                color: 'var(--ink-3)',
              }}
            >
              {companyName(symbol)}
            </div>
            <h1
              className="serif"
              style={{
                margin: '4px 0 0',
                fontSize: 52,
                fontWeight: 800,
                letterSpacing: '-0.035em',
                lineHeight: 0.95,
                color: 'var(--ink)',
                textTransform: 'uppercase',
                display: 'flex',
                alignItems: 'center',
                gap: 14,
              }}
            >
              {symbol}
              <SignedIn>
                <WatchlistButton ticker={symbol} onToast={showToast} />
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
                      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.6" />
                      <path
                        d="M12 8v8M8 12h8"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                      />
                    </svg>
                  </button>
                </SignInButton>
              </SignedOut>
            </h1>
            <div
              style={{
                marginTop: 10,
                display: 'flex',
                alignItems: 'baseline',
                gap: 14,
                flexWrap: 'wrap',
              }}
            >
              <span className="serif tnum" style={{ fontSize: 28, fontWeight: 700 }}>
                ${spot.toFixed(2)}
              </span>
              {live && live.change !== null && (
                <span
                  className="mono tnum"
                  style={{ fontSize: 13, color: moveColor }}
                >
                  {moveArrow} {Math.abs(change).toFixed(2)} (
                  {(Math.abs(changePct) * 100).toFixed(2)}%)
                </span>
              )}
              <span
                style={{
                  fontSize: 10.5,
                  color: 'var(--ink-4)',
                  letterSpacing: '0.1em',
                  textTransform: 'uppercase',
                }}
              >
                {quoteSourceLabel(live, symbol, data.as_of_date)}
              </span>
            </div>
          </div>

          {em?.earnings_date && (() => {
            const daysOut = daysFromToday(em.earnings_date);
            const past = daysOut !== null && daysOut < 0;
            const magnitude = daysOut === null ? null : Math.abs(daysOut);
            return (
              <div style={{ textAlign: 'right' }}>
                <div
                  style={{
                    fontSize: 11,
                    letterSpacing: '0.14em',
                    textTransform: 'uppercase',
                    color: 'var(--ink-3)',
                  }}
                >
                  {past ? 'Reported' : 'Reports in'}
                </div>
                <div
                  className="serif tnum"
                  style={{
                    fontSize: 38,
                    fontWeight: 800,
                    lineHeight: 1,
                    marginTop: 4,
                    letterSpacing: '-0.03em',
                  }}
                >
                  {magnitude ?? '—'}
                  <span
                    style={{
                      fontSize: 13,
                      color: 'var(--ink-3)',
                      marginLeft: 4,
                      fontWeight: 500,
                    }}
                  >
                    {past ? (magnitude === 1 ? 'day ago' : 'days ago') : 'days'}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 6 }}>
                  {shortDate(em.earnings_date)}
                  {timingLabel ? ` · ${timingLabel}` : ''}
                </div>
              </div>
            );
          })()}
        </div>
      </div>

      {/* EM hero */}
      {em && (
        <Reveal
          style={isCards
            ? { margin: '28px 0 0' }
            : { padding: '32px 0 16px', borderBottom: '1px solid var(--line)' }}
        >
          <div
            className={isCards ? 'qv-card-hi' : undefined}
            style={isCards ? { padding: '26px 28px' } : undefined}
          >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'flex-start',
              gap: 24,
            }}
          >
            <div>
              <div
                className={isCards ? 'qv-pill' : undefined}
                style={
                  isCards
                    ? undefined
                    : {
                        fontSize: 10,
                        letterSpacing: '0.18em',
                        textTransform: 'uppercase',
                        color: 'var(--ink-3)',
                      }
                }
              >
                Expected move · {em.earnings_date ? 'Earnings' : 'Nearest expiry'}
              </div>
              <h2
                className="serif"
                style={{
                  margin: '4px 0 0',
                  fontSize: 26,
                  fontWeight: 700,
                  letterSpacing: '-0.015em',
                }}
              >
                Options are pricing a move of
              </h2>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div
                className="serif tnum"
                style={{
                  fontSize: 84,
                  fontWeight: 800,
                  lineHeight: 0.9,
                  letterSpacing: '-0.04em',
                }}
              >
                ±{(straddlePct * 100).toFixed(1)}
                <span style={{ fontSize: 32, color: 'var(--ink-3)', fontWeight: 600 }}>%</span>
              </div>
              <div
                className="mono tnum"
                style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 6 }}
              >
                ${lower.toFixed(2)} – ${upper.toFixed(2)} · via ATM straddle
              </div>
            </div>
          </div>

          {spot > 0 && straddlePct > 0 && (
            <InteractiveBar
              spot={spot}
              em={straddlePct}
              emIV={ivPct}
              atmIV={atmIV}
              dte={dte}
            />
          )}
          </div>
        </Reveal>
      )}

      {/* ML forecast card */}
      {em?.em_ml_pct != null && (
        <Reveal
          delay={60}
          style={isCards
            ? { margin: '14px 0 0' }
            : { padding: '24px 0', borderBottom: '1px solid var(--line)' }}
        >
          <div className={isCards ? 'qv-card' : undefined}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'flex-start',
              gap: 24,
              marginBottom: 14,
            }}
          >
            <div>
              <div
                className={isCards ? 'qv-pill' : undefined}
                style={
                  isCards
                    ? undefined
                    : {
                        fontSize: 10,
                        letterSpacing: '0.18em',
                        textTransform: 'uppercase',
                        color: 'var(--accent)',
                      }
                }
              >
                LightGBM forecast
                {em.model_horizon != null ? ` · T-${em.model_horizon}` : ''}
              </div>
              <div
                className="serif"
                style={{
                  margin: '4px 0 0',
                  fontSize: 18,
                  fontWeight: 600,
                  color: 'var(--ink-2)',
                  letterSpacing: '-0.01em',
                }}
              >
                Model predicts{' '}
                <span className="tnum" style={{ color: 'var(--ink)' }}>
                  ±{(em.em_ml_pct * 100).toFixed(2)}%
                </span>
                {em.correction_factor != null && (
                  <span
                    className="mono tnum"
                    style={{
                      marginLeft: 10,
                      fontSize: 12,
                      color:
                        em.correction_factor < 0.9
                          ? 'var(--up)'
                          : em.correction_factor > 1.1
                            ? 'var(--down)'
                            : 'var(--ink-3)',
                    }}
                  >
                    {em.correction_factor < 1
                      ? `straddle overstates by ${((1 - em.correction_factor) * 100).toFixed(0)}%`
                      : `straddle understates by ${((em.correction_factor - 1) * 100).toFixed(0)}%`}
                  </span>
                )}
              </div>
            </div>
            <div
              className="mono tnum"
              style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'right' }}
            >
              Straddle ±{(straddlePct * 100).toFixed(2)}%
              {em.p10 != null && em.p90 != null && (
                <div style={{ marginTop: 2 }}>
                  80% band: ±{(em.p10 * 100).toFixed(2)}% – ±{(em.p90 * 100).toFixed(2)}%
                </div>
              )}
            </div>
          </div>

          {em.p10 != null && em.p90 != null && em.p50 != null && (
            <MLBand
              p10={em.p10}
              p25={em.p25 ?? em.p10}
              p50={em.p50}
              p75={em.p75 ?? em.p90}
              p90={em.p90}
              straddle={straddlePct}
            />
          )}
          </div>
        </Reveal>
      )}

      {/* Stat row */}
      {em && (
        <Reveal
          delay={120}
          style={isCards ? { margin: '14px 0 0' } : undefined}
        >
          <div
            className={isCards ? 'qv-card' : undefined}
            style={{
              display: 'grid',
              gridTemplateColumns: `repeat(${data.vol_regime?.iv_rank != null ? 5 : 4}, 1fr)`,
              ...(isCards
                ? { columnGap: 24, padding: '6px 20px' }
                : { borderBottom: '1px solid var(--line)', columnGap: 24 }),
            }}
          >
          <Stat
            label="ATM IV"
            value={em.atm_iv !== null ? `${(em.atm_iv * 100).toFixed(1)}%` : '—'}
            sub={
              em.term_slope !== null && em.term_slope !== undefined
                ? `Δ term slope ${(em.term_slope * 100).toFixed(1)} vol/30d`
                : undefined
            }
          />
          {data.vol_regime?.iv_rank != null && (
            <Stat
              label="IV Rank"
              value={`${Math.round(data.vol_regime.iv_rank * 100)}%`}
              sub={
                data.vol_regime.iv_year_low != null && data.vol_regime.iv_year_high != null
                  ? `52w ${(data.vol_regime.iv_year_low * 100).toFixed(0)}–${(data.vol_regime.iv_year_high * 100).toFixed(0)}%`
                  : undefined
              }
            />
          )}
          <Stat
            label="IV-based EM"
            value={em.iv_pct !== null ? `±${(em.iv_pct * 100).toFixed(1)}%` : '—'}
            sub={
              em.atm_iv !== null
                ? `σ ${(em.atm_iv * 100).toFixed(0)}% · ${em.dte}d`
                : undefined
            }
          />
          <Stat
            label="ATM straddle"
            value={em.straddle_abs !== null ? `$${em.straddle_abs.toFixed(2)}` : '—'}
            sub={`Strike $${em.atm_strike.toFixed(2)}`}
          />
          <Stat
            label="Skew (ATM)"
            value={
              em.skew_atm !== null && em.skew_atm !== undefined
                ? `${(em.skew_atm * 100).toFixed(2)}v`
                : '—'
            }
            sub={
              em.total_vega !== null && em.total_vega !== undefined
                ? `Vega $${(em.total_vega * 1000).toFixed(0)}`
                : undefined
            }
          />
          </div>
        </Reveal>
      )}

      {/* Historical implied vs realized (last 8 quarters).
          Realized close-to-close moves come from v_ohlcv bracketed by
          Finnhub-grade earnings timing. EPS / revenue surprise come
          from the Finnhub overlay and may be null on older rows that
          predate the overlay's rolling window; the chart degrades
          gracefully (no strip, no surprise styling). The `implied`
          band remains pending — it requires historical option chains
          captured at T-N, which is a separate data ingest. */}
      {(() => {
        const series = buildHistorySeries(data.earnings_history);
        if (series.length < 2) return null;
        const withImplied = series.filter((h) => h.implied != null);
        const hasImplied = withImplied.length > 0;
        const beats = withImplied.filter(
          (h) => Math.abs(h.actual) > (h.implied as number),
        ).length;
        const withEps = series.filter((h) => h.epsSurprise != null);
        const epsBeats = withEps.filter((h) => (h.epsSurprise as number) >= 0).length;
        return (
          <Reveal
            delay={140}
            style={isCards ? { margin: '14px 0 0' } : undefined}
          >
            <HistoricalSection
              series={series}
              hasImplied={hasImplied}
              beats={beats}
              withImpliedLen={withImplied.length}
              withEpsLen={withEps.length}
              epsBeats={epsBeats}
              cardChrome={isCards}
            />
          </Reveal>
        );
      })()}

      {/* Term structure */}
      {data.straddle_features.length > 0 && (
        <Reveal
          delay={180}
          style={isCards
            ? { margin: '14px 0 0' }
            : { padding: '40px 0', borderBottom: '1px solid var(--line)' }}
        >
          <div
            className={isCards ? 'qv-card' : undefined}
            style={isCards ? { padding: '22px 24px' } : undefined}
          >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              marginBottom: 14,
            }}
          >
            <div>
              <div
                className={isCards ? 'qv-pill' : undefined}
                style={
                  isCards
                    ? undefined
                    : {
                        fontSize: 10,
                        letterSpacing: '0.18em',
                        textTransform: 'uppercase',
                        color: 'var(--ink-3)',
                      }
                }
              >
                Term structure
              </div>
              <h2
                className="serif"
                style={{
                  margin: '2px 0 0',
                  fontSize: 22,
                  fontWeight: 700,
                  letterSpacing: '-0.01em',
                }}
              >
                Expected move by expiry
              </h2>
            </div>
            <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>
              {data.straddle_features.length} expiries
            </span>
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--line)' }}>
                <Th>Expiry</Th>
                <Th align="right">DTE</Th>
                <Th align="right">ATM IV</Th>
                <Th align="right">Straddle</Th>
                <Th align="right">EM · math</Th>
                <Th align="right">EM · IV</Th>
              </tr>
            </thead>
            <tbody>
              {data.straddle_features.map((s) => (
                <tr
                  key={s.expiration}
                  style={{
                    borderBottom: '1px solid var(--line)',
                    background:
                      fanHoverExpiration === s.expiration ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : undefined,
                    transition: 'background 120ms ease',
                  }}
                  onMouseEnter={() => setFanHoverExpiration(s.expiration)}
                  onMouseLeave={() => setFanHoverExpiration(null)}
                >
                  <Td>
                    <span className="serif" style={{ fontSize: 15 }}>
                      {shortDate(s.expiration)}
                    </span>
                  </Td>
                  <Td align="right" mono>
                    {s.dte}d
                  </Td>
                  <Td align="right" mono>
                    {s.atm_iv !== null ? `${(s.atm_iv * 100).toFixed(2)}%` : '—'}
                  </Td>
                  <Td align="right" mono>
                    {s.straddle_mid !== null ? `$${s.straddle_mid.toFixed(2)}` : '—'}
                  </Td>
                  <Td align="right" mono bold tone="var(--ink)">
                    {s.em_straddle_pct !== null
                      ? `±${(s.em_straddle_pct * 100).toFixed(2)}%`
                      : '—'}
                  </Td>
                  <Td align="right" mono tone="var(--accent)">
                    {s.em_iv_pct !== null ? `±${(s.em_iv_pct * 100).toFixed(2)}%` : '—'}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>

          {spot > 0 && (
            <TermStructureFan
              asOf={data.as_of_date}
              spot={spot}
              expiries={data.straddle_features}
              highlightExpiration={fanHoverExpiration}
              onHighlightExpiration={setFanHoverExpiration}
            />
          )}
          </div>
        </Reveal>
      )}

      <div
        style={{
          padding: '20px 0 0',
          fontSize: 11,
          color: 'var(--ink-4)',
          display: 'flex',
          justifyContent: 'space-between',
        }}
      >
        <span>Options data · as of {data.as_of_date}</span>
        <span>
          Method: {em?.em_method === 'ml_lightgbm'
            ? 'ML forecast'
            : em?.em_method === 'ensemble'
              ? 'Math + ML'
              : 'options math baseline'}
        </span>
      </div>
    </div>
  );
}
