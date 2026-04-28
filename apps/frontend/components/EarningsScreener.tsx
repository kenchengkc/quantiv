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

type SortKey = 'edge' | 'dte' | 'date' | 'straddle' | 'ml' | 'iv' | 'band' | 'skew' | 'spot';
type SortDir = 'asc' | 'desc';
type TimingFilter = 'all' | 'bmo' | 'amc';

type LiveTick = { change: number | null; changePct: number | null };

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

function parseSort(s: string | null): SortKey {
  if (
    s === 'edge' ||
    s === 'dte' ||
    s === 'date' ||
    s === 'straddle' ||
    s === 'ml' ||
    s === 'iv' ||
    s === 'band' ||
    s === 'skew' ||
    s === 'spot'
  )
    return s;
  return 'edge';
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
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const bundleRes = await fetch('/screener.json', { cache: 'no-store' });
        if (bundleRes.ok) {
          const bundle = (await bundleRes.json()) as ScreenerBundle;
          if (cancelled) return;
          setManifest({
            as_of_date: bundle.metadata?.as_of_date,
            weeks: [],
          });
          setEvents(dedupeEvents(bundle.events ?? []));
          return;
        }

        const manRes = await fetch('/weeks/manifest.json', { cache: 'no-store' });
        if (!manRes.ok) throw new Error('No weeks manifest');
        const man = (await manRes.json()) as Manifest;
        if (cancelled) return;
        setManifest(man);

        const weekUrls = (man.weeks ?? []).map((w) => `/weeks/${w.start}.json`);
        const payloads = await Promise.all(
          weekUrls.map(async (url) => {
            const r = await fetch(url, { cache: 'no-store' });
            if (!r.ok) return { events: [] as ScreenerEvent[] };
            return (await r.json()) as WeekPayload;
          }),
        );
        if (cancelled) return;
        const merged = dedupeEvents(payloads.flatMap((p) => p.events ?? []));
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
      return true;
    });
  }, [events, q, sp500Only, minSpotN, timing, mlOnly]);

  const sorted = useMemo(() => {
    const dir = sortDir === 'asc' ? 1 : -1;
    const val = (ev: ScreenerEvent): number => {
      switch (sortKey) {
        case 'edge':
          return edgePct(ev) ?? -Infinity;
        case 'dte':
          return ev.lead_time_days ?? 999;
        case 'date':
          return new Date(ev.earnings_date).getTime();
        case 'straddle':
          return ev.em_straddle_pct ?? -Infinity;
        case 'ml':
          return ev.em_ml_pct ?? -Infinity;
        case 'iv':
          return ev.atm_iv ?? -Infinity;
        case 'band':
          return band80(ev) ?? -Infinity;
        case 'skew':
          return ev.skew_atm ?? -Infinity;
        case 'spot':
          return ev.spot_price ?? -Infinity;
        default:
          return 0;
      }
    };
    return [...filtered].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (va === vb) return a.ticker.localeCompare(b.ticker);
      return va < vb ? -dir : dir;
    });
  }, [filtered, sortKey, sortDir]);

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
          for (const t of json.data) {
            if (t.price !== null) {
              next[t.symbol] = { change: t.change, changePct: t.changePct };
            }
          }
          return next;
        });
      } catch {
        /* ignore */
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

  const th = (key: SortKey, label: string, hint?: string) => {
    const active = sortKey === key;
    return (
      <th style={{ textAlign: 'right', padding: '10px 8px', whiteSpace: 'nowrap' }}>
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

  if (loading) {
    return (
      <div style={{ color: 'var(--ink-3)', fontSize: 13, padding: '40px 0' }}>
        Loading earnings universe…
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ color: 'var(--down)', fontSize: 13, padding: '24px 0' }}>
        {error}
      </div>
    );
  }

  return (
    <div>
      <div
        style={{
          padding: '24px 0 16px',
          borderBottom: '1px solid var(--line)',
        }}
      >
        <div
          style={{
            fontSize: 10,
            letterSpacing: '0.18em',
            textTransform: 'uppercase',
            color: 'var(--ink-3)',
            marginBottom: 6,
          }}
        >
          Options · earnings
        </div>
        <h1
          className="serif"
          style={{
            margin: 0,
            fontSize: 36,
            fontWeight: 800,
            letterSpacing: '-0.025em',
            lineHeight: 1,
            textTransform: 'uppercase',
          }}
        >
          Screener
        </h1>
        <p
          style={{
            margin: '14px 0 0',
            fontSize: 13,
            color: 'var(--ink-3)',
            maxWidth: 760,
            lineHeight: 1.55,
          }}
        >
          Cross-week view for sizing earnings risk: <strong>straddle-implied move</strong> vs{' '}
          <strong>ML median</strong> (edge), <strong>ATM IV</strong>, <strong>quantile band</strong>{' '}
          (tail uncertainty), <strong>session</strong> (BMO vs AMC), <strong>days to print</strong>, and{' '}
          <strong>1d price change</strong>. Positive edge means the listed straddle embeds a larger one-day
          move than the model median (often worth comparing to your own vol view before trading).
        </p>
        {manifest?.as_of_date && (
          <div className="mono tnum" style={{ fontSize: 11, color: 'var(--ink-4)', marginTop: 10 }}>
            Data as of {manifest.as_of_date} · {sorted.length} names
          </div>
        )}
      </div>

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
      </div>

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
              {th('ml', 'ML EM', 'Model median expected |move|')}
              {th('edge', 'Edge', 'Straddle % minus ML %')}
              {th('band', 'P90−P10', 'Quantile spread — wider = more tail uncertainty')}
              {th('iv', 'ATM IV', 'Front ATM implied vol')}
              {th('skew', 'Skew', 'ATM call/put IV skew snapshot')}
              <th style={{ textAlign: 'right', padding: '10px 8px', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                1d %
              </th>
              {th('spot', 'Spot', 'Underlying price at snapshot')}
              <th style={{ textAlign: 'right', padding: '10px 8px', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                $ Straddle
              </th>
              <th style={{ textAlign: 'right', padding: '10px 8px', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
                Opt DTE
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((ev) => {
              const e = edgePct(ev);
              const bw = band80(ev);
              const tick = live[ev.ticker];
              const chg = tick?.changePct;
              const flat = chg != null && Math.round(chg * 10000) / 10000 === 0;
              const up = !flat && (chg ?? 0) >= 0;
              const chgColor = flat ? 'var(--ink-4)' : up ? 'var(--up)' : 'var(--down)';

              return (
                <tr
                  key={`${ev.ticker}-${ev.earnings_date}`}
                  style={{ borderBottom: '1px solid var(--line)' }}
                >
                  <td style={{ padding: '10px 8px' }}>
                    <Link
                      href={`/${ev.ticker}`}
                      style={{ textDecoration: 'none', color: 'var(--ink)', fontWeight: 600 }}
                    >
                      {ev.ticker}
                    </Link>
                    <div style={{ fontSize: 10, color: 'var(--ink-4)', marginTop: 2, maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {companyName(ev.ticker)}
                    </div>
                    <div className="mono" style={{ fontSize: 9, color: 'var(--ink-4)', marginTop: 2, letterSpacing: '0.06em' }}>
                      {ev.em_method === 'ml_lightgbm' ? 'ML forecast' : 'Options baseline'}
                    </div>
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
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px' }}>
                    {pct1(ev.em_straddle_pct)}
                  </td>
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
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px', color: 'var(--ink-2)' }}>
                    {num(ev.skew_atm, 3)}
                  </td>
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px', color: chgColor, fontSize: 11 }}>
                    {chg == null ? '—' : `${flat ? '–' : up ? '▲' : '▼'} ${(Math.abs(chg) * 100).toFixed(2)}%`}
                  </td>
                  <td className="mono tnum" style={{ textAlign: 'right', padding: '10px 8px' }}>
                    {ev.spot_price != null ? `$${num(ev.spot_price, 2)}` : '—'}
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

      {sorted.length === 0 && (
        <div style={{ padding: '48px 0', color: 'var(--ink-3)', fontSize: 13 }}>
          No rows match these filters. Try clearing ticker / S&amp;P / ML-only, or lowering min spot.
        </div>
      )}
    </div>
  );
}
