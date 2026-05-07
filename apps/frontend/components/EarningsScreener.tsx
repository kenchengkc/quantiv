'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { COMPANY_NAMES } from '@/lib/companyNames';
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

function companyName(t: string) {
  return COMPANY_NAMES[t] || t;
}

function timingBucket(t?: string): 'bmo' | 'amc' | 'unknown' {
  const k = (t || '').toLowerCase();
  if (k.includes('before') || k === 'bmo') return 'bmo';
  if (k.includes('after') || k === 'amc') return 'amc';
  return 'unknown';
}

function timingShort(t?: string) {
  const b = timingBucket(t);
  if (b === 'bmo') return 'BMO';
  if (b === 'amc') return 'AMC';
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

/* eslint-disable @next/next/no-img-element */
/** Inline ticker logo using the parqet asset CDN with a typographic
 *  fallback. Mirrors the EarningsGrid's Logo but kept local so the
 *  screener doesn't import from another component. */
function ScreenerLogo({ ticker, size = 26 }: { ticker: string; size?: number }) {
  const [err, setErr] = useState(false);
  const s = { width: size, height: size };
  if (err) {
    return (
      <div
        className="serif"
        style={{
          ...s,
          borderRadius: 6,
          background: 'var(--bg-3)',
          border: '1px solid var(--line)',
          display: 'grid',
          placeItems: 'center',
          color: 'var(--ink-2)',
          fontSize: Math.max(9, size * 0.34),
          fontWeight: 600,
          letterSpacing: '0.02em',
        }}
      >
        {ticker.slice(0, 3)}
      </div>
    );
  }
  return (
    <img
      src={`https://assets.parqet.com/logos/symbol/${ticker}?format=png`}
      alt={ticker}
      loading="lazy"
      onError={() => setErr(true)}
      style={{
        ...s,
        borderRadius: 6,
        objectFit: 'cover',
        background: 'var(--paper)',
        border: '1px solid var(--line)',
      }}
    />
  );
}
/* eslint-enable @next/next/no-img-element */

/** Single KPI panel for the strip below the header. */
function KpiCell({
  label,
  value,
  sub,
  tone,
  divider = true,
}: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: string;
  divider?: boolean;
}) {
  return (
    <div
      style={{
        flex: '1 1 0',
        minWidth: 140,
        padding: '16px 18px',
        borderRight: divider ? '1px solid var(--line)' : 'none',
      }}
    >
      <div
        style={{
          fontSize: 9.5,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color: 'var(--ink-4)',
          marginBottom: 8,
        }}
      >
        {label}
      </div>
      <div
        className="serif tnum"
        style={{
          fontSize: 28,
          fontWeight: 700,
          letterSpacing: '-0.02em',
          color: tone || 'var(--ink)',
          lineHeight: 1,
        }}
      >
        {value}
      </div>
      {sub && (
        <div
          className="mono tnum"
          style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 6 }}
        >
          {sub}
        </div>
      )}
    </div>
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
        style={{ fontSize: 11.5, color: 'var(--ink)', minWidth: 44, textAlign: 'right' }}
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
  const router = useRouter();
  const searchParams = useSearchParams();

  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [events, setEvents] = useState<ScreenerEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<Record<string, LiveTick>>({});

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
      const r = await fetch(url, { cache: 'no-store' });
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
    for (const e of sorted) {
      const he = histEdge(e);
      if (he != null && he >= 0.20) rich++;
      if (e.iv_rank != null && e.iv_rank <= 0.30) cheap++;
      if (e.em_straddle_pct != null && e.em_straddle_pct >= 0.10) big++;
      if (e.atm_iv != null && Number.isFinite(e.atm_iv)) {
        ivSum += e.atm_iv;
        ivCount++;
      }
    }
    return {
      rich,
      cheap,
      big,
      avgIv: ivCount > 0 ? ivSum / ivCount : null,
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
      } catch {
        setLive((prev) => {
          const next = { ...prev };
          for (const symbol of cap) {
            if (!next[symbol]) next[symbol] = { price: null, change: null, changePct: null };
          }
          return next;
        });
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

  const th = (key: SortKey, label: string, hint?: string, width?: number) => {
    const active = sortKey === key;
    return (
      <th
        style={{
          textAlign: 'right',
          padding: '10px 8px',
          whiteSpace: 'nowrap',
          ...(width ? { width, minWidth: width } : {}),
        }}
      >
        <button
          type="button"
          onClick={() => toggleSort(key)}
          title={hint}
          className="mono"
          style={{
            border: 'none',
            background: 'transparent',
            cursor: 'pointer',
            fontSize: 10,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: active ? 'var(--ink)' : 'var(--ink-3)',
            fontWeight: active ? 600 : 500,
            padding: 0,
          }}
        >
          {label}
          {active ? (sortDir === 'desc' ? ' ▼' : ' ▲') : ''}
        </button>
      </th>
    );
  };

  // Loading + error states are rendered inline below (inside the page chrome)
  // rather than as early returns. Returning early swaps the entire layout
  // when data lands, causing a jarring vertical jump from "Loading…" to the
  // full table. Keeping the header + filters fixed eliminates the jitter.

  return (
    <div>
      <div
        style={{
          padding: '24px 0 20px',
          borderBottom: '1px solid var(--line)',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            fontSize: 10,
            letterSpacing: '0.18em',
            textTransform: 'uppercase',
            color: 'var(--ink-3)',
            marginBottom: 10,
          }}
        >
          <span>Options · Earnings</span>
          <span style={{ color: 'var(--ink-4)' }}>·</span>
          <span>Quantiv</span>
        </div>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-end',
            gap: 24,
            flexWrap: 'wrap',
          }}
        >
          <div style={{ maxWidth: 760, minWidth: 0 }}>
            <h1
              className="serif"
              style={{
                margin: 0,
                fontSize: 52,
                fontWeight: 800,
                letterSpacing: '-0.03em',
                lineHeight: 0.98,
                textTransform: 'uppercase',
                color: 'var(--ink)',
              }}
            >
              Screener
            </h1>
            <p
              style={{
                margin: '14px 0 0',
                fontSize: 13,
                color: 'var(--ink-3)',
                maxWidth: 720,
                lineHeight: 1.55,
              }}
            >
              Cross-week view for sizing earnings risk. Compare the{' '}
              <strong style={{ color: 'var(--ink-2)' }}>straddle-implied move</strong> against the{' '}
              <strong style={{ color: 'var(--ink-2)' }}>last-4Q realized average</strong>{' '}
              (hist edge), the{' '}
              <strong style={{ color: 'var(--ink-2)' }}>ML model median</strong>, and the{' '}
              <strong style={{ color: 'var(--ink-2)' }}>P90−P10 band</strong>. Click any
              column to sort, or use the preset chips below.
            </p>
          </div>
          {manifest?.as_of_date && (
            <div
              className="mono tnum"
              style={{ fontSize: 11, color: 'var(--ink-4)', whiteSpace: 'nowrap' }}
            >
              Data as of {manifest.as_of_date} · {sorted.length} names
            </div>
          )}
        </div>
      </div>

      {/* KPI strip — five panes summarizing the visible universe.
          Tones: rich = down (red, premium overpriced), cheap = up
          (green, premium discounted), big movers = neutral. */}
      {summary && (
        <div
          style={{
            display: 'flex',
            border: '1px solid var(--line)',
            borderTop: 'none',
            background: 'var(--bg-2)',
            flexWrap: 'wrap',
          }}
        >
          <KpiCell label="Universe" value={String(sorted.length)} sub="filtered names" />
          <KpiCell
            label="Rich vs hist"
            value={String(summary.rich)}
            sub="implied ≥ +20% over 4Q realized"
            tone="var(--down)"
          />
          <KpiCell
            label="Cheap IV"
            value={String(summary.cheap)}
            sub="IV rank ≤ 30%"
            tone="var(--up)"
          />
          <KpiCell
            label="Big movers"
            value={String(summary.big)}
            sub="straddle EM ≥ 10%"
          />
          <KpiCell
            label="Avg ATM IV"
            value={summary.avgIv != null ? `${(summary.avgIv * 100).toFixed(1)}%` : '—'}
            sub="annualized"
            divider={false}
          />
        </div>
      )}

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 12,
          alignItems: 'center',
          padding: '16px 0',
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
            color: 'var(--ink)',
            fontSize: 12,
          }}
        />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--ink-2)' }}>
          <input
            type="checkbox"
            checked={sp500Only}
            onChange={(e) => setParam({ sp500: e.target.checked ? '1' : null })}
          />
          S&amp;P 500
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--ink-2)' }}>
          <input
            type="checkbox"
            checked={mlOnly}
            onChange={(e) => setParam({ ml: e.target.checked ? '1' : null })}
          />
          ML rows only
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--ink-2)' }}>
          Min spot
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
              color: 'var(--ink)',
              fontSize: 12,
            }}
          />
        </label>
        <select
          value={timing}
          onChange={(e) =>
            setParam({ timing: e.target.value === 'all' ? null : e.target.value })
          }
          style={{
            padding: '8px 10px',
            borderRadius: 8,
            border: '1px solid var(--line)',
            background: 'var(--bg-2)',
            color: 'var(--ink)',
            fontSize: 12,
          }}
        >
          <option value="all">All sessions</option>
          <option value="bmo">BMO only</option>
          <option value="amc">AMC only</option>
        </select>

        {/* Preset chips — common options-trader intents one click away */}
        <div style={{ display: 'flex', gap: 6, marginLeft: 'auto', flexWrap: 'wrap' }}>
          {([
            ['rich_vol',   'Rich vs hist',   'Implied move ≥ 20% above last-4Q realized average'],
            ['cheap_vol',  'Cheap IV',       'IV Rank ≤ 30% — options trading near 52-week lows'],
            ['big_movers', 'Big movers',     'Implied move ≥ 10%'],
            ['confident',  'Tight bands',    'P90−P10 ≤ 8% — model is highly confident'],
          ] as [string, string, string][]).map(([key, label, tip]) => {
            const active = preset === key;
            return (
              <button
                key={key}
                type="button"
                title={tip}
                onClick={() => setParam({ preset: active ? null : key })}
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
                  fontWeight: active ? 600 : 500,
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
        </div>
      </div>

      {loading && (
        <div style={{ color: 'var(--ink-3)', fontSize: 13, padding: '40px 0', minHeight: 480 }}>
          Loading earnings universe…
        </div>
      )}

      {error && !loading && (
        <div style={{ color: 'var(--down)', fontSize: 13, padding: '24px 0' }}>
          {error}
        </div>
      )}

      {!loading && !error && (
      <div style={{ overflowX: 'auto', marginTop: 8, WebkitOverflowScrolling: 'touch' }}>
        <table
          style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: 12,
            minWidth: 1040,
          }}
        >
          <thead>
            <tr style={{ borderBottom: '1px solid var(--line)' }}>
              <th style={{ textAlign: 'left', padding: '10px 8px', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                Name
              </th>
              {th('date', 'Date', 'Earnings date')}
              {th('dte', 'DTE', 'Calendar days until earnings (from snapshot)')}
              <th style={{ textAlign: 'left', padding: '10px 8px', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                Session
              </th>
              {th('straddle', 'Straddle EM', 'ATM straddle-implied one-day move')}
              {th('hist_avg', 'Hist 4Q avg', 'Average |close-to-close| move over last 4 earnings reports')}
              {th('hist_edge', 'Hist edge', 'Straddle vs hist average. +ve = implied richer than recent realized')}
              {th('ml', 'ML EM', 'Model median expected |move|')}
              {th('edge', 'Edge (vs ML)', 'Straddle % minus ML %')}
              {th('band', 'P90−P10', 'Quantile spread — wider = more tail uncertainty')}
              {th('iv', 'ATM IV', 'Front ATM implied vol')}
              {th('iv_rank', 'IV Rank', 'Current IV percentile vs trailing 52 weeks (0% = year low, 100% = year high)')}
              {th('iv_crush', 'IV crush', 'Front-month IV premium over the next-out expiry — proxy for post-print IV drop')}
              {th('skew', 'Skew', 'ATM call/put IV skew snapshot')}
              <th
                style={{
                  textAlign: 'right',
                  padding: '10px 8px',
                  fontSize: 10,
                  color: 'var(--ink-3)',
                  letterSpacing: '0.14em',
                  textTransform: 'uppercase',
                  width: 84,
                  minWidth: 84,
                }}
              >
                1d %
              </th>
              {th('spot', 'Spot', 'Latest underlying price when quote data is available', 88)}
              <th style={{ textAlign: 'right', padding: '10px 8px', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                $ Straddle
              </th>
              <th style={{ textAlign: 'right', padding: '10px 8px', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                Opt DTE
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((ev, rowIndex) => {
              const e = edgePct(ev);
              const bw = band80(ev);
              const tick = live[ev.ticker];
              const quotePending = liveRequested.has(ev.ticker) && tick === undefined;
              const quoteDelay = (rowIndex % 12) * 35;
              const chg = tick?.changePct;
              const flat = chg != null && Math.round(chg * 10000) / 10000 === 0;
              const up = !flat && (chg ?? 0) >= 0;
              const chgColor = flat ? 'var(--ink-4)' : up ? 'var(--up)' : 'var(--down)';
              const chgText = chg == null
                ? '—'
                : `${flat ? '–' : up ? '▲' : '▼'} ${(Math.abs(chg) * 100).toFixed(2)}%`;
              const liveSpot = tick?.price ?? ev.spot_price ?? null;

              return (
                <tr
                  key={`${ev.ticker}-${ev.earnings_date}`}
                  style={{
                    borderBottom: '1px solid var(--line)',
                    transition: 'background 120ms ease',
                  }}
                  onMouseEnter={(el) => (el.currentTarget.style.background = 'var(--bg-2)')}
                  onMouseLeave={(el) => (el.currentTarget.style.background = 'transparent')}
                >
                  <td style={{ padding: '10px 8px' }}>
                    <Link
                      href={`/${ev.ticker}`}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                        textDecoration: 'none',
                        color: 'var(--ink)',
                      }}
                    >
                      <ScreenerLogo ticker={ev.ticker} size={26} />
                      <div style={{ minWidth: 0 }}>
                        <div
                          className="serif"
                          style={{
                            fontWeight: 700,
                            color: 'var(--ink)',
                            fontSize: 14,
                            letterSpacing: '-0.005em',
                          }}
                        >
                          {ev.ticker}
                        </div>
                        <div
                          style={{
                            fontSize: 10.5,
                            color: 'var(--ink-4)',
                            marginTop: 1,
                            maxWidth: 160,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {companyName(ev.ticker)}
                        </div>
                        <div
                          className="mono"
                          style={{
                            fontSize: 9,
                            color:
                              ev.em_method === 'ml_lightgbm'
                                ? 'var(--accent)'
                                : 'var(--ink-4)',
                            marginTop: 2,
                            letterSpacing: '0.08em',
                            textTransform: 'uppercase',
                          }}
                        >
                          {ev.em_method === 'ml_lightgbm' ? 'ML forecast' : 'Options baseline'}
                        </div>
                      </div>
                    </Link>
                  </td>
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px', color: 'var(--ink-2)' }}>
                    {shortDate(ev.earnings_date)}
                  </td>
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px', color: 'var(--ink-2)' }}>
                    {ev.lead_time_days ?? '—'}
                  </td>
                  <td className="mono" style={{ padding: '10px 8px', color: 'var(--ink-3)', fontSize: 11 }}>
                    {timingShort(ev.timing)}
                  </td>
                  <td style={{ padding: '10px 8px' }}>
                    <MoveBar value={ev.em_straddle_pct} max={maxStraddle} />
                  </td>
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px', color: 'var(--ink-2)' }}>
                    {pct1(ev.hist_move_avg_4q)}
                  </td>
                  {(() => {
                    const hEdge = histEdge(ev);
                    const hot = hEdge != null && Math.abs(hEdge) >= 0.20;
                    const tone = hEdge == null
                      ? 'var(--ink-3)'
                      : hEdge > 0 ? 'var(--down)' : 'var(--up)';
                    return (
                      <td className="mono tnum" style={{
                        textAlign: 'right', padding: '10px 8px',
                        color: tone,
                        fontWeight: hot ? 600 : 400,
                      }}>
                        {hEdge == null ? '—' : `${hEdge > 0 ? '+' : ''}${(hEdge * 100).toFixed(0)}%`}
                      </td>
                    );
                  })()}
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px' }}>
                    {pct1(ev.em_ml_pct)}
                  </td>
                  <td
                    className="mono tnum"
                    style={{
                      textAlign: 'right',
                      padding: '10px 8px',
                      color: 'var(--ink)',
                      fontWeight: e != null && Math.abs(e) >= 0.008 ? 600 : 400,
                    }}
                  >
                    {e == null ? '—' : pct1(e)}
                  </td>
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px', color: 'var(--ink-2)' }}>
                    {bw == null ? '—' : pct1(bw)}
                  </td>
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px' }}>
                    {ivPct(ev.atm_iv)}
                  </td>
                  <td style={{ textAlign: 'right', padding: '10px 8px' }}>
                    <IvRankBar rank={ev.iv_rank} />
                  </td>
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px', color: 'var(--ink-2)' }}>
                    {pct1(ev.iv_crush_pct)}
                  </td>
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px', color: 'var(--ink-2)' }}>
                    {num(ev.skew_atm, 3)}
                  </td>
                  <td
                    className="mono tnum"
                    style={{
                      textAlign: 'right',
                      padding: '10px 8px',
                      color: chgColor,
                      fontSize: 11,
                      width: 84,
                      minWidth: 84,
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
                      padding: '10px 8px',
                      width: 88,
                      minWidth: 88,
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
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px', color: 'var(--ink-2)' }}>
                    {ev.em_straddle_abs != null ? `$${num(ev.em_straddle_abs, 2)}` : '—'}
                  </td>
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px', color: 'var(--ink-3)' }}>
                    {ev.days_to_expiry ?? '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      )}

      {!loading && !error && sorted.length === 0 && (
        <div style={{ padding: '48px 0', color: 'var(--ink-3)', fontSize: 13 }}>
          No rows match these filters. Try clearing ticker / S&amp;P / ML-only, or lowering min spot.
        </div>
      )}

      {!loading && !error && sorted.length > 0 && (
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
