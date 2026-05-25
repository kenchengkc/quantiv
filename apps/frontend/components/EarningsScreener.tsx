'use client';

import Link from 'next/link';
import { forwardRef, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Info } from 'lucide-react';
import { TableVirtuoso, type TableComponents } from 'react-virtuoso';
import { companyName } from '@/lib/companyNames';
import { useEnsureCompanyNames } from '@/lib/useCompanyNames';
import { useTickerHover } from '@/components/TickerHoverCard';
import {
  hasTickerLogoState,
  preloadTickerLogos,
  TickerLogo,
} from '@/components/TickerLogo';
import sp500Constituents from '../../../lib/data/sp500-constituents.json';

const SP500_SET = new Set(
  (sp500Constituents as { symbol: string }[]).map((c) => c.symbol),
);

/** One row from weeks/*.json — aligned with tools/build_frontend_data week events */
export interface ScreenerEvent {
  ticker: string;
  earnings_date: string;
  timing: string;
  fiscal_q?: string;
  spot_price?: number | null;
  atm_iv?: number | null;
  em_straddle_pct?: number | null;
  em_iv_pct?: number | null;
  em_ml_pct?: number | null;
  em_straddle_abs?: number | null;
  em_method?: string;
  lead_time_days?: number | null;
  days_to_expiry?: number | null;
  skew_atm?: number | null;
  term_slope?: number | null;
  p10?: number | null;
  p90?: number | null;
  correction_factor?: number | null;
  // Screener-only extras emitted by tools/build_frontend_data.screener_extras
  iv_rank?: number | null;            // 0..1, percentile of current IV in trailing year
  hist_move_avg_4q?: number | null;   // |close pre→post| avg over last 4 quarters
  iv_crush_pct?: number | null;       // (front − back) / front
}

interface WeekPayload {
  metadata?: { as_of_date?: string };
  window?: { start: string; end: string };
  events: ScreenerEvent[];
}

interface Manifest {
  as_of_date?: string;
  weeks: { start: string; end: string; offset: number; count: number }[];
}

/** Built by tools/build_frontend_data.py — preferred single-fetch path */
interface ScreenerBundle {
  metadata?: { as_of_date?: string; event_count?: number };
  events: ScreenerEvent[];
}

type SortKey =
  | 'edge'
  | 'dte'
  | 'date'
  | 'straddle'
  | 'ml'
  | 'iv'
  | 'band'
  | 'skew'
  | 'spot'
  | 'iv_rank'
  | 'hist_avg'
  | 'hist_edge'
  | 'iv_crush';
type SortDir = 'asc' | 'desc';
type TimingFilter = 'all' | 'bmo' | 'amc';

type LiveTick = { price: number | null; change: number | null; changePct: number | null };


function timingBucket(t?: string): 'bmo' | 'amc' | 'dmh' | 'unknown' {
  const k = (t || '').toLowerCase();
  if (k.includes('before') || k === 'bmo') return 'bmo';
  if (k.includes('after') || k === 'amc') return 'amc';
  if (k.includes('during') || k === 'dmh') return 'dmh';
  return 'unknown';
}

function timingShort(t?: string) {
  const b = timingBucket(t);
  if (b === 'bmo') return 'BMO';
  if (b === 'amc') return 'AMC';
  if (b === 'dmh') return 'DMH';
  return '—';
}

function pct1(v: number | null | undefined) {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

function ivPct(iv: number | null | undefined) {
  if (iv == null || !Number.isFinite(iv)) return '—';
  return `${(iv * 100).toFixed(1)}%`;
}

function num(v: number | null | undefined, digits = 2) {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(digits);
}

// ── Loading-gate timings ─────────────────────────────────────────────────
// MIN_LOADING_MS prevents the skeleton from flashing on a fast cache hit.
// INITIAL_QUOTE_WAIT_MS bounds how long we wait for the live-quote batch
// before unblocking the row render (rows show bundle spot + "—" 1d % if
// the quote API is slow). LOGO_PRELOAD_TIMEOUT_MS keeps a single
// failed/slow logo from holding up the whole page.
const MIN_LOADING_MS = 600;
const INITIAL_QUOTE_WAIT_MS = 3_200;
const LOGO_PRELOAD_TIMEOUT_MS = 4_000;
const SCREENER_NAME_COL_WIDTH = 280;

/** Name cell for a screener row. Owns its own hover handlers so the
 *  shared ticker hover-card only fires when the cursor is over the
 *  logo / ticker / company-name area, not the entire row. */
function NameCell({ ev }: { ev: ScreenerEvent }) {
  const hover = useTickerHover(ev.ticker);
  return (
    <td
      className="qv-m-sticky-cell"
      style={{
        padding: '16px 14px',
        width: SCREENER_NAME_COL_WIDTH,
        minWidth: SCREENER_NAME_COL_WIDTH,
        maxWidth: SCREENER_NAME_COL_WIDTH,
      }}
      {...hover}
    >
      <Link
        href={`/${ev.ticker}`}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          width: '100%',
          minWidth: 0,
          overflow: 'hidden',
          textDecoration: 'none',
          color: 'var(--ink)',
        }}
      >
        <TickerLogo ticker={ev.ticker} size={32} radius={6} loading="eager" />
        <div style={{ minWidth: 0, flex: '1 1 auto', overflow: 'hidden' }}>
          {/* Ticker + ML pill inline. ML rows ride on a small brand-blue
              pill next to the symbol so it reads as a one-line label
              rather than a third row below the company name. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span
              className="serif"
              style={{
                fontWeight: 800,
                color: 'var(--ink)',
                fontSize: 15.5,
                letterSpacing: '-0.01em',
                textTransform: 'uppercase',
              }}
            >
              {ev.ticker}
            </span>
            {ev.em_method === 'ml_lightgbm' && (
              <span
                style={{
                  fontSize: 8.5,
                  letterSpacing: '0.14em',
                  textTransform: 'uppercase',
                  padding: '2px 6px',
                  borderRadius: 999,
                  fontWeight: 700,
                  background:
                    'color-mix(in oklab, var(--brand-blue-1) 18%, transparent)',
                  color: 'var(--brand-blue-1)',
                }}
                title="LightGBM ensemble forecast available for this name"
              >
                ML
              </span>
            )}
          </div>
          <div
            title={companyName(ev.ticker)}
            style={{
              fontSize: 11.5,
              color: 'var(--ink-3)',
              marginTop: 2,
              maxWidth: '100%',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {companyName(ev.ticker)}
          </div>
        </div>
      </Link>
    </td>
  );
}

/** Sortable column header with a delayed (~600ms) hover tooltip that
 *  anchors to the left edge of the header so it extends to the right.
 *  The tooltip itself shows the column name in brand blue with a
 *  one-line plain-English description below. */
function SortHeader({
  active,
  dir,
  label,
  hint,
  width,
  onClick,
}: {
  active: boolean;
  dir: SortDir;
  label: string;
  hint?: string;
  width?: number;
  onClick: () => void;
}) {
  const [show, setShow] = useState(false);
  const timerRef = useRef<number | null>(null);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };
  const onEnter = () => {
    clearTimer();
    if (!hint) return;
    timerRef.current = window.setTimeout(() => setShow(true), 600);
  };
  const onLeave = () => {
    clearTimer();
    setShow(false);
  };
  useEffect(() => () => clearTimer(), []);

  return (
    <th
      style={{
        textAlign: 'right',
        padding: '14px 12px',
        whiteSpace: 'nowrap',
        position: 'relative',
        ...(width ? { width, minWidth: width } : {}),
      }}
    >
      <button
        type="button"
        onClick={onClick}
        onMouseEnter={onEnter}
        onMouseLeave={onLeave}
        onFocus={onEnter}
        onBlur={onLeave}
        className="mono"
        // Same fontWeight in both states so the column header doesn't
        // widen when you sort-toggle it, which would shift every column
        // to its right by a pixel or two.
        style={{
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          fontSize: 10.5,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color: active ? 'var(--ink)' : 'var(--ink-3)',
          fontWeight: 700,
          padding: 0,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 5,
        }}
      >
        {label}
        <span
          style={{
            // Inactive arrows used to sit at opacity 0.25 + 9px, which made
            // them nearly invisible against the column kicker and hid the
            // fact that columns were sortable. Bumping opacity + size so
            // the affordance reads from a normal viewing distance without
            // shouting; active sort still pops via full opacity.
            opacity: active ? 1 : 0.6,
            fontSize: 10,
            color: active ? 'var(--ink)' : 'var(--ink-2)',
          }}
        >
          {active ? (dir === 'desc' ? '▼' : '▲') : '▼'}
        </span>
      </button>
      {show && hint && (
        <div
          style={{
            position: 'absolute',
            left: 8,
            top: 'calc(100% + 6px)',
            zIndex: 50,
            padding: '8px 12px',
            background: 'linear-gradient(180deg, var(--bg-3), var(--bg-2))',
            border: '1px solid var(--line-2)',
            borderRadius: 8,
            boxShadow: '0 12px 32px rgba(0,0,0,0.55)',
            maxWidth: 260,
            minWidth: 180,
            fontSize: 11.5,
            lineHeight: 1.4,
            color: 'var(--ink-2)',
            textAlign: 'left',
            letterSpacing: 0,
            textTransform: 'none',
            whiteSpace: 'normal',
            fontWeight: 400,
            animation: 'qv-hover-pop 160ms cubic-bezier(.2,.8,.3,1) both',
            pointerEvents: 'none',
          }}
        >
          <div
            style={{
              fontSize: 9.5,
              letterSpacing: '0.16em',
              textTransform: 'uppercase',
              color: 'var(--brand-blue-1)',
              fontWeight: 700,
              marginBottom: 4,
            }}
          >
            {label}
          </div>
          {hint}
        </div>
      )}
    </th>
  );
}

/** Inline filter label paired with a delayed (~600ms) hover tooltip,
 *  mirroring the SortHeader hint card so filter chips/inputs (Min spot,
 *  etc.) can carry the same plain-English explanation surface. The
 *  label itself shows a subtle dotted underline + Info icon so users
 *  know there is help available without forcing them to hunt for it. */
function FilterHint({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children?: React.ReactNode;
}) {
  const [show, setShow] = useState(false);
  const timerRef = useRef<number | null>(null);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };
  const onEnter = () => {
    clearTimer();
    timerRef.current = window.setTimeout(() => setShow(true), 600);
  };
  const onLeave = () => {
    clearTimer();
    setShow(false);
  };
  useEffect(() => () => clearTimer(), []);

  return (
    <span
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        position: 'relative',
        cursor: 'help',
      }}
    >
      <span
        style={{
          textDecoration: 'underline dotted',
          textDecorationColor: 'var(--ink-4)',
          textUnderlineOffset: 3,
        }}
      >
        {children ?? label}
      </span>
      <Info
        size={11}
        aria-hidden
        style={{ color: 'var(--ink-4)', flexShrink: 0 }}
      />
      {show && (
        <div
          role="tooltip"
          style={{
            position: 'absolute',
            left: 0,
            top: 'calc(100% + 6px)',
            zIndex: 50,
            padding: '8px 12px',
            background: 'linear-gradient(180deg, var(--bg-3), var(--bg-2))',
            border: '1px solid var(--line-2)',
            borderRadius: 8,
            boxShadow: '0 12px 32px rgba(0,0,0,0.55)',
            maxWidth: 280,
            minWidth: 200,
            fontSize: 11.5,
            lineHeight: 1.4,
            color: 'var(--ink-2)',
            textAlign: 'left',
            letterSpacing: 0,
            textTransform: 'none',
            whiteSpace: 'normal',
            fontWeight: 400,
            animation: 'qv-hover-pop 160ms cubic-bezier(.2,.8,.3,1) both',
            pointerEvents: 'none',
          }}
        >
          <div
            style={{
              fontSize: 9.5,
              letterSpacing: '0.16em',
              textTransform: 'uppercase',
              color: 'var(--brand-blue-1)',
              fontWeight: 700,
              marginBottom: 4,
            }}
          >
            {label}
          </div>
          {hint}
        </div>
      )}
    </span>
  );
}

