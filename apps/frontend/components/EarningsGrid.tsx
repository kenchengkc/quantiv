'use client';

import { useState, useEffect, useMemo } from 'react';
import Link from 'next/link';
import { Sun, Moon, Flag, Filter, ChevronLeft, ChevronRight } from 'lucide-react';

interface EarningsEvent {
  ticker: string;
  earnings_date: string;
  timing: string;
  fiscal_q?: string;
  spot_price?: number | null;
  atm_iv?: number | null;
  em_straddle_pct?: number | null;
  em_iv_pct?: number | null;
  em_straddle_abs?: number | null;
  days_to_expiry?: number;
  lead_time_days?: number;
}

interface WeeklyData {
  metadata: {
    version: string;
    generated_at: string;
    as_of_date: string;
    method: string;
  };
  window: { start: string; end: string };
  events: EarningsEvent[];
  summary?: {
    total_events: number;
    avg_em_straddle_pct: number;
    avg_em_iv_pct: number;
  };
}

const POPULAR_WEIGHT: Record<string, number> = {
  AAPL: 100, TSLA: 100, NVDA: 100, MSFT: 95, AMZN: 95, META: 95, GOOGL: 95,
  JPM: 90, BAC: 85, UNH: 90, JNJ: 88, V: 85, MA: 85, PG: 85, KO: 80,
  NFLX: 90, IBM: 85, INTC: 85, AMD: 85, BA: 85, GE: 85, T: 80, VZ: 80,
  XOM: 85, CVX: 85, WMT: 85, HD: 80, DIS: 85, NKE: 80, MCD: 80, SBUX: 78,
  COST: 82, CRM: 82, ORCL: 82, CSCO: 80, QCOM: 80, AVGO: 85, AMAT: 78,
  AXP: 80, GS: 82, MS: 82, C: 82, WFC: 82, PYPL: 78, SQ: 75,
  UNP: 78, LMT: 80, RTX: 80, CAT: 78, DE: 76, HON: 78, MMM: 75,
  TMO: 80, ABT: 80, LLY: 85, PFE: 80, MRK: 80, NEE: 76, DUK: 74,
  SLB: 78, HAL: 75, EOG: 74, COP: 76, KMI: 72, PSX: 72,
  NOW: 82, ADBE: 85, SNOW: 78, PLTR: 80, COIN: 80, SHOP: 76,
  DHR: 75, DHI: 72, EFX: 72, TSCO: 72, SYF: 72, MCO: 76, MSCI: 74,
  NOC: 75, BSX: 74, ELV: 74, CME: 74, LRCX: 76, TXN: 78, QS: 70,
  LUV: 72, AAL: 72, UAL: 74, DAL: 74, CCI: 72, CMCSA: 78,
  CHTR: 75, HCA: 75, VRT: 72, GEV: 74, BX: 80, EW: 74, FIX: 70,
  DLR: 74, FCX: 75, BKR: 72, KNSL: 68, NDAQ: 74, NEM: 72, EQT: 70,
  ADC: 68, CB: 78, COF: 78, PM: 80, UHG: 88, IBKR: 72, ISRG: 78,
};

function timingKey(timing: string): 'bmo' | 'amc' | 'other' {
  const t = (timing || '').toLowerCase();
  if (t === 'bmo' || t === 'before_market_open' || t === 'before_open') return 'bmo';
  if (t === 'amc' || t === 'after_market_close' || t === 'after_close') return 'amc';
  return 'other';
}

function logoUrl(ticker: string): string {
  return `https://assets.parqet.com/logos/symbol/${ticker}?format=png`;
}

function TickerTile({ event }: { event: EarningsEvent }) {
  const [imgError, setImgError] = useState(false);
  const movePct = event.em_straddle_pct ?? event.em_iv_pct;
  const moveLabel = typeof movePct === 'number' ? `±${(movePct * 100).toFixed(1)}%` : null;

  return (
    <Link
      href={`/${event.ticker}`}
      className="group flex flex-col items-center gap-1.5"
      title={
        moveLabel
          ? `${event.ticker} — expected move ${moveLabel}`
          : event.ticker
      }
    >
      <div className="relative w-14 h-14 rounded-xl overflow-hidden bg-gray-800 ring-1 ring-white/10 group-hover:ring-emerald-400/60 group-hover:scale-105 transition-all">
        {!imgError ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={logoUrl(event.ticker)}
            alt={event.ticker}
            className="w-full h-full object-cover"
            onError={() => setImgError(true)}
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-blue-900 to-gray-800 text-white text-xs font-bold">
            {event.ticker.slice(0, 4)}
          </div>
        )}
      </div>
      <div className="flex flex-col items-center leading-tight">
        <span className="text-xs font-semibold text-gray-200 group-hover:text-white">
          {event.ticker}
        </span>
        {moveLabel && (
          <span className="text-[10px] font-medium text-emerald-400/90">
            {moveLabel}
          </span>
        )}
      </div>
    </Link>
  );
}

