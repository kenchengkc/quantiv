'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ChevronLeft } from 'lucide-react';
import { SignedIn, SignedOut, SignInButton } from '@clerk/nextjs';
import { COMPANY_NAMES } from '@/lib/companyNames';
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

interface SymbolDetail {
  symbol: string;
  as_of_date: string;
  spot_price: number | null;
  expected_move?: ExpectedMove;
  straddle_features: Straddle[];
  earnings_history?: Array<{ date: string; timing: string }>;
  next_earnings?: string | null;
  next_earnings_timing?: string;
}

interface LivePrice {
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
  updated: string | null;
  source: 'finnhub' | 'unavailable';
}

function logoUrl(t: string) {
  return `https://assets.parqet.com/logos/symbol/${t}?format=png`;
}
function companyName(t: string) {
  return COMPANY_NAMES[t] || t;
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

  const probBeyond = useCallback(
    (pct: number) => {
      const z = Math.log(1 + Math.abs(pct)) / sigma;
      return 2 * (1 - normCDF(z));
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

  const density = useMemo(() => {
    const n = 80;
    const arr: { x: number; y: number }[] = [];
    for (let i = 0; i <= n; i++) {
      const x = i / n;
      const pct = minPct + x * (maxPct - minPct);
      const z = Math.log(1 + pct) / sigma;
      const pdf = Math.exp(-0.5 * z * z) / Math.sqrt(2 * Math.PI) / sigma;
      arr.push({ x: x * 100, y: pdf });
    }
    const maxP = Math.max(...arr.map((p) => p.y));
    return arr.map((p) => ({ x: p.x, y: p.y / maxP }));
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

// ---------- Page ----------
export default function SymbolPage() {
  const params = useParams();
  const router = useRouter();
  const symbol = (params.symbol as string)?.toUpperCase();

  const [data, setData] = useState<SymbolDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState<LivePrice | null>(null);
  const [toast, setToast] = useState<{ msg: string; key: number } | null>(null);

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
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/stocks/batch-price?symbols=${symbol}`, { cache: 'no-store' });
        if (!res.ok) return;
        const json = (await res.json()) as {
          updated: string | null;
          source: 'finnhub' | 'unavailable';
          data: Array<{
            symbol: string;
            price: number | null;
            previousClose: number | null;
            change: number | null;
            changePct: number | null;
          }>;
        };
        const tick = json.data?.[0];
        if (!cancelled && tick) {
          setLive({
            price: tick.price,
            previousClose: tick.previousClose,
            change: tick.change,
            changePct: tick.changePct,
            updated: json.updated,
            source: json.source === 'finnhub' ? 'finnhub' : 'unavailable',
          });
        }
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
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
          <ChevronLeft size={14} /> Earnings calendar
        </button>
        <div
          style={{
            marginTop: 24,
            padding: 20,
            border: '1px solid var(--down)',
            borderRadius: 12,
            color: 'var(--down)',
            fontSize: 13,
          }}
        >
          No data for {symbol}. {error ?? ''}
        </div>
      </div>
    );
  }

  const em = data.expected_move;
  const livePrice = live?.price ?? null;
  const spot = livePrice ?? data.spot_price ?? 0;
  const change = live?.change ?? 0;
  const changePct = live?.changePct ?? 0;
  const up = change >= 0;

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
        <button
          onClick={() => {
            if (window.history.length > 1) router.back();
            else router.push('/');
          }}
          className="chip"
          style={{ border: 'none', color: 'var(--ink-3)', paddingLeft: 0, cursor: 'pointer' }}
        >
          <ChevronLeft size={14} /> Earnings calendar
        </button>

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
                  style={{ fontSize: 13, color: up ? 'var(--up)' : 'var(--down)' }}
                >
                  {up ? '▲' : '▼'} {Math.abs(change).toFixed(2)} (
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
                {live?.source === 'finnhub'
                  ? 'Live · Finnhub'
                  : `As of ${data.as_of_date}`}
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
        <section style={{ padding: '32px 0 16px', borderBottom: '1px solid var(--line)' }}>
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
                style={{
                  fontSize: 10,
                  letterSpacing: '0.18em',
                  textTransform: 'uppercase',
                  color: 'var(--ink-3)',
                }}
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
        </section>
      )}

      {/* ML forecast card */}
      {em?.em_ml_pct != null && (
        <section
          style={{
            padding: '24px 0',
            borderBottom: '1px solid var(--line)',
          }}
        >
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
                style={{
                  fontSize: 10,
                  letterSpacing: '0.18em',
                  textTransform: 'uppercase',
                  color: 'var(--accent)',
                }}
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
        </section>
      )}

      {/* Stat row */}
      {em && (
        <section
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            borderBottom: '1px solid var(--line)',
            columnGap: 24,
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
        </section>
      )}

      {/* Term structure */}
      {data.straddle_features.length > 0 && (
        <section style={{ padding: '40px 0', borderBottom: '1px solid var(--line)' }}>
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
                style={{
                  fontSize: 10,
                  letterSpacing: '0.18em',
                  textTransform: 'uppercase',
                  color: 'var(--ink-3)',
                }}
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
                  style={{ borderBottom: '1px solid var(--line)' }}
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
        </section>
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
