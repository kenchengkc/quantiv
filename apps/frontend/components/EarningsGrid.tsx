'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Sun, Moon, Info, ChevronLeft, ChevronRight, Search, Circle } from 'lucide-react';
import { POPULAR_WEIGHT } from '@/lib/popular';
import { COMPANY_NAMES } from '@/lib/companyNames';
import sp500Constituents from '../../../lib/data/sp500-constituents.json';

// Full S&P 500 (503 constituents incl. dual-class). Used for the "S&P 500"
// filter instead of the tiny POPULAR_WEIGHT map.
const SP500_SET: Set<string> = new Set(
  (sp500Constituents as { symbol: string }[]).map((c) => c.symbol),
);

interface EarningsEvent {
  ticker: string;
  earnings_date: string;
  timing: string;
  em_straddle_pct?: number | null;
  em_iv_pct?: number | null;
}

interface WeeklyData {
  metadata: { as_of_date: string; method: string; offset?: number };
  window: { start: string; end: string };
  events: EarningsEvent[];
}

type Filter = 'popular' | 'sp500' | 'movers' | 'all';

const MIN_OFFSET = -1;
const MAX_OFFSET = 2;
const OFFSETS: { v: number; l: string }[] = [
  { v: -1, l: 'Last week' },
  { v: 0, l: 'This week' },
  { v: 1, l: 'Next week' },
  { v: 2, l: 'In two weeks' },
];

function parseLocalDate(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}
function mondayOf(d: Date) {
  const out = new Date(d);
  const day = out.getDay();
  const delta = day === 0 ? -6 : 1 - day;
  out.setDate(out.getDate() + delta);
  out.setHours(0, 0, 0, 0);
  return out;
}
function isoDay(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function timingKey(t?: string) {
  const k = (t || '').toLowerCase();
  if (k === 'bmo' || k === 'before_market_open' || k === 'before_open') return 'bmo' as const;
  if (k === 'amc' || k === 'after_market_close' || k === 'after_close') return 'amc' as const;
  return 'unknown' as const;
}
function logoUrl(t: string) {
  return `https://assets.parqet.com/logos/symbol/${t}?format=png`;
}
function companyName(t: string) {
  return COMPANY_NAMES[t] || t;
}

function Logo({ ticker, size = 24 }: { ticker: string; size?: number }) {
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
  // eslint-disable-next-line @next/next/no-img-element
  return (
    <img
      src={logoUrl(ticker)}
      alt={ticker}
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

function TickerRow({
  ev,
  live,
}: {
  ev: EarningsEvent;
  live?: { change: number | null; changePct: number | null };
}) {
  const movePct = ev.em_straddle_pct ?? ev.em_iv_pct ?? null;
  const changePct = live?.changePct ?? null;
  const change = live?.change ?? null;
  // Round to display precision before deciding flat — avoids "−0.00 (-0.00%)"
  // showing as a down move because the underlying float was -1e-5.
  const pctRounded = changePct !== null ? Math.round(changePct * 10000) / 10000 : null;
  const dollarRounded = change !== null ? Math.round(change * 100) / 100 : null;
  const flat = pctRounded === 0 && (dollarRounded === null || dollarRounded === 0);
  const up = !flat && (changePct ?? 0) >= 0;
  const arrow = flat ? '–' : up ? '▲' : '▼';
  const color = flat ? 'var(--ink-4)' : up ? 'var(--up)' : 'var(--down)';
  return (
    <Link
      href={`/${ev.ticker}`}
      style={{
        display: 'grid',
        gridTemplateColumns: '24px minmax(0, 1fr) auto',
        alignItems: 'center',
        gap: 8,
        padding: '7px 8px',
        borderRadius: 6,
        textDecoration: 'none',
        transition: 'background 120ms ease',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-3)')}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
    >
      <Logo ticker={ev.ticker} size={24} />
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 }}>
          <span
            style={{
              fontWeight: 600,
              color: 'var(--ink)',
              fontSize: 12.5,
              letterSpacing: '0.01em',
              flexShrink: 0,
            }}
          >
            {ev.ticker}
          </span>
          <span
            style={{
              color: 'var(--ink-4)',
              fontSize: 10.5,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              minWidth: 0,
            }}
          >
            {companyName(ev.ticker)}
          </span>
        </div>
        {changePct !== null && (
          <div
            className="mono tnum"
            style={{
              fontSize: 10,
              color,
              marginTop: 2,
              letterSpacing: '0.01em',
            }}
          >
            {arrow}{' '}
            {change !== null ? `$${Math.abs(change).toFixed(2)} ` : ''}
            ({Math.abs(changePct * 100).toFixed(2)}%)
          </div>
        )}
      </div>
      <div
        className="serif tnum"
        style={{
          fontSize: 14,
          fontWeight: 600,
          color: 'var(--ink)',
          flexShrink: 0,
          whiteSpace: 'nowrap',
        }}
      >
        {movePct !== null ? (
          <>
            ±{(movePct * 100).toFixed(1)}
            <span style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 1 }}>%</span>
          </>
        ) : (
          <span style={{ fontSize: 11, color: 'var(--ink-4)' }}>—</span>
        )}
      </div>
    </Link>
  );
}

