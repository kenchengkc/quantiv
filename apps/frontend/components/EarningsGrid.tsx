'use client';

import { useCallback, useEffect, useMemo, useState, useTransition } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Sun, Moon, Info, ChevronLeft, ChevronRight, Search, Circle, Clock } from 'lucide-react';
import { POPULAR_WEIGHT } from '@/lib/popular';
import { companyName } from '@/lib/companyNames';
import { useEnsureCompanyNames } from '@/lib/useCompanyNames';
import { useTickerHover } from '@/components/TickerHoverCard';
import { CalendarGridSkeleton } from '@/components/EarningsGridSkeleton';
import {
  hasTickerLogoState,
  preloadTickerLogos,
  TickerLogo,
} from '@/components/TickerLogo';
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
// Three gates run in parallel before the calendar's real rows render.
// Each stops a different class of "looks loaded but isn't" jitter:
//   MIN_GRID_LOADING_MS    — minimum skeleton hold so a cache-warm fetch
//                            doesn't strobe the skeleton on/off in <100 ms.
//   INITIAL_QUOTE_WAIT_MS  — upper bound on waiting for the first live-quote
//                            batch before unblocking; rows still appear if
//                            the quote API is slow.
//   LOGO_PRELOAD_TIMEOUT_MS — caps logo preload so one slow third-party
//                            image can't hold the whole page hostage.
const MIN_GRID_LOADING_MS = 750;
const INITIAL_QUOTE_WAIT_MS = 3_200;
const LOGO_PRELOAD_TIMEOUT_MS = 4_000;
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
  if (k === 'dmh' || k === 'during_market_hours' || k === 'during_market_hour') return 'dmh' as const;
  return 'unknown' as const;
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
  const hover = useTickerHover(ev.ticker);
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
      onMouseEnter={(e) => {
        e.currentTarget.style.background = 'var(--bg-3)';
        hover.onMouseEnter(e);
      }}
      onMouseMove={hover.onMouseMove}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent';
        hover.onMouseLeave();
      }}
    >
      <TickerLogo ticker={ev.ticker} size={24} radius={6} loading="eager" />
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 }}>
          <span
            className="serif"
            style={{
              fontWeight: 800,
              color: 'var(--ink-2)',
              fontSize: 13,
              letterSpacing: '-0.01em',
              textTransform: 'uppercase',
              flexShrink: 0,
            }}
          >
            {ev.ticker}
          </span>
          <span
            style={{
              color: 'var(--ink-3)',
              fontSize: 11,
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
            {arrow} {Math.abs(changePct * 100).toFixed(2)}%
          </div>
        )}
      </div>
      <div
        className="serif tnum"
        style={{
          fontSize: 14,
          fontWeight: 600,
          color: 'var(--ink-2)',
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
    <div style={{ marginBottom: 12 }}>
      {/* Stronger session separator: tinted background + colored left rule
          + slightly bolder label so BMO/AMC sections are visually distinct
          inside a single day cell. */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 7,
          padding: '7px 11px 7px 9px',
          color: 'var(--ink-2)',
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          background: `color-mix(in srgb, ${tone} 24%, var(--bg-2))`,
          borderLeft: `5px solid ${tone}`,
          borderTop: `1px solid color-mix(in srgb, ${tone} 55%, var(--line))`,
          borderBottom: `1px solid color-mix(in srgb, ${tone} 55%, var(--line))`,
        }}
      >
        <span style={{ color: tone, display: 'inline-flex' }}>{icon}</span>
        <span>{title}</span>
        <span
          className="mono tnum"
          style={{
            marginLeft: 'auto',
            color: 'var(--ink-2)',
            letterSpacing: 0,
            fontWeight: 700,
          }}
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
  const dmh = events.filter((e) => timingKey(e.timing) === 'dmh');
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
        title="During market hours"
        icon={<Clock size={12} />}
        list={dmh}
        tone="var(--ink-3)"
        live={live}
      />
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
    popular: 'Ranked by a 70/30 blend of 90-day dollar volume and market cap.',
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
  marketOpen,
}: {
  offset: number;
  setOffset: (n: number) => void;
  windowLabel: string;
  filter: Filter;
  setFilter: (f: Filter) => void;
  search: string;
  setSearch: (s: string) => void;
  marketOpen: boolean;
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
              marginBottom: 14,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flexWrap: 'wrap',
              // Reserve the row's height so the MARKET CLOSED badge can
              // appear without pushing the headline below it. The badge
              // is ~22px tall; matching that here keeps the row stable
              // whether the badge is rendered or not.
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
                // Brand asset has a solid black background. Screen-blend
                // it onto the page so only the ring + tail survive against
                // the dark background.
                mixBlendMode: 'screen',
              }}
            />
            <span>Earnings Week</span>
            <span
              title="US equity regular session is 09:30–16:00 ET. After the close, quotes may still update for a short time while data feeds settle."
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                padding: '2px 8px',
                borderRadius: 999,
                border: '1px solid var(--line)',
                background: 'var(--bg-2)',
                color: 'var(--ink-3)',
                letterSpacing: '0.08em',
                fontSize: 9.5,
                // visibility (not display) keeps the badge in the
                // layout flow regardless of marketOpen, so the row
                // height is fixed and there's no CLS when the API
                // result flips marketOpen from true → false.
                visibility: marketOpen ? 'hidden' : 'visible',
              }}
              aria-hidden={marketOpen}
            >
              <span style={{
                width: 6, height: 6, borderRadius: 999,
                background: 'var(--ink-4)',
              }} />
              MARKET CLOSED · LAST CLOSE
            </span>
          </div>
          <h1
            className="serif qv-m-h1"
            style={{
              margin: 0,
              fontSize: 56,
              fontWeight: 800,
              letterSpacing: '-0.032em',
              lineHeight: 0.94,
              color: 'var(--ink)',
              textWrap: 'balance',
              textTransform: 'uppercase',
            }}
          >
            {windowLabel}
          </h1>
          <div
            style={{
              marginTop: 14,
              fontSize: 16,
              color: 'var(--ink-2)',
              maxWidth: 660,
              lineHeight: 1.55,
              letterSpacing: '-0.005em',
            }}
          >
            Tracking what options markets expect and what the market actually delivers.
          </div>
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
              { key: 'popular', label: 'Popular', tip: 'Weight >= 76 from 90-day dollar volume and market cap.' },
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
        <div style={{ position: 'relative', display: 'flex', alignItems: 'center', height: 34 }}>
          <Search
            size={13}
            style={{
              position: 'absolute',
              left: 10,
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--ink-3)',
            }}
          />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value.toUpperCase())}
            placeholder="Jump to ticker"
            className="qv-ticker-search-input outline-none"
            style={{
              display: 'block',
              height: 34,
              lineHeight: '34px',
              background: 'color-mix(in oklab, var(--bg-2) 88%, transparent)',
              border: '1px solid var(--line-2)',
              boxShadow: 'inset 0 0 0 1px color-mix(in oklab, var(--ink) 4%, transparent)',
              color: 'var(--ink)',
              caretColor: 'var(--ink)',
              padding: '0 12px 0 30px',
              borderRadius: 999,
              fontSize: 12,
              width: 188,
              fontFamily: 'inherit',
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = 'var(--accent)';
              e.currentTarget.style.boxShadow = '0 0 0 2px color-mix(in oklab, var(--accent) 18%, transparent)';
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = 'var(--line-2)';
              e.currentTarget.style.boxShadow = 'inset 0 0 0 1px color-mix(in oklab, var(--ink) 4%, transparent)';
            }}
          />
        </div>
      </div>
    </div>
  );
}