/** Numeric % cell with a horizontal magnitude bar — used for Straddle EM
 *  so the eye can rank rows visually before reading the number. */
function MoveBar({ value, max }: { value: number | null | undefined; max: number }) {
  if (value == null || !Number.isFinite(value)) {
    return <span style={{ color: 'var(--ink-4)' }}>—</span>;
  }
  const pct = Math.min(1, Math.max(0, value / max));
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        justifyContent: 'flex-end',
      }}
    >
      <span
        className="mono tnum"
        style={{ fontSize: 11.5, color: 'var(--ink-2)', minWidth: 44, textAlign: 'right' }}
      >
        {pct1(value)}
      </span>
      <div
        style={{
          width: 56,
          height: 6,
          borderRadius: 3,
          background: 'var(--bg-3)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${pct * 100}%`,
            height: '100%',
            background: 'linear-gradient(90deg, var(--accent-2), var(--accent))',
          }}
        />
      </div>
    </div>
  );
}

/** Compact 0–100% progress bar for IV Rank. Color codes the percentile
 *  band: red ≥70 (rich), green ≤30 (cheap), neutral in between. */
function IvRankBar({ rank }: { rank: number | null | undefined }) {
  if (rank == null || !Number.isFinite(rank)) {
    return <span style={{ color: 'var(--ink-4)' }}>—</span>;
  }
  const pct = Math.min(1, Math.max(0, rank));
  const fill =
    rank > 0.7 ? 'var(--down)' : rank < 0.3 ? 'var(--up)' : 'var(--ink-3)';
  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        justifyContent: 'flex-end',
      }}
    >
      <div
        style={{
          width: 36,
          height: 4,
          borderRadius: 2,
          background: 'var(--bg-3)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${pct * 100}%`,
            height: '100%',
            background: fill,
          }}
        />
      </div>
      <span
        className="mono tnum"
        style={{ fontSize: 11, color: 'var(--ink-2)', minWidth: 32, textAlign: 'right' }}
      >
        {Math.round(pct * 100)}%
      </span>
    </div>
  );
}

function QuoteSkeleton({ width = 64, delayMs = 0 }: { width?: number; delayMs?: number }) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'inline-block',
        width,
        height: 10,
        borderRadius: 999,
        background: 'var(--bg-3)',
        animation: 'earnings-grid-pulse 1.1s ease-in-out infinite',
        animationDelay: `${delayMs}ms`,
        verticalAlign: '-1px',
      }}
    />
  );
}

/** One row of waterfall skeleton bars, mirroring the screener's table
 *  columns. Each row uses a staggered animation-delay so the rows pulse
 *  in a wave from top to bottom — same idea as the calendar grid's
 *  per-column waterfall, just stretched to one wide ticker per row. */