type LiveMap = Record<string, { change: number | null; changePct: number | null }>;

function Group({
  title,
  icon,
  list,
  tone,
  live,
}: {
  title: string;
  icon: React.ReactNode;
  list: EarningsEvent[];
  tone: string;
  live: LiveMap;
}) {
  if (list.length === 0) return null;
  return (
    <div style={{ marginBottom: 10 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '6px 10px',
          color: 'var(--ink-3)',
          fontSize: 10,
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          borderTop: '1px solid var(--line)',
        }}
      >
        <span style={{ color: tone, display: 'inline-flex' }}>{icon}</span>
        <span>{title}</span>
        <span
          className="mono tnum"
          style={{ marginLeft: 'auto', color: 'var(--ink-4)', letterSpacing: 0 }}
        >
          {list.length}
        </span>
      </div>
      <div>
        {list.map((e) => (
          <TickerRow key={`${e.ticker}-${e.earnings_date}`} ev={e} live={live[e.ticker]} />
        ))}
      </div>
    </div>
  );
}

function DayBlock({
  dateLabel,
  label,
  events,
  isToday,
  live,
}: {
  dateLabel: string;
  label: string;
  events: EarningsEvent[];
  isToday: boolean;
  live: LiveMap;
}) {
  const bmo = events.filter((e) => timingKey(e.timing) === 'bmo');
  const amc = events.filter((e) => timingKey(e.timing) === 'amc');
  const unk = events.filter((e) => timingKey(e.timing) === 'unknown');

  return (
    <div style={{ minWidth: 0 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 10,
          padding: '0 10px 14px',
          marginBottom: 4,
        }}
      >
        <div
          className="serif"
          style={{
            fontSize: 32,
            lineHeight: 1,
            fontWeight: 800,
            color: isToday ? 'var(--ink)' : 'var(--ink-2)',
            letterSpacing: '-0.03em',
          }}
        >
          {dateLabel}
        </div>
        <div style={{ flex: 1 }}>
          <div
            style={{
              fontSize: 11,
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              color: isToday ? 'var(--accent)' : 'var(--ink-3)',
              fontWeight: 500,
            }}
          >
            {label}
            {isToday && ' · Today'}
          </div>
          <div
            className="mono tnum"
            style={{ fontSize: 10.5, color: 'var(--ink-4)', marginTop: 2 }}
          >
            {events.length} {events.length === 1 ? 'report' : 'reports'}
          </div>
        </div>
      </div>

      <Group title="Before open" icon={<Sun size={12} />} list={bmo} tone="var(--flag)" live={live} />
      <Group title="After close" icon={<Moon size={12} />} list={amc} tone="var(--accent)" live={live} />
      <Group
        title="Timing unconfirmed"
        icon={<Circle size={10} />}
        list={unk}
        tone="var(--ink-4)"
        live={live}
      />

      {events.length === 0 && (
        <div
          style={{
            padding: '24px 10px',
            color: 'var(--ink-4)',
            fontSize: 12,
            borderTop: '1px solid var(--line)',
            textAlign: 'center',
          }}
        >
          Quiet day.
        </div>
      )}
    </div>
  );
}