function DayColumn({
  dayLabel,
  dateLabel,
  events,
  isToday,
  popularOnly,
}: {
  dayLabel: string;
  dateLabel: string;
  events: EarningsEvent[];
  isToday: boolean;
  popularOnly: boolean;
}) {
  const filtered = useMemo(() => {
    let list = events;
    if (popularOnly) {
      list = list.filter((e) => (POPULAR_WEIGHT[e.ticker] ?? 0) >= 68);
    }
    list = [...list].sort(
      (a, b) =>
        (POPULAR_WEIGHT[b.ticker] ?? 0) - (POPULAR_WEIGHT[a.ticker] ?? 0)
    );
    return list;
  }, [events, popularOnly]);

  const beforeOpen = filtered.filter((e) => timingKey(e.timing) === 'bmo');
  const afterClose = filtered.filter((e) => timingKey(e.timing) === 'amc');
  const other = filtered.filter((e) => timingKey(e.timing) === 'other');

  return (
    <div className="flex-1 min-w-[180px]">
      <div className="mb-3 flex items-center justify-center">
        <span
          className={`text-sm font-semibold px-3 py-1 rounded-full ${
            isToday
              ? 'bg-emerald-500 text-black'
              : 'text-gray-300'
          }`}
        >
          {dayLabel} {dateLabel}
        </span>
      </div>

      <div className="space-y-4">
        {beforeOpen.length > 0 && (
          <Section title="Before Open" icon={<Sun className="w-3.5 h-3.5" />} events={beforeOpen} />
        )}
        {afterClose.length > 0 && (
          <Section title="After Close" icon={<Moon className="w-3.5 h-3.5" />} events={afterClose} />
        )}
        {other.length > 0 && (
          <Section title="Other" icon={<Flag className="w-3.5 h-3.5" />} events={other} />
        )}
        {filtered.length === 0 && (
          <div className="rounded-xl bg-gray-900/40 border border-white/5 p-4 text-center text-xs text-gray-500">
            No popular earnings
          </div>
        )}
      </div>
    </div>
  );
}

function Section({
  title,
  icon,
  events,
}: {
  title: string;
  icon: React.ReactNode;
  events: EarningsEvent[];
}) {
  return (
    <div className="rounded-xl bg-gray-900/60 border border-white/5 p-3">
      <div className="flex items-center gap-1.5 mb-3 text-gray-400 text-xs font-medium">
        {icon}
        <span>{title}</span>
        <span className="ml-auto text-gray-500">{events.length}</span>
      </div>
      <div className="grid grid-cols-3 gap-y-3 gap-x-1">
        {events.map((e) => (
          <TickerTile key={`${e.ticker}-${e.earnings_date}`} event={e} />
        ))}
      </div>
    </div>
  );
}

function formatWindow(start: string, end: string) {
  const s = new Date(start + 'T00:00:00');
  const e = new Date(end + 'T00:00:00');
  const m = (d: Date) => d.toLocaleDateString('en-US', { month: 'short' }).toUpperCase();
  return `${m(s)} ${s.getDate()} - ${e.getDate()}`;
}

export default function EarningsGrid() {
  const [data, setData] = useState<WeeklyData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [popularOnly, setPopularOnly] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch('/weekly.json', { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
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
  }, []);

  if (loading) {
    return (
      <div className="flex justify-center items-center py-20 text-gray-400 text-sm">
        Loading earnings calendar…
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-6 text-center text-red-300 text-sm">
        Couldn&apos;t load weekly data. {error}
      </div>
    );
  }

  const weekStart = new Date(data.window.start + 'T00:00:00');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Array.from({ length: 5 }, (_, i) => {
    const d = new Date(weekStart);
    d.setDate(weekStart.getDate() + i);
    return d;
  });

  const dayLabels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];

  const eventsByDay = days.map((day) => {
    const iso = day.toISOString().slice(0, 10);
    return data.events.filter((e) => e.earnings_date.slice(0, 10) === iso);
  });

  return (
    <div className="w-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between mb-6 px-1">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <button
              className="w-9 h-9 rounded-full bg-gray-800 hover:bg-gray-700 flex items-center justify-center text-gray-300"
              aria-label="Previous week"
              disabled
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <div className="w-9 h-9 rounded-full bg-gray-800 flex flex-col items-center justify-center text-[10px] text-gray-400 leading-none">
              <span>{weekStart.toLocaleDateString('en-US', { weekday: 'short' })[0]}</span>
              <span className="text-xs text-white font-semibold">{weekStart.getDate()}</span>
            </div>
            <button
              className="w-9 h-9 rounded-full bg-gray-800 hover:bg-gray-700 flex items-center justify-center text-gray-300"
              aria-label="Next week"
              disabled
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
          <div className="ml-2">
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Earnings this week</div>
            <div className="text-sm font-semibold text-gray-200">
              {formatWindow(data.window.start, data.window.end)}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setPopularOnly((v) => !v)}
            className={`inline-flex items-center gap-1.5 px-3 h-9 rounded-full border text-sm transition-colors ${
              popularOnly
                ? 'bg-gray-800 border-white/10 text-white'
                : 'border-white/10 text-gray-400 hover:text-white'
            }`}
          >
            <Filter className="w-3.5 h-3.5" />
            {popularOnly ? 'Popular' : 'All'}
          </button>
        </div>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
        {days.map((day, i) => (
          <DayColumn
            key={day.toISOString()}
            dayLabel={dayLabels[i]}
            dateLabel={String(day.getDate())}
            events={eventsByDay[i]}
            isToday={day.getTime() === today.getTime()}
            popularOnly={popularOnly}
          />
        ))}
      </div>

      {/* Footer meta */}
      <div className="mt-8 text-center text-[11px] text-gray-500">
        As of {data.metadata.as_of_date} · {data.events.length} events ·
        {' '}
        {data.metadata.method}
      </div>
    </div>
  );
}