function ScreenerSkeletonRow({ delayMs }: { delayMs: number }) {
  const bar = (extra: number, w: number | string, h: number, r = 4) =>
    ({
      width: w,
      height: h,
      borderRadius: r,
      background: 'var(--bg-3)',
      animation: 'earnings-grid-pulse 1.1s ease-in-out infinite',
      animationDelay: `${delayMs + extra}ms`,
    }) as const;
  const cell = (extra: number, w: number, h: number, align: 'left' | 'right' = 'right') => (
    <td style={{ padding: '16px 14px', textAlign: align }}>
      <span
        aria-hidden
        style={{ display: 'inline-block', ...bar(extra, w, h, 999) }}
      />
    </td>
  );
  return (
    <tr style={{ borderBottom: '1px solid var(--line)' }}>
      {/* Name: logo + ticker + company + method tag */}
      <td className="qv-m-sticky-cell" style={{ padding: '16px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ ...bar(0, 26, 26, 6), display: 'inline-block', flexShrink: 0 }} />
          <span style={{ display: 'inline-block', minWidth: 0 }}>
            <span style={{ display: 'block', ...bar(20, 56, 12) }} />
            <span style={{ display: 'block', ...bar(40, 110, 9), marginTop: 5 }} />
            <span style={{ display: 'block', ...bar(60, 78, 8), marginTop: 4 }} />
          </span>
        </div>
      </td>
      {cell(10, 38, 10)}
      {cell(15, 24, 10)}
      <td style={{ padding: '16px 14px' }}>
        <span aria-hidden style={{ display: 'inline-block', ...bar(20, 26, 10, 999) }} />
      </td>
      {/* Straddle EM — wider, mimics text + bar block */}
      <td style={{ padding: '16px 14px' }}>
        <span
          aria-hidden
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            justifyContent: 'flex-end',
          }}
        >
          <span style={{ display: 'inline-block', ...bar(30, 36, 10, 999) }} />
          <span style={{ display: 'inline-block', ...bar(35, 56, 6, 3) }} />
        </span>
      </td>
      {cell(40, 40, 10)}
      {cell(45, 32, 10)}
      {cell(50, 40, 10)}
      {cell(55, 36, 10)}
      {cell(60, 44, 10)}
      {cell(65, 44, 10)}
      {/* IV Rank — bar + small number */}
      <td style={{ padding: '16px 14px', textAlign: 'right' }}>
        <span
          aria-hidden
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            justifyContent: 'flex-end',
          }}
        >
          <span style={{ display: 'inline-block', ...bar(70, 36, 4, 2) }} />
          <span style={{ display: 'inline-block', ...bar(72, 26, 10, 999) }} />
        </span>
      </td>
      {cell(75, 40, 10)}
      {cell(80, 30, 10)}
      {cell(85, 54, 10)}
      {cell(90, 58, 10)}
      {cell(95, 48, 10)}
      {cell(100, 24, 10)}
    </tr>
  );
}

function shortDate(iso: string) {
  return iso.slice(5, 10).replace('-', '/');
}

function edgePct(ev: ScreenerEvent): number | null {
  const s = ev.em_straddle_pct;
  const m = ev.em_ml_pct;
  if (s == null || m == null) return null;
  return s - m;
}

function band80(ev: ScreenerEvent): number | null {
  const lo = ev.p10;
  const hi = ev.p90;
  if (lo == null || hi == null) return null;
  return hi - lo;
}

/** Hist edge = (straddle − last_4Q avg realized) / last_4Q avg realized.
 *  +ve → implied move is richer than recent realized history (often a sell).
 *  -ve → implied is cheaper than recent realized (often a buy). */
function histEdge(ev: ScreenerEvent): number | null {
  const s = ev.em_straddle_pct;
  const h = ev.hist_move_avg_4q;
  if (s == null || h == null || h === 0) return null;
  return (s - h) / h;
}