function FilterInfo({ filter }: { filter: Filter }) {
  const msg = {
    popular: 'Ranked by a blend of options volume, market cap, and recent news flow.',
    sp500: 'S&P 500 constituents only.',
    movers: 'Tickers whose implied move is ≥ 10% this week.',
    all: 'Every confirmed earnings report in our calendar.',
  }[filter];
  return (
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
      <span>{msg}</span>
    </span>
  );
}

function WeekHeader({
  offset,
  setOffset,
  windowLabel,
  filter,
  setFilter,
  search,
  setSearch,
}: {
  offset: number;
  setOffset: (n: number) => void;
  windowLabel: string;
  filter: Filter;
  setFilter: (f: Filter) => void;
  search: string;
  setSearch: (s: string) => void;
}) {
  return (
    <div style={{ padding: '24px 0 20px', borderBottom: '1px solid var(--line)' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-end',
          gap: 24,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ maxWidth: 560, minWidth: 0 }}>
          <div
            style={{
              fontSize: 10,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              color: 'var(--ink-3)',
              marginBottom: 6,
            }}
          >
            Earnings Week
          </div>
          <h1
            className="serif"
            style={{
              margin: 0,
              fontSize: 40,
              fontWeight: 800,
              letterSpacing: '-0.025em',
              lineHeight: 1.0,
              color: 'var(--ink)',
              textWrap: 'balance',
              textTransform: 'uppercase',
            }}
          >
            {windowLabel}
          </h1>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <button
            className="chip"
            onClick={() => setOffset(Math.max(MIN_OFFSET, offset - 1))}
            disabled={offset <= MIN_OFFSET}
            style={{ width: 32, padding: 0, justifyContent: 'center' }}
            aria-label="Previous week"
          >
            <ChevronLeft size={14} />
          </button>
          <div style={{ display: 'flex', gap: 2, padding: '0 6px' }}>
            {OFFSETS.map((o) => (
              <button
                key={o.v}
                className="chip"
                aria-pressed={offset === o.v}
                onClick={() => setOffset(o.v)}
                style={{ fontSize: 11 }}
              >
                {o.l}
              </button>
            ))}
          </div>
          <button
            className="chip"
            onClick={() => setOffset(Math.min(MAX_OFFSET, offset + 1))}
            disabled={offset >= MAX_OFFSET}
            style={{ width: 32, padding: 0, justifyContent: 'center' }}
            aria-label="Next week"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>

      <div
        style={{
          marginTop: 28,
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          flexWrap: 'wrap',
        }}
      >
        <div
          style={{
            color: 'var(--ink-3)',
            fontSize: 11,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
          }}
        >
          Show
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {(
            [
              { key: 'popular', label: 'Popular', tip: 'Top ~50 by options volume & market cap.' },
              { key: 'sp500', label: 'S&P 500', tip: 'S&P 500 constituents only.' },
              { key: 'movers', label: 'Big movers', tip: 'Expected move ≥ 10% this week.' },
              { key: 'all', label: 'All', tip: 'Every confirmed earnings report.' },
            ] as { key: Filter; label: string; tip: string }[]
          ).map((f) => (
            <button
              key={f.key}
              className="chip"
              aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}
              title={f.tip}
            >
              {f.label}
            </button>
          ))}
        </div>
        <FilterInfo filter={filter} />
        <div style={{ flex: 1 }} />
        <div style={{ position: 'relative' }}>
          <Search
            size={13}
            style={{
              position: 'absolute',
              left: 10,
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--ink-4)',
            }}
          />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value.toUpperCase())}
            placeholder="Jump to ticker"
            className="outline-none"
            style={{
              background: 'transparent',
              border: '1px solid var(--line)',
              color: 'var(--ink)',
              padding: '6px 12px 6px 30px',
              borderRadius: 999,
              fontSize: 12,
              width: 180,
              fontFamily: 'inherit',
            }}
            onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--line-2)')}
            onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--line)')}
          />
        </div>
      </div>
    </div>
  );
}