export default function EarningsGrid() {
  // Triggers the one-time EDGAR ticker-names fetch + re-render on
  // arrival so non-S&P-500 tickers in the weekly view get their real
  // names instead of echoing the ticker symbol.
  useEnsureCompanyNames();

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
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<LiveMap>({});
  const [marketOpen, setMarketOpen] = useState<boolean>(true);
  const [minLoadingDoneWeek, setMinLoadingDoneWeek] = useState<string | null>(null);
  const [quotesReadyWeek, setQuotesReadyWeek] = useState<string | null>(null);
  const [logosReadyKey, setLogosReadyKey] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const thisMonday = useMemo(() => mondayOf(new Date()), []);
  const weekStartIso = useMemo(() => {
    const d = new Date(thisMonday);
    d.setDate(d.getDate() + 7 * offset);
    return isoDay(d);
  }, [thisMonday, offset]);

  useEffect(() => {
    setMinLoadingDoneWeek(null);
    const timeoutId = window.setTimeout(() => {
      setMinLoadingDoneWeek(weekStartIso);
    }, MIN_GRID_LOADING_MS);
    return () => window.clearTimeout(timeoutId);
  }, [weekStartIso]);

  const fetchWeek = useCallback(
    async (iso: string) => {
      const urls = [`/weeks/${iso}.json`, offset === 0 ? '/weekly.json' : null].filter(
        Boolean,
      ) as string[];
      for (const url of urls) {
        const res = await fetch(url);
        if (res.ok) return (await res.json()) as WeeklyData;
      }
      throw new Error(`no data for ${iso}`);
    },
    [offset],
  );

  useEffect(() => {
    let cancelled = false;
    setIsFetching(true);
    setError(null);
    fetchWeek(weekStartIso)
      .then((json) => {
        if (!cancelled) setData(json);
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setIsFetching(false);
      });
    return () => {
      cancelled = true;
    };
  }, [weekStartIso, fetchWeek]);

  useEffect(() => {
    const dataWeek = data?.window?.start?.slice(0, 10) ?? null;
    if (!data || dataWeek !== weekStartIso) {
      setQuotesReadyWeek(null);
      return;
    }
    if (data.events.length === 0) {
      setQuotesReadyWeek(weekStartIso);
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let initialReadyTimer: ReturnType<typeof setTimeout> | null = null;
    const symbols = Array.from(new Set(data.events.map((e) => e.ticker)));

    let lastMarketOpen = true;
    let lastQuoteRefreshActive = true;
    const markQuotesReady = () => {
      if (cancelled) return;
      if (initialReadyTimer) {
        clearTimeout(initialReadyTimer);
        initialReadyTimer = null;
      }
      setQuotesReadyWeek(weekStartIso);
    };

    setQuotesReadyWeek(null);
    initialReadyTimer = setTimeout(markQuotesReady, INITIAL_QUOTE_WAIT_MS);

    const fetchOnce = async (): Promise<{ pending: number; marketOpen: boolean; quoteRefreshActive: boolean }> => {
      try {
        const res = await fetch(`/api/stocks/batch-price?symbols=${symbols.join(',')}`, {
          cache: 'no-store',
        });
        if (!res.ok) {
          return { pending: 0, marketOpen: lastMarketOpen, quoteRefreshActive: lastQuoteRefreshActive };
        }
        const json = (await res.json()) as {
          pending?: number;
          marketOpen?: boolean;
          quoteRefreshActive?: boolean;
          data: { symbol: string; price: number | null; change: number | null; changePct: number | null }[];
        };
        if (cancelled) {
          return { pending: 0, marketOpen: lastMarketOpen, quoteRefreshActive: lastQuoteRefreshActive };
        }
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
        const open = json.marketOpen ?? true;
        const refreshOn = json.quoteRefreshActive ?? open;
        lastMarketOpen = open;
        lastQuoteRefreshActive = refreshOn;
        setMarketOpen(open);
        return { pending: json.pending ?? 0, marketOpen: open, quoteRefreshActive: refreshOn };
      } catch {
        return { pending: 0, marketOpen: lastMarketOpen, quoteRefreshActive: lastQuoteRefreshActive };
      }
    };

    // Phase 1: aggressive polling until the first paint is fully populated.
    // Phase 2: gentle 30s loop while market is open. When closed, drop to a
    // very slow 5-min heartbeat — quotes are frozen anyway, no need to spin.
    const fastPoll = async (attempt = 0) => {
      if (cancelled) return;
      const { pending, quoteRefreshActive: refreshOn } = await fetchOnce();
      if (refreshOn && pending > 0 && attempt < 30) {
        const delay = attempt < 10 ? 2_000 : 8_000;
        timer = setTimeout(() => fastPoll(attempt + 1), delay);
      } else {
        markQuotesReady();
        const slowLoop = () => {
          if (cancelled) return;
          // 30s while Finnhub refresh window (incl. post-close settlement), 5min otherwise.
          const interval = lastQuoteRefreshActive ? 30_000 : 300_000;
          timer = setTimeout(async () => {
            await fetchOnce();
            slowLoop();
          }, interval);
        };
        slowLoop();
      }
    };
    fastPoll();

    // Re-poll immediately when the tab regains focus — covers the
    // "I clicked into a ticker, came back, dashboard is stale" case.
    const onVisible = () => {
      if (document.visibilityState === 'visible' && !cancelled) {
        fetchOnce();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (initialReadyTimer) clearTimeout(initialReadyTimer);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [data, weekStartIso]);

  // Calendar columns always follow the selected week (URL offset), so the
  // header dates stay correct while JSON for that week is still loading.
  const days = useMemo(() => {
    const weekStart = parseLocalDate(weekStartIso);
    return Array.from({ length: 5 }, (_, i) => {
      const d = new Date(weekStart);
      d.setDate(weekStart.getDate() + i);
      return d;
    });
  }, [weekStartIso]);

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

  const dataWeekKey = data?.window?.start?.slice(0, 10) ?? null;
  const weekReady =
    !!data && !isFetching && !error && dataWeekKey === weekStartIso;

  const filteredEvents = useMemo(() => {
    if (!data) return [] as EarningsEvent[];
    let list = data.events;
    if (filter === 'popular') list = list.filter((e) => (POPULAR_WEIGHT[e.ticker] ?? 0) >= 76);
    if (filter === 'sp500') list = list.filter((e) => SP500_SET.has(e.ticker));
    if (filter === 'movers') list = list.filter((e) => (e.em_straddle_pct ?? e.em_iv_pct ?? 0) >= 0.10);
    if (search) list = list.filter((e) => e.ticker.startsWith(search));
    return list;
  }, [data, filter, search]);

  // Preload logos for the entire week's events, not just the currently
  // visible filter slice. Keying readiness on the week (not the filter)
  // means switching Popular ↔ S&P 500 ↔ Movers ↔ All is instant — no
  // skeleton flash, no second preload pass when a filter exposes tickers
  // that weren't in the previous slice.
  const allTickerKey = useMemo(() => {
    if (!data) return '';
    return Array.from(new Set(data.events.map((e) => e.ticker))).join('|');
  }, [data]);

  useEffect(() => {
    if (!weekReady) {
      setLogosReadyKey(null);
      return;
    }

    const tickers = allTickerKey ? allTickerKey.split('|') : [];
    const uncached = tickers.filter((ticker) => !hasTickerLogoState(ticker));
    if (uncached.length === 0) {
      setLogosReadyKey(weekStartIso);
      return;
    }

    let cancelled = false;
    setLogosReadyKey(null);
    void preloadTickerLogos(uncached, LOGO_PRELOAD_TIMEOUT_MS).then(() => {
      if (!cancelled) setLogosReadyKey(weekStartIso);
    });

    return () => {
      cancelled = true;
    };
  }, [allTickerKey, weekReady, weekStartIso]);

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
  // All three gates have to clear before the real rows render. The whole
  // calendar stays on the skeleton until the week's JSON is fetched,
  // logos have preloaded (or timed out), the first quote batch is back
  // (or the wait-bound fired), and the min-loading hold has elapsed.
  // Trades a slower first paint for a more "ready" look.
  const contentReady =
    weekReady &&
    minLoadingDoneWeek === weekStartIso &&
    quotesReadyWeek === weekStartIso &&
    logosReadyKey === weekStartIso;
  const showSkeleton = !error && !contentReady;

  return (
    <>
      <WeekHeader
        offset={offset}
        setOffset={setOffset}
        windowLabel={winLabel}
        filter={filter}
        setFilter={(f) => startTransition(() => setFilter(f))}
        search={search}
        setSearch={setSearch}
        marketOpen={marketOpen}
      />

      {error && (
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

      {/* Fixed shell height keeps the Suspense fallback, in-component
          skeleton, and loaded calendar at the same document position. */}
      <div className="qv-calendar-shell">
        {showSkeleton && !error && <CalendarGridSkeleton days={days} today={today} />}

        {contentReady && data && (
          <div
            className="qv-m-stack qv-calendar-grid"
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(5, minmax(0, 1fr))',
              marginTop: 20,
              opacity: isPending ? 0.88 : 1,
              transition: 'opacity 220ms ease',
              animation: 'earnings-grid-fade-in 0.22s ease-out',
            }}
          >
            {days.map((d, i) => (
              <div
                key={d.toISOString()}
                className="qv-calendar-day"
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
      </div>

      {(contentReady || showSkeleton) && !error && (
        <div
          style={{
            marginTop: 40,
            padding: '16px 0',
            borderTop: '1px solid var(--line)',
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: 11,
            color: 'var(--ink-4)',
            minHeight: 36,
          }}
        >
          {contentReady && data ? (
            <span className="mono">
              {filteredEvents.length} reports · {data.metadata.method} · as of {data.metadata.as_of_date}
            </span>
          ) : (
            <span className="mono" style={{ color: 'var(--ink-3)' }}>
              Updating calendar…
            </span>
          )}
        </div>
      )}
    </>
  );
}