function dedupeEvents(rows: ScreenerEvent[]): ScreenerEvent[] {
  const seen = new Set<string>();
  const out: ScreenerEvent[] = [];
  for (const ev of rows) {
    const k = `${ev.ticker}|${ev.earnings_date}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(ev);
  }
  return out;
}

const SORT_KEYS: readonly SortKey[] = [
  'edge', 'dte', 'date', 'straddle', 'ml', 'iv', 'band', 'skew', 'spot',
  'iv_rank', 'hist_avg', 'hist_edge', 'iv_crush',
] as const;

function parseSort(s: string | null): SortKey {
  return (SORT_KEYS as readonly string[]).includes(s ?? '') ? (s as SortKey) : 'hist_edge';
}

function parseDir(d: string | null): SortDir {
  return d === 'asc' ? 'asc' : 'desc';
}

export default function EarningsScreener() {
  // Triggers the one-time EDGAR ticker-names fetch and re-renders this
  // component when the data lands, so non-S&P-500 tickers (SOFI, RIOT,
  // JBL, etc.) pick up their proper company names rather than echoing
  // the ticker symbol.
  useEnsureCompanyNames();

  const router = useRouter();
  const searchParams = useSearchParams();

  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [events, setEvents] = useState<ScreenerEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<Record<string, LiveTick>>({});

  // Three independent gates that together unblock the real table.
  // Each defaults to false and flips true asynchronously:
  //   logosReady — every logo in the bundle has preloaded (or failed cleanly)
  //   quotesReady — first live-quote batch returned, or fallback timer fired
  //   minLoadingDone — minimum skeleton hold elapsed (kills sub-second flash)
  const [logosReady, setLogosReady] = useState(false);
  const [quotesReady, setQuotesReady] = useState(false);
  const [minLoadingDone, setMinLoadingDone] = useState(false);

  const q = (searchParams.get('q') ?? '').trim().toUpperCase();
  const sp500Only = searchParams.get('sp500') === '1';
  const minSpotRaw = searchParams.get('minSpot');
  const minSpotN = (() => {
    const n = Number(minSpotRaw ?? '15');
    return Number.isFinite(n) && n > 0 ? n : 15;
  })();
  const timing = (searchParams.get('timing') ?? 'all') as TimingFilter;
  const mlOnly = searchParams.get('ml') === '1';
  // Preset chips encode common screener intents in a single string param so
  // the URL stays shareable. Only one preset can be active at a time.
  type Preset = 'rich_vol' | 'cheap_vol' | 'big_movers' | 'confident' | null;
  const preset = (searchParams.get('preset') as Preset) ?? null;
  const sortKey = parseSort(searchParams.get('sort'));
  const sortDir = parseDir(searchParams.get('dir'));

  const setParam = useCallback(
    (updates: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [k, v] of Object.entries(updates)) {
        if (v === null || v === '') next.delete(k);
        else next.set(k, v);
      }
      const qs = next.toString();
      router.replace(qs ? `/screener?${qs}` : '/screener', { scroll: false });
    },
    [router, searchParams],
  );

  useEffect(() => {
    let cancelled = false;

    // Defensive JSON fetch: returns null if the response isn't OK or isn't
    // actually JSON. The original "<!DOCTYPE" crash was caused by middleware
    // rewriting a static .json to an HTML page; even after the matcher fix,
    // we don't want one bad payload to take the whole screener down.
    async function fetchJson<T>(url: string): Promise<T | null> {
      const r = await fetch(url);
      if (!r.ok) return null;
      const ct = r.headers.get('content-type') ?? '';
      if (!ct.includes('json')) return null;
      try {
        return (await r.json()) as T;
      } catch {
        return null;
      }
    }

    (async () => {
      setLoading(true);
      setError(null);
      try {
        const bundle = await fetchJson<ScreenerBundle>('/screener.json');
        if (bundle) {
          if (cancelled) return;
          setManifest({
            as_of_date: bundle.metadata?.as_of_date,
            weeks: [],
          });
          setEvents(dedupeEvents(bundle.events ?? []));
          return;
        }

        const man = await fetchJson<Manifest>('/weeks/manifest.json');
        if (!man) throw new Error('No weeks manifest available');
        if (cancelled) return;
        setManifest(man);

        const weekUrls = (man.weeks ?? []).map((w) => `/weeks/${w.start}.json`);
        const payloads = await Promise.all(
          weekUrls.map((url) => fetchJson<WeekPayload>(url)),
        );
        if (cancelled) return;
        const merged = dedupeEvents(
          payloads.flatMap((p) => p?.events ?? []),
        );
        setEvents(merged);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Min-loading hold — guarantees the skeleton shows at least
  // MIN_LOADING_MS so a cached fetch doesn't strobe through it.
  useEffect(() => {
    const t = window.setTimeout(() => setMinLoadingDone(true), MIN_LOADING_MS);
    return () => window.clearTimeout(t);
  }, []);

  // Preload every logo in the bundle so the actual <img> render hits a
  // warm browser cache and never reflow-shifts a row. Keyed on `events`
  // (the full set) rather than `sorted` so filter / sort changes don't
  // trigger a redundant preload pass.
  useEffect(() => {
    if (loading || error) return;
    if (events.length === 0) {
      setLogosReady(true);
      return;
    }
    const tickers = Array.from(new Set(events.map((e) => e.ticker)));
    const uncached = tickers.filter((ticker) => !hasTickerLogoState(ticker));
    if (uncached.length === 0) {
      setLogosReady(true);
      return;
    }
    let cancelled = false;
    setLogosReady(false);
    void preloadTickerLogos(uncached, LOGO_PRELOAD_TIMEOUT_MS).then(() => {
      if (!cancelled) setLogosReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, [events, loading, error]);

  const filtered = useMemo(() => {
    return events.filter((ev) => {
      if (q && !ev.ticker.includes(q)) return false;
      if (sp500Only && !SP500_SET.has(ev.ticker)) return false;
      const spot = ev.spot_price ?? 0;
      if (spot < minSpotN) return false;
      if (timing !== 'all') {
        const b = timingBucket(ev.timing);
        if (timing === 'bmo' && b !== 'bmo') return false;
        if (timing === 'amc' && b !== 'amc') return false;
      }
      if (mlOnly && ev.em_method !== 'ml_lightgbm') return false;

      // Preset filters
      if (preset === 'rich_vol') {
        const he = histEdge(ev);
        if (he == null || he < 0.20) return false;
      } else if (preset === 'cheap_vol') {
        if (ev.iv_rank == null || ev.iv_rank > 0.30) return false;
      } else if (preset === 'big_movers') {
        const m = ev.em_straddle_pct ?? 0;
        if (m < 0.10) return false;
      } else if (preset === 'confident') {
        const bw = band80(ev);
        if (bw == null || bw > 0.08) return false;
      }
      return true;
    });
  }, [events, q, sp500Only, minSpotN, timing, mlOnly, preset]);

  const sorted = useMemo(() => {
    const dir = sortDir === 'asc' ? 1 : -1;
    const finite = (v: number | null | undefined): number | null =>
      v != null && Number.isFinite(v) ? v : null;
    const val = (ev: ScreenerEvent): number | null => {
      switch (sortKey) {
        case 'edge':
          return finite(edgePct(ev));
        case 'dte':
          return finite(ev.lead_time_days);
        case 'date':
          return finite(new Date(ev.earnings_date).getTime());
        case 'straddle':
          return finite(ev.em_straddle_pct);
        case 'ml':
          return finite(ev.em_ml_pct);
        case 'iv':
          return finite(ev.atm_iv);
        case 'band':
          return finite(band80(ev));
        case 'skew':
          return finite(ev.skew_atm);
        case 'spot':
          return finite(ev.spot_price);
        case 'iv_rank':
          return finite(ev.iv_rank);
        case 'hist_avg':
          return finite(ev.hist_move_avg_4q);
        case 'hist_edge':
          return finite(histEdge(ev));
        case 'iv_crush':
          return finite(ev.iv_crush_pct);
        default:
          return 0;
      }
    };
    return [...filtered].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (va == null && vb == null) return a.ticker.localeCompare(b.ticker);
      if (va == null) return 1;
      if (vb == null) return -1;
      if (va === vb) return a.ticker.localeCompare(b.ticker);
      return va < vb ? -dir : dir;
    });
  }, [filtered, sortKey, sortDir]);

  // KPI strip summary across the visible (filtered + sorted) universe.
  // Computed once per `sorted` change so the panes don't recompute per row.
  const summary = useMemo(() => {
    if (sorted.length === 0) return null;
    let rich = 0;
    let cheap = 0;
    let big = 0;
    let ivSum = 0;
    let ivCount = 0;
    let ivMin = Infinity;
    let ivMax = -Infinity;
    for (const e of sorted) {
      const he = histEdge(e);
      if (he != null && he >= 0.20) rich++;
      if (e.iv_rank != null && e.iv_rank <= 0.30) cheap++;
      if (e.em_straddle_pct != null && e.em_straddle_pct >= 0.10) big++;
      if (e.atm_iv != null && Number.isFinite(e.atm_iv)) {
        ivSum += e.atm_iv;
        ivCount++;
        if (e.atm_iv < ivMin) ivMin = e.atm_iv;
        if (e.atm_iv > ivMax) ivMax = e.atm_iv;
      }
    }
    return {
      rich,
      cheap,
      big,
      avgIv: ivCount > 0 ? ivSum / ivCount : null,
      minIv: ivCount > 0 ? ivMin : null,
      maxIv: ivCount > 0 ? ivMax : null,
    };
  }, [sorted]);

  // Largest straddle EM in the visible set, used to scale the MoveBar so
  // the bar lengths read relative to the current view, not an absolute.
  const maxStraddle = useMemo(() => {
    return Math.max(0.04, ...sorted.map((e) => e.em_straddle_pct ?? 0));
  }, [sorted]);

  useEffect(() => {
    const syms = Array.from(new Set(sorted.map((e) => e.ticker)));
    if (syms.length === 0) return;
    const cap = syms.slice(0, 400);
    let cancelled = false;
    // Fallback: unblock the page even if the quote API is slow / down.
    let initialReadyTimer: ReturnType<typeof setTimeout> | null = setTimeout(
      () => setQuotesReady(true),
      INITIAL_QUOTE_WAIT_MS,
    );
    const markQuotesReady = () => {
      if (cancelled) return;
      if (initialReadyTimer) {
        clearTimeout(initialReadyTimer);
        initialReadyTimer = null;
      }
      setQuotesReady(true);
    };
    const fetchOnce = async () => {
      try {
        const res = await fetch(`/api/stocks/batch-price?symbols=${cap.join(',')}`, {
          cache: 'no-store',
        });
        if (!res.ok || cancelled) return;
        const json = (await res.json()) as {
          data: { symbol: string; price: number | null; change: number | null; changePct: number | null }[];
        };
        setLive((prev) => {
          const next = { ...prev };
          const seen = new Set<string>();
          for (const t of json.data) {
            seen.add(t.symbol);
            next[t.symbol] = { price: t.price, change: t.change, changePct: t.changePct };
          }
          for (const symbol of cap) {
            if (!seen.has(symbol) && !next[symbol]) {
              next[symbol] = { price: null, change: null, changePct: null };
            }
          }
          return next;
        });
        markQuotesReady();
      } catch {
        setLive((prev) => {
          const next = { ...prev };
          for (const symbol of cap) {
            if (!next[symbol]) next[symbol] = { price: null, change: null, changePct: null };
          }
          return next;
        });
        markQuotesReady();
      }
    };
    void fetchOnce();
    const onVis = () => {
      if (document.visibilityState === 'visible') void fetchOnce();
    };
    document.addEventListener('visibilitychange', onVis);
    window.addEventListener('focus', onVis);
    return () => {
      cancelled = true;
      if (initialReadyTimer) clearTimeout(initialReadyTimer);
      document.removeEventListener('visibilitychange', onVis);
      window.removeEventListener('focus', onVis);
    };
  }, [sorted]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setParam({ dir: sortDir === 'desc' ? 'asc' : 'desc' });
    } else {
      const defaultDesc = key !== 'dte' && key !== 'date';
      setParam({ sort: key, dir: defaultDesc ? 'desc' : 'asc' });
    }
  };

  const liveRequested = useMemo(
    () => new Set(sorted.slice(0, 400).map((e) => e.ticker)),
    [sorted],
  );

  const th = (key: SortKey, label: string, hint?: string, width?: number) => (
    <SortHeader
      active={sortKey === key}
      dir={sortDir}
      label={label}
      hint={hint}
      width={width}
      onClick={() => toggleSort(key)}
    />
  );

  // Loading + error states are rendered inline below (inside the page chrome)
  // rather than as early returns. Returning early swaps the entire layout
  // when data lands, causing a jarring vertical jump from "Loading…" to the
  // full table. Keeping the header + filters fixed eliminates the jitter.

  // Real rows + KPI strip only unblock once *every* gate is green: data
  // fetched, logos cached, initial quotes back (or timer expired), and
  // the min skeleton hold elapsed. Until then, render the waterfall.
  // All three gates have to clear before the real table renders. The
  // skeleton stays up until the bundle JSON is fetched, all logos have
  // preloaded (or timed out), the first live-quote batch is back (or the
  // wait-bound fired), and the min-loading hold has elapsed. Trades a
  // slower first paint for a more "ready" look.
  const contentReady =
    !loading && !error && logosReady && quotesReady && minLoadingDone;
  const showSkeleton = !error && !contentReady;

  return (
    <div>
      {/* Header — mirrors the Earnings + Watchlist header rhythm exactly:
          padding 24/20, bottom border, 10px kicker, 56px serif h1, small
          mono callout on the right. The old 15px description paragraph
          was dropped — the column headers + insight cards convey the
          same info without competing with the headline. */}
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
            {/* The kicker reserves the same 22px height that the
                Earnings header does for its MARKET CLOSED badge. Without
                this, "SCREENER" sits ~10px higher in the band than the
                date headline on the calendar — the kickers look the
                same but stack with different heights. */}
            <div
              style={{
                fontSize: 10,
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                color: 'var(--ink-3)',
                // 21px — midpoint of the 20 (at 64px) and 22 (at 68px)
                // spacing scales, tuned to the current 66px title.
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
                // 66px — middle of the 64 that read as too small and
                // the 68 that read as too big.
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
                // Mirrors the kicker's 21px gap so the title has equal
                // breathing room above and below.
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
          {/* Right-hand stacked callout (matches the design's panel):
              big serif count → small caps "names · filtered" → mono
              as-of date underneath. Replaces the single-line "N names ·
              as of YYYY-MM-DD" caption that was previously here. */}
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'flex-end',
              gap: 4,
              whiteSpace: 'nowrap',
            }}
          >
            <div
              data-testid="screener-filtered-count"
              className="serif tnum"
              style={{
                fontSize: 32,
                fontWeight: 700,
                letterSpacing: '-0.02em',
                color: 'var(--ink)',
                lineHeight: 1,
                minWidth: 56,
                textAlign: 'right',
              }}
            >
              {sorted.length}
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
              {sorted.length === 1 ? 'name' : 'names'} · filtered
            </div>
            {/* Always rendered so the right panel reserves height for the
                As-of line. Using visibility (not conditional render) prevents
                the count above from jittering upward when the manifest lands. */}
            <div
              className="mono tnum"
              aria-hidden={!manifest?.as_of_date}
              style={{
                fontSize: 11,
                color: 'var(--ink-4)',
                marginTop: 6,
                visibility: manifest?.as_of_date ? 'visible' : 'hidden',
              }}
            >
              As of {manifest?.as_of_date ?? '0000-00-00'}
            </div>
          </div>
        </div>
      </div>

      {/* Insight cards — four cards summarizing the visible universe.
          Each shows the count of names meeting a criterion, the ratio
          of the universe, and a one-line definition. Renders skeleton
          placeholders during loading so the info line below doesn't
          jump up under the header subtext, then shift down when the
          real cards mount. */}
      {!(contentReady && summary) ? (
        <div
          className="qv-m-2col"
          aria-hidden
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
                // Matches the real cards' rendered height. The cards have
                // a 56px serif count plus a 2-line description, so real
                // height lands around 210px even with minHeight: 178.
                // Locking both to the same value keeps the info line +
                // table below from jumping when the cards swap in.
                minHeight: 210,
                animation: 'earnings-grid-pulse 1.4s ease-in-out infinite',
                animationDelay: `${i * 90}ms`,
                opacity: 0.55,
              }}
            />
          ))}
        </div>
      ) : (
        <div
          className="qv-m-2col"
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 14,
            marginTop: 10,
          }}
        >
          {([
            {
              key: 'rich',
              count: summary.rich,
              ratio: summary.rich / Math.max(1, sorted.length),
              isPct: false,
              tone: 'var(--down)',
              kicker: 'Hist edge',
              label: 'Rich vs history',
              desc: 'Implied move at least 20% above last-4Q realized. Often a sell candidate.',
            },
            {
              key: 'cheap',
              count: summary.cheap,
              ratio: summary.cheap / Math.max(1, sorted.length),
              isPct: false,
              tone: 'var(--up)',
              kicker: 'IV rank',
              label: 'Cheap implied vol',
              desc: 'ATM IV in the bottom 30% of its 52-week range. Often a buy candidate.',
            },
            {
              key: 'big',
              count: summary.big,
              ratio: summary.big / Math.max(1, sorted.length),
              isPct: false,
              tone: 'var(--brand-blue-1)',
              kicker: 'Straddle EM',
              label: 'Big movers',
              desc: 'Straddle pricing a ≥ 10% one-day move on print.',
            },
            {
              key: 'iv',
              count:
                summary.avgIv != null
                  ? `${(summary.avgIv * 100).toFixed(1)}%`
                  : '–',
              // Bar shows where the average ATM IV sits between the
              // visible universe's min and max. Flat universe → midpoint.
              ratio:
                summary.avgIv != null && summary.minIv != null && summary.maxIv != null
                  ? summary.maxIv === summary.minIv
                    ? 0.5
                    : (summary.avgIv - summary.minIv) /
                      (summary.maxIv - summary.minIv)
                  : 0,
              isPct: true,
              tone: 'var(--flag)',
              kicker: 'ATM IV',
              label: 'Average vol',
              desc: 'Front-month implied volatility across the universe, annualized.',
            },
          ] as const).map((k) => (
            <div
              key={k.key}
              className="qv-screener-insight"
              style={{
                position: 'relative',
                overflow: 'hidden',
                borderRadius: 14,
                border: '1px solid var(--line)',
                background: `linear-gradient(135deg,
                  color-mix(in oklab, ${k.tone} 8%, var(--bg-2)) 0%,
                  var(--bg-2) 60%)`,
                padding: '18px 20px 20px',
                display: 'flex',
                flexDirection: 'column',
                // Matches the loading-state skeleton minHeight so the
                // page below doesn't shift when real cards swap in.
                minHeight: 210,
              }}
            >
              {/* Top: small caps kicker in the card's accent color. */}
              <div
                style={{
                  fontSize: 10,
                  letterSpacing: '0.18em',
                  textTransform: 'uppercase',
                  color: k.tone,
                  fontWeight: 700,
                  opacity: 0.9,
                }}
              >
                {k.kicker}
              </div>
              {/* Middle: big serif count + /denominator. */}
              <div
                className="serif tnum"
                style={{
                  fontSize: 56,
                  fontWeight: 800,
                  letterSpacing: '-0.035em',
                  color: 'var(--ink)',
                  lineHeight: 0.95,
                  marginTop: 10,
                }}
              >
                {k.count}
                {!k.isPct && (
                  <span
                    style={{
                      fontSize: 16,
                      color: 'var(--ink-3)',
                      fontWeight: 500,
                      letterSpacing: '0.01em',
                      marginLeft: 8,
                    }}
                  >
                    / {sorted.length}
                  </span>
                )}
              </div>
              {/* Ratio bar — visual only. */}
              <div
                style={{
                  height: 4,
                  borderRadius: 2,
                  background: 'color-mix(in oklab, var(--bg-3) 70%, transparent)',
                  overflow: 'hidden',
                  marginTop: 14,
                }}
              >
                <div
                  style={{
                    width: `${k.ratio * 100}%`,
                    height: '100%',
                    background: k.tone,
                    boxShadow: `0 0 8px color-mix(in oklab, ${k.tone} 50%, transparent)`,
                    borderRadius: 2,
                  }}
                />
              </div>
              {/* Bottom: serif label (white) + description (gray). */}
              <div style={{ marginTop: 'auto', paddingTop: 14 }}>
                <div
                  className="serif"
                  style={{
                    fontSize: 15,
                    fontWeight: 700,
                    color: 'var(--ink)',
                    letterSpacing: '-0.005em',
                  }}
                >
                  {k.label}
                </div>
                <div
                  style={{
                    fontSize: 11.5,
                    color: 'var(--ink-3)',
                    marginTop: 4,
                    lineHeight: 1.45,
                  }}
                >
                  {k.desc}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Same italic info line treatment used on the Earnings calendar
          (FilterInfo). The copy is intentionally about navigation, not
          about what each KPI card means — the cards already carry their
          own definitions. */}
      <div
        style={{
          padding: '14px 0 0',
          display: 'flex',
        }}
      >
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
          <Info size={13} />
          <span>
            Sortable table of every upcoming earnings print; stack filters and
            a preset to narrow the universe.
          </span>
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
        <input
          value={searchParams.get('q') ?? ''}
          onChange={(e) => setParam({ q: e.target.value.toUpperCase() || null })}
          placeholder="Ticker"
          className="mono"
          style={{
            width: 100,
            padding: '8px 10px',
            borderRadius: 8,
            border: '1px solid var(--line)',
            background: 'var(--bg-2)',
            color: 'var(--ink-2)',
            caretColor: 'var(--ink)',
            fontSize: 12,
          }}
        />
        <button
          type="button"
          className="chip"
          aria-pressed={sp500Only}
          onClick={() => setParam({ sp500: sp500Only ? null : '1' })}
        >
          S&amp;P 500
        </button>
        <button
          type="button"
          className="chip"
          aria-pressed={mlOnly}
          onClick={() => setParam({ ml: mlOnly ? null : '1' })}
        >
          ML rows only
        </button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--ink-2)' }}>
          <FilterHint
            label="Min spot ($)"
            hint="Hide tickers trading below this share price. Filters out low-priced names where options data is thinner and quotes can be noisy."
          />
          <input
            type="number"
            min={1}
            step={5}
            value={minSpotRaw ?? '15'}
            onChange={(e) => {
              const v = e.target.value;
              setParam({ minSpot: v === '' || v === '15' ? null : v });
            }}
            className="mono tnum"
            style={{
              width: 64,
              padding: '6px 8px',
              borderRadius: 8,
              border: '1px solid var(--line)',
              background: 'var(--bg-2)',
              color: 'var(--ink-2)',
              caretColor: 'var(--ink)',
              fontSize: 12,
            }}
          />
        </label>
        <div
          role="group"
          aria-label="Earnings session"
          style={{
            display: 'inline-flex',
            border: '1px solid var(--line)',
            borderRadius: 8,
            overflow: 'hidden',
          }}
        >
          {(
            [
              ['all', 'All'],
              ['bmo', 'BMO'],
              ['amc', 'AMC'],
            ] as const
          ).map(([key, label], i) => {
            const active = timing === key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => setParam({ timing: key === 'all' ? null : key })}
                aria-pressed={active}
                // fontWeight stays constant across active/inactive so the
                // segment widths don't reflow when the user taps a label.
                style={{
                  padding: '6px 14px',
                  fontSize: 11.5,
                  letterSpacing: '0.04em',
                  fontWeight: 600,
                  background: active ? 'var(--ink)' : 'transparent',
                  color: active ? 'var(--bg)' : 'var(--ink-2)',
                  borderRight: i < 2 ? '1px solid var(--line)' : 'none',
                  cursor: 'pointer',
                  transition: 'background 140ms ease, color 140ms ease',
                }}
              >
                {label}
              </button>
            );
          })}
        </div>

        {/* Preset chips — common options-trader intents one click away */}
        <div style={{ display: 'flex', gap: 6, marginLeft: 'auto', flexWrap: 'wrap' }}>
          {([
            ['rich_vol',   'Rich vs hist',   'Implied move ≥ 20% above last-4Q realized average'],
            ['cheap_vol',  'Cheap IV',       'IV Rank ≤ 30%; options trading near 52-week lows'],
            ['big_movers', 'Big movers',     'Implied move ≥ 10%'],
            ['confident',  'Tight bands',    'P90−P10 ≤ 8%; model is highly confident'],
          ] as [string, string, string][]).map(([key, label, tip]) => {
            const active = preset === key;
            return (
              <button
                key={key}
                type="button"
                title={tip}
                onClick={() => setParam({ preset: active ? null : key })}
                // Holding fontWeight constant at 600 across active + inactive
                // states so toggling a chip doesn't reflow its neighbors:
                // bolder text is wider, which used to shove the chips to
                // the left of the one you tapped sideways by a few pixels.
                // The active state is now communicated entirely through
                // border, background, and color.
                style={{
                  padding: '7px 12px',
                  borderRadius: 999,
                  border: `1px solid ${active ? 'var(--accent)' : 'var(--line)'}`,
                  background: active
                    ? 'color-mix(in srgb, var(--accent) 18%, transparent)'
                    : 'transparent',
                  color: active ? 'var(--accent)' : 'var(--ink-2)',
                  fontSize: 11.5,
                  letterSpacing: '0.04em',
                  fontWeight: 600,
                  cursor: 'pointer',
                  transition: 'border-color 140ms ease, background 140ms ease, color 140ms ease',
                  whiteSpace: 'nowrap',
                }}
                onMouseEnter={(e) => {
                  if (!active) e.currentTarget.style.borderColor = 'var(--line-2)';
                }}
                onMouseLeave={(e) => {
                  if (!active) e.currentTarget.style.borderColor = 'var(--line)';
                }}
              >
                {label}
              </button>
            );
          })}
          {(q !== '' ||
            sp500Only ||
            mlOnly ||
            minSpotRaw != null ||
            timing !== 'all' ||
            preset != null ||
            sortKey !== 'hist_edge' ||
            sortDir !== 'desc') && (
            <button
              type="button"
              title="Clear ticker search, filters, presets, and sort"
              onClick={() =>
                router.replace('/screener', { scroll: false })
              }
              style={{
                padding: '7px 12px',
                borderRadius: 999,
                border: '1px solid var(--line)',
                background: 'transparent',
                color: 'var(--ink-3)',
                fontSize: 11.5,
                letterSpacing: '0.04em',
                fontWeight: 600,
                cursor: 'pointer',
                transition:
                  'border-color 140ms ease, background 140ms ease, color 140ms ease',
                whiteSpace: 'nowrap',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = 'var(--line-2)';
                e.currentTarget.style.color = 'var(--ink)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'var(--line)';
                e.currentTarget.style.color = 'var(--ink-3)';
              }}
            >
              Reset
            </button>
          )}
        </div>
      </div>

      {error && !loading && (
        <div style={{ color: 'var(--down)', fontSize: 13, padding: '24px 0' }}>
          {error}
        </div>
      )}

      {/* Colgroup shared between the skeleton table and the virtualized
          data table. table-layout: fixed honors these widths regardless
          of cell content, which is what kills the filter-toggle column
          shift bug. The wrapper supports horizontal scroll for narrow
          viewports. */}
      {(() => {
        const col = {
          name: SCREENER_NAME_COL_WIDTH,
          date: 72,
          dte: 58,
          session: 84,
          straddle: 150,
          histAvg: 116,
          histEdge: 110,
          ml: 84,
          edge: 128,
          band: 104,
          iv: 96,
          ivRank: 124,
          ivCrush: 108,
          skew: 76,
          dayChange: 92,
          spot: 104,
          straddleAbs: 112,
          optDte: 82,
        } as const;
        const tableWidth = Object.values(col).reduce((sum, width) => sum + width, 0);
        const colGroup = (
          <colgroup>
            <col style={{ width: col.name }} />
            <col style={{ width: col.date }} />
            <col style={{ width: col.dte }} />
            <col style={{ width: col.session }} />
            <col style={{ width: col.straddle }} />
            <col style={{ width: col.histAvg }} />
            <col style={{ width: col.histEdge }} />
            <col style={{ width: col.ml }} />
            <col style={{ width: col.edge }} />
            <col style={{ width: col.band }} />
            <col style={{ width: col.iv }} />
            <col style={{ width: col.ivRank }} />
            <col style={{ width: col.ivCrush }} />
            <col style={{ width: col.skew }} />
            <col style={{ width: col.dayChange }} />
            <col style={{ width: col.spot }} />
            <col style={{ width: col.straddleAbs }} />
            <col style={{ width: col.optDte }} />
          </colgroup>
        );

        const tableStyles: React.CSSProperties = {
          tableLayout: 'fixed',
          borderCollapse: 'separate',
          borderSpacing: 0,
          fontSize: 13,
          color: 'var(--ink-2)',
          width: tableWidth,
        };

        // Both the skeleton + the virtuoso table want the same header
        // markup. The skeleton renders it as a plain <thead>; virtuoso
        // gets it via fixedHeaderContent (still a <tr> inside thead).
        const renderHeaderCells = () => (
          <>
            <th
              className="qv-m-sticky-cell mono"
              style={{
                textAlign: 'left',
                padding: '14px 12px',
                fontSize: 10.5,
                color: 'var(--ink-3)',
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                fontWeight: 600,
                width: col.name,
                minWidth: col.name,
                // Solid bg so the sticky thead doesn't show table rows
                // bleeding through when the body scrolls underneath.
                background: 'var(--bg)',
              }}
            >
              Name
            </th>
            {th('date', 'Date', 'Reporting date for the upcoming earnings event.')}
            {th('dte', 'DTE', 'Calendar days from today until the earnings print.')}
            <th
              className="mono"
              style={{
                textAlign: 'left',
                padding: '14px 12px',
                fontSize: 10.5,
                color: 'var(--ink-3)',
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                fontWeight: 600,
              }}
            >
              Session
            </th>
            {th('straddle', 'Straddle EM', 'One-day move priced by the print-expiry ATM straddle.')}
            {th('hist_avg', 'Hist 4Q avg', 'Mean absolute close-to-close move over the last 4 prints.')}
            {th('hist_edge', 'Hist edge', '(Straddle EM − hist 4Q avg) / hist 4Q avg. Positive = richer.')}
            {th('ml', 'ML EM', 'LightGBM median expected absolute one-day move on print.')}
            {th('edge', 'Edge (vs ML)', 'Straddle EM minus ML EM in percentage points.')}
            {th('band', 'P90−P10', "Width of model's 80% prediction interval. Tighter = more confident.")}
            {th('iv', 'ATM IV', 'Annualized front-month at-the-money implied volatility.')}
            {th('iv_rank', 'IV Rank', "Percentile of today's ATM IV in its trailing 52-week range.")}
            {th('iv_crush', 'IV crush', '(Front IV − next-expiry IV) / front IV. Higher = more crush.')}
            {th('skew', 'Skew', 'ATM call IV minus ATM put IV. Positive = calls richer than puts.')}
            <th
              className="mono"
              style={{
                textAlign: 'right',
                padding: '14px 12px',
                fontSize: 10.5,
                color: 'var(--ink-3)',
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                fontWeight: 600,
                width: col.dayChange,
                minWidth: col.dayChange,
              }}
            >
              1d %
            </th>
            {th('spot', 'Spot', 'Latest underlying share price at the snapshot.', col.spot)}
            <th className="mono" style={{ textAlign: 'right', padding: '14px 12px', fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: '0.18em', textTransform: 'uppercase', fontWeight: 600 }}>
              $ Straddle
            </th>
            <th className="mono" style={{ textAlign: 'right', padding: '14px 12px', fontSize: 10.5, color: 'var(--ink-3)', letterSpacing: '0.18em', textTransform: 'uppercase', fontWeight: 600 }}>
              Opt DTE
            </th>
          </>
        );

        // Body cells for a single visible row. Returned as a fragment so
        // virtuoso can drop it inside its own auto-generated <tr>.
        const renderRowCells = (ev: ScreenerEvent, rowIndex: number) => {
          const e = edgePct(ev);
          const bw = band80(ev);
          const tick = live[ev.ticker];
          const quotePending = liveRequested.has(ev.ticker) && tick === undefined;
          const quoteDelay = (rowIndex % 12) * 35;
          const chg = tick?.changePct;
          const flat = chg != null && Math.round(chg * 10000) / 10000 === 0;
          const up = !flat && (chg ?? 0) >= 0;
          const chgColor = flat ? 'var(--ink-4)' : up ? 'var(--up)' : 'var(--down)';
          const chgText =
            chg == null
              ? '—'
              : `${flat ? '–' : up ? '▲' : '▼'} ${(Math.abs(chg) * 100).toFixed(2)}%`;
          const liveSpot = tick?.price ?? ev.spot_price ?? null;
          return (
            <>
              <NameCell ev={ev} />
              <td className="mono tnum" style={{ textAlign: 'right', padding: '16px 14px', color: 'var(--ink-2)' }}>
                {shortDate(ev.earnings_date)}
              </td>
              <td className="mono tnum" style={{ textAlign: 'right', padding: '16px 14px', color: 'var(--ink-2)' }}>
                {ev.lead_time_days ?? '—'}
              </td>
              <td style={{ padding: '16px 14px' }}>
                {(() => {
                  const t = timingShort(ev.timing);
                  const tone =
                    t === 'BMO'
                      ? 'var(--flag)'
                      : t === 'AMC'
                        ? 'var(--accent-hi)'
                        : 'var(--ink-4)';
                  const bg =
                    t === 'BMO'
                      ? 'color-mix(in oklab, var(--flag) 15%, transparent)'
                      : t === 'AMC'
                        ? 'color-mix(in oklab, var(--accent) 18%, transparent)'
                        : 'color-mix(in oklab, var(--bg-3) 70%, transparent)';
                  return (
                    <span
                      className="mono"
                      style={{
                        display: 'inline-block',
                        fontSize: 10.5,
                        letterSpacing: '0.12em',
                        padding: '3px 8px',
                        borderRadius: 4,
                        fontWeight: 600,
                        color: tone,
                        background: bg,
                      }}
                    >
                      {t}
                    </span>
                  );
                })()}
              </td>
              <td style={{ padding: '16px 14px' }}>
                <MoveBar value={ev.em_straddle_pct} max={maxStraddle} />
              </td>
              <td className="mono tnum" style={{ textAlign: 'right', padding: '16px 14px', color: 'var(--ink-2)' }}>
                {pct1(ev.hist_move_avg_4q)}
              </td>
              {(() => {
                const hEdge = histEdge(ev);
                const hot = hEdge != null && Math.abs(hEdge) >= 0.20;
                const tone = hEdge == null
                  ? 'var(--ink-3)'
                  : hEdge > 0
                    ? 'var(--down)'
                    : 'var(--up)';
                return (
                  <td className="mono tnum" style={{
                    textAlign: 'right', padding: '16px 14px',
                    color: tone,
                    fontWeight: hot ? 600 : 400,
                  }}>
                    {hEdge == null ? '—' : `${hEdge > 0 ? '+' : ''}${(hEdge * 100).toFixed(0)}%`}
                  </td>
                );
              })()}
              <td className="mono tnum" style={{ textAlign: 'right', padding: '16px 14px' }}>
                {pct1(ev.em_ml_pct)}
              </td>
              <td
                className="mono tnum"
                style={{
                  textAlign: 'right',
                  padding: '16px 14px',
                  color:
                    e != null && Math.abs(e) >= 0.008
                      ? 'var(--ink)'
                      : 'var(--ink-2)',
                  fontWeight: e != null && Math.abs(e) >= 0.008 ? 600 : 400,
                }}
              >
                {e == null ? '—' : pct1(e)}
              </td>
              <td className="mono tnum" style={{ textAlign: 'right', padding: '16px 14px', color: 'var(--ink-2)' }}>
                {bw == null ? '—' : pct1(bw)}
              </td>
              <td className="mono tnum" style={{ textAlign: 'right', padding: '16px 14px' }}>
                {ivPct(ev.atm_iv)}
              </td>
              <td style={{ textAlign: 'right', padding: '16px 14px' }}>
                <IvRankBar rank={ev.iv_rank} />
              </td>
              <td className="mono tnum" style={{ textAlign: 'right', padding: '16px 14px', color: 'var(--ink-2)' }}>
                {pct1(ev.iv_crush_pct)}
              </td>
              <td className="mono tnum" style={{ textAlign: 'right', padding: '16px 14px', color: 'var(--ink-2)' }}>
                {num(ev.skew_atm, 3)}
              </td>
              <td
                className="mono tnum"
                style={{
                  textAlign: 'right',
                  padding: '16px 14px',
                  color: chgColor,
                  fontSize: 11,
                  width: col.dayChange,
                  minWidth: col.dayChange,
                  whiteSpace: 'nowrap',
                }}
              >
                <span style={{ display: 'inline-flex', justifyContent: 'flex-end', alignItems: 'center', width: 70 }}>
                  {quotePending ? (
                    <QuoteSkeleton width={58} delayMs={quoteDelay} />
                  ) : (
                    <span style={{ animation: 'earnings-grid-fade-in 160ms ease-out' }}>
                      {chgText}
                    </span>
                  )}
                </span>
              </td>
              <td
                className="mono tnum"
                style={{
                  textAlign: 'right',
                  padding: '16px 14px',
                  width: col.spot,
                  minWidth: col.spot,
                  whiteSpace: 'nowrap',
                }}
              >
                <span style={{ display: 'inline-flex', justifyContent: 'flex-end', alignItems: 'center', width: 74 }}>
                  {quotePending ? (
                    <QuoteSkeleton width={62} delayMs={quoteDelay + 20} />
                  ) : (
                    <span style={{ animation: 'earnings-grid-fade-in 160ms ease-out' }}>
                      {liveSpot != null ? `$${num(liveSpot, 2)}` : '—'}
                    </span>
                  )}
                </span>
              </td>
              <td className="mono tnum" style={{ textAlign: 'right', padding: '16px 14px', color: 'var(--ink-2)' }}>
                {ev.em_straddle_abs != null ? `$${num(ev.em_straddle_abs, 2)}` : '—'}
              </td>
              <td className="mono tnum" style={{ textAlign: 'right', padding: '16px 14px', color: 'var(--ink-3)' }}>
                {ev.days_to_expiry ?? '—'}
              </td>
            </>
          );
        };

        if (error) return null;

        // STATE 1 · Loading skeleton. Plain (non-virtualized) table so
        // the cascading row animations land in the correct DOM order.
        if (showSkeleton) {
          return (
            <div
              role="status"
              aria-busy
              aria-label="Loading screener"
              className="qv-m-table-wrap qv-screener-table-shell"
              style={{ overflowX: 'auto', marginTop: 0, WebkitOverflowScrolling: 'touch' }}
            >
              <table style={tableStyles}>
                {colGroup}
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--line)' }}>
                    {renderHeaderCells()}
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: 12 }).map((_, i) => (
                    <ScreenerSkeletonRow key={`sk-${i}`} delayMs={i * 75} />
                  ))}
                </tbody>
              </table>
            </div>
          );
        }

        // STATE 2 · Empty result. No virtuoso, just a single-line note.
        if (sorted.length === 0) {
          return (
            <div style={{ padding: '48px 0', color: 'var(--ink-3)', fontSize: 13 }}>
              No rows match these filters. Try clearing ticker / S&amp;P / ML-only, or lowering min spot.
            </div>
          );
        }

        // STATE 3 · Virtualized data table (react-virtuoso).
        // useWindowScroll keeps the page's natural scroll behaviour
        // (browser back/forward scroll restoration just works) while
        // virtuoso unmounts off-screen rows so we only ever have ~15
        // visible <tr>s in the DOM, regardless of total result size.
        // The wrapper handles horizontal
        // scrolling. Sticky-Name column survives because we set
        // position: sticky inline on the cell, not on the row.
        const virtuosoComponents = {
          // Inject the colgroup right after the <table> opening
          // tag, before virtuoso's auto-generated thead/tbody.
          Table: forwardRef<HTMLTableElement, React.HTMLAttributes<HTMLTableElement>>(
            function VirtuosoTable({ style, children, ...rest }, ref) {
              return (
                <table ref={ref} {...rest} style={{ ...tableStyles, ...style }}>
                  {colGroup}
                  {children}
                </table>
              );
            },
          ),
          // Keep Virtuoso's own sticky header positioning. Overriding
          // `top` here creates a blank 64px band before the header.
          TableHead: forwardRef<HTMLTableSectionElement, React.HTMLAttributes<HTMLTableSectionElement>>(
            function VirtuosoHead({ style, children, ...rest }, ref) {
              return (
                <thead
                  ref={ref}
                  {...rest}
                  style={{
                    ...style,
                    zIndex: 10,
                    background: 'var(--bg)',
                  }}
                >
                  {children}
                </thead>
              );
            },
          ),
          // Row hover wash — same effect as before, just hoisted
          // into a single component override so each itemContent
          // returns only cells.
          TableRow: ({ children, style, ...rest }: React.HTMLAttributes<HTMLTableRowElement>) => (
            <tr
              {...rest}
              style={{
                ...style,
                borderBottom: '1px solid var(--line)',
                transition: 'background 120ms ease',
              }}
              onMouseEnter={(el) => (el.currentTarget.style.background = 'var(--bg-2)')}
              onMouseLeave={(el) => (el.currentTarget.style.background = 'transparent')}
            >
              {children}
            </tr>
          ),
        } as unknown as TableComponents<ScreenerEvent>;

        return (
          <div
            className="qv-m-table-wrap qv-screener-table-shell"
            style={{ overflowX: 'auto', marginTop: 0, WebkitOverflowScrolling: 'touch' }}
          >
            <TableVirtuoso
              useWindowScroll
              data={sorted}
              computeItemKey={(_, ev) => `${ev.ticker}-${ev.earnings_date}`}
              style={{ width: tableWidth }}
              components={virtuosoComponents}
              fixedHeaderContent={() => (
                <tr style={{ borderBottom: '1px solid var(--line)' }}>
                  {renderHeaderCells()}
                </tr>
              )}
              itemContent={(index, ev) => renderRowCells(ev, index)}
            />
          </div>
        );
      })()}

      {contentReady && sorted.length > 0 && (
        <div
          style={{
            marginTop: 24,
            padding: '16px 0',
            borderTop: '1px solid var(--line)',
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: 11,
            color: 'var(--ink-4)',
          }}
        >
          <span className="mono">
            {sorted.length} rows · Updated hourly
          </span>
          <span style={{ letterSpacing: '0.06em' }}>Quantiv Screener</span>
        </div>
      )}
    </div>
  );
}