export default function EarningsGrid() {
  // Persist (offset, filter) in the URL so that navigating to a ticker and
  // hitting back returns the user to the same week + filter they were on.
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialOffset = (() => {
    const v = Number(searchParams.get('offset'));
    return Number.isFinite(v) && v >= MIN_OFFSET && v <= MAX_OFFSET ? v : 0;
  })();
  const initialFilter: Filter = (() => {
    const v = searchParams.get('filter');
    return v === 'popular' || v === 'sp500' || v === 'movers' || v === 'all'
      ? v
      : 'popular';
  })();
  const [offset, setOffset] = useState(initialOffset);
  const [filter, setFilter] = useState<Filter>(initialFilter);
  const [search, setSearch] = useState('');

  // Mirror state → URL query. Omit default values so a fresh landing stays
  // on a clean `/` URL.
  useEffect(() => {
    const next = new URLSearchParams();
    if (offset !== 0) next.set('offset', String(offset));
    if (filter !== 'popular') next.set('filter', filter);
    const qs = next.toString();
    const url = qs ? `/?${qs}` : '/';
    // replace (not push) so state updates don't pollute back-button history.
    router.replace(url, { scroll: false });
  }, [offset, filter, router]);
  const [data, setData] = useState<WeeklyData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<LiveMap>({});

  const thisMonday = useMemo(() => mondayOf(new Date()), []);
  const weekStartIso = useMemo(() => {
    const d = new Date(thisMonday);
    d.setDate(d.getDate() + 7 * offset);
    return isoDay(d);
  }, [thisMonday, offset]);

  const fetchWeek = useCallback(
    async (iso: string) => {
      const urls = [`/weeks/${iso}.json`, offset === 0 ? '/weekly.json' : null].filter(
        Boolean,
      ) as string[];
      for (const url of urls) {
        const res = await fetch(url, { cache: 'no-store' });
        if (res.ok) return (await res.json()) as WeeklyData;
      }
      throw new Error(`no data for ${iso}`);
    },
    [offset],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchWeek(weekStartIso)
      .then((json) => { if (!cancelled) setData(json); })
      .catch((e) => { if (!cancelled) setError((e as Error).message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [weekStartIso, fetchWeek]);

  useEffect(() => {
    if (!data || data.events.length === 0) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const symbols = Array.from(new Set(data.events.map((e) => e.ticker))).slice(0, 200);

    // Poll live prices until every ticker has a value, then back off. First
    // request triggers server-side background fetches for any Redis miss;
    // subsequent polls catch them as they land.
    const poll = async (attempt = 0) => {
      if (cancelled) return;
      try {
        const res = await fetch(`/api/stocks/batch-price?symbols=${symbols.join(',')}`, {
          cache: 'no-store',
        });
        if (!res.ok) return;
        const json = (await res.json()) as {
          pending?: number;
          data: { symbol: string; price: number | null; change: number | null; changePct: number | null }[];
        };
        if (cancelled) return;
        setLive((prev) => {
          const next: LiveMap = { ...prev };
          for (const t of json.data) {
            // Only overwrite with non-null values so we never *remove* a
            // price that landed on an earlier poll.
            if (t.price !== null) {
              next[t.symbol] = { change: t.change, changePct: t.changePct };
            }
          }
          return next;
        });
        const pending = json.pending ?? 0;
        if (pending > 0 && attempt < 20) {
          // 8s between polls — matches the Finnhub warmup cadence reasonably
          // well without spamming the API. Cap at 20 attempts (~2.5 min).
          timer = setTimeout(() => poll(attempt + 1), 8_000);
        }
      } catch {
        /* ignore */
      }
    };
    poll();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [data]);

  const weekStart = useMemo(
    () => (data ? parseLocalDate(data.window.start) : parseLocalDate(weekStartIso)),
    [data, weekStartIso],
  );
  const days = useMemo(
    () =>
      Array.from({ length: 5 }, (_, i) => {
        const d = new Date(weekStart);
        d.setDate(weekStart.getDate() + i);
        return d;
      }),
    [weekStart],
  );

  const winLabel = useMemo(() => {
    const s = days[0];
    const e = days[4];
    if (!s || !e) return '';
    const m = (d: Date) => d.toLocaleDateString('en-US', { month: 'long' });
    const sameMonth = s.getMonth() === e.getMonth();
    return sameMonth
      ? `${m(s)} ${s.getDate()} – ${e.getDate()}, ${s.getFullYear()}`
      : `${m(s)} ${s.getDate()} – ${m(e)} ${e.getDate()}, ${s.getFullYear()}`;
  }, [days]);

  const today = useMemo(() => {
    const t = new Date();
    t.setHours(0, 0, 0, 0);
    return t;
  }, []);

  const filteredEvents = useMemo(() => {
    if (!data) return [] as EarningsEvent[];
    let list = data.events;
    if (filter === 'popular') list = list.filter((e) => (POPULAR_WEIGHT[e.ticker] ?? 0) >= 76);
    if (filter === 'sp500') list = list.filter((e) => SP500_SET.has(e.ticker));
    if (filter === 'movers') list = list.filter((e) => (e.em_straddle_pct ?? e.em_iv_pct ?? 0) >= 0.10);
    if (search) list = list.filter((e) => e.ticker.startsWith(search));
    return list;
  }, [data, filter, search]);

  const eventsByDay = useMemo(() => {
    return days.map((day) => {
      const iso = isoDay(day);
      return filteredEvents
        .filter((e) => e.earnings_date.slice(0, 10) === iso)
        .sort(
          (a, b) => (POPULAR_WEIGHT[b.ticker] ?? 0) - (POPULAR_WEIGHT[a.ticker] ?? 0),
        );
    });
  }, [days, filteredEvents]);

  const dayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];

  return (
    <>
      <WeekHeader
        offset={offset}
        setOffset={setOffset}
        windowLabel={winLabel}
        filter={filter}
        setFilter={setFilter}
        search={search}
        setSearch={setSearch}
      />

      {loading && (
        <div style={{ padding: '60px 0', textAlign: 'center', color: 'var(--ink-3)', fontSize: 13 }}>
          Loading earnings calendar…
        </div>
      )}

      {error && !loading && (
        <div
          style={{
            padding: 20,
            marginTop: 20,
            border: '1px solid var(--down)',
            borderRadius: 12,
            color: 'var(--down)',
            fontSize: 13,
          }}
        >
          Couldn&apos;t load weekly data: {error}
        </div>
      )}

      {!loading && !error && data && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(5, minmax(0, 1fr))',
            marginTop: 20,
          }}
        >
          {days.map((d, i) => (
            <div
              key={d.toISOString()}
              style={{
                borderRight: i < 4 ? '1px solid var(--line)' : 'none',
              }}
            >
              <DayBlock
                dateLabel={String(d.getDate())}
                label={dayNames[i]}
                events={eventsByDay[i]}
                isToday={d.getTime() === today.getTime()}
                live={live}
              />
            </div>
          ))}
        </div>
      )}

      {data && (
        <div
          style={{
            marginTop: 40,
            padding: '16px 0',
            borderTop: '1px solid var(--line)',
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: 11,
            color: 'var(--ink-4)',
          }}
        >
          <span className="mono">
            {filteredEvents.length} reports · {data.metadata.method} · as of {data.metadata.as_of_date}
          </span>
        </div>
      )}
    </>
  );
}
