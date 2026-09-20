'use client';

import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from 'react';
import Link from 'next/link';
import { Sun, Moon, Info, ChevronLeft, ChevronRight, Search, Circle, Clock } from 'lucide-react';
import { POPULAR_WEIGHT } from '@/lib/popular';
import { companyName } from '@/lib/companyNames';
import { useEnsureCompanyNames } from '@/lib/useCompanyNames';
import { useTickerHover } from '@/components/TickerHoverCard';
import { CalendarGridSkeleton } from '@/components/EarningsGridSkeleton';
import { useFitNowrapText } from '@/lib/useFitNowrapText';
import {
  hasTickerLogoState,
  preloadTickerLogos,
  TickerLogo,
} from '@/components/TickerLogo';
import {
  resolveEarningsReactionDisplay,
  shouldFetchRealizedBackfill,
  shouldPollLiveQuote,
} from '@/lib/earningsReaction';
import {
  hasWeekCache,
  primeWeekMemory,
  readLiveQuoteCache,
  readWeekCache,
  writeLiveQuoteCache,
  writeWeekCache,
  type LiveQuoteMap,
} from '@/lib/earningsCalendarCache';
import { parseHomeSearchParams, type HomeCalendarFilter } from '@/lib/homeSearchParams';
import { earningsQuoteRequestUrl } from '@/lib/earningsQuoteRequest';
import {
  calendarCacheKey,
  mergeCalendarReference,
  type CalendarReference,
} from '@/lib/calendarReference';
import {
  displayForecastLabel,
  finiteDisplayForecast,
  resolveDisplayForecastCompat,
  type DisplayForecastFields,
  type DisplayForecastMethod,
} from '@/lib/displayForecast';
import forecastStyles from './DisplayForecast.module.css';
import sp500Constituents from '../../../lib/data/sp500-constituents.json';

const SP500_SET: Set<string> = new Set(
  (sp500Constituents as { symbol: string }[]).map((c) => c.symbol),
);

interface EarningsEvent extends DisplayForecastFields {
  ticker: string;
  earnings_date: string;
  timing: string;
  em_straddle_pct?: number | null;
  em_iv_pct?: number | null;
  em_ml_pct?: number | null;
  p25?: number | null;
  p75?: number | null;
  realized_move_pct?: number | null;
}

export interface WeeklyData {
  metadata: {
    as_of_date: string;
    method: string;
    offset?: number;
    calendar_reference_release_id?: string | null;
    calendar_reference_receipt_id?: string | null;
    calendar_reference_observed_at?: string | null;
    research_as_of_date?: string | null;
  };
  window: { start: string; end: string };
  events: EarningsEvent[];
}

type Filter = HomeCalendarFilter;

const MIN_OFFSET = -1;
const MAX_OFFSET = 2;
const MIN_GRID_LOADING_MS = 750;
const LOGO_PRELOAD_TIMEOUT_MS = 1_500;
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
function fmtMovePct(v: number | null | undefined, digits = 1) {
  if (v == null) return null;
  return `${(Math.abs(v) * 100).toFixed(digits)}%`;
}

function legacyForecastMethod(ev: EarningsEvent): DisplayForecastMethod | null {
  if (ev.em_ml_pct != null) return 'ml';
  if (ev.em_iv_pct != null || ev.em_straddle_pct != null) return 'options_math';
  return null;
}

function forecastClass(method: DisplayForecastMethod | null): string {
  if (method === 'ml') return `${forecastStyles.base} ${forecastStyles.ml}`;
  if (method === 'options_math') return `${forecastStyles.base} ${forecastStyles.options}`;
  if (method === 'options_indicative') return `${forecastStyles.base} ${forecastStyles.indicative}`;
  if (method === 'historical') return `${forecastStyles.base} ${forecastStyles.historical}`;
  if (method === 'historical_prior') return `${forecastStyles.base} ${forecastStyles.prior}`;
  return forecastStyles.base;
}

function TooltipLine({ label, value, muted = false, marginTop = 0 }: { label: string; value: string; muted?: boolean; marginTop?: number }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginTop }}>
      <span style={{ color: 'var(--ink-3)', fontSize: 10 }}>{label}</span>
      <span className="mono tnum" style={{ color: muted ? 'var(--ink-4)' : 'var(--ink)' }}>
        {value}
      </span>
    </div>
  );
}

/** Compact provenance surface for the calendar headline expected move. */
function ExpectedMoveHover({
  movePct,
  ivPct,
  straddlePct,
  mlPct,
  method,
  bandLo,
  bandHi,
  children,
}: {
  movePct: number | null;
  ivPct: number | null;
  straddlePct: number | null;
  mlPct: number | null;
  method: DisplayForecastMethod | null;
  bandLo: number | null | undefined;
  bandHi: number | null | undefined;
  children: React.ReactNode;
}) {
  const [show, setShow] = useState(false);
  const timerRef = useRef<number | null>(null);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  useEffect(() => () => clearTimer(), []);

  const moveLine = fmtMovePct(movePct);
  const ivLine = fmtMovePct(ivPct);
  const straddleLine = fmtMovePct(straddlePct);
  const mlLine = fmtMovePct(mlPct);
  const bandLine =
    method === 'ml' && bandLo != null && bandHi != null
      ? `${fmtMovePct(bandLo)}–${fmtMovePct(bandHi)}`
      : null;
  const hasTooltip = moveLine != null;

  return (
    <div
      data-calendar-move
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        flexShrink: 0,
        whiteSpace: 'nowrap',
        lineHeight: 1.1,
      }}
      onMouseEnter={() => {
        if (!hasTooltip) return;
        clearTimer();
        timerRef.current = window.setTimeout(() => setShow(true), 400);
      }}
      onMouseLeave={() => {
        clearTimer();
        setShow(false);
      }}
      onFocus={() => {
        if (!hasTooltip) return;
        setShow(true);
      }}
      onBlur={() => setShow(false)}
    >
      {children}
      {show && hasTooltip && (
        <div
          role="tooltip"
          aria-label="Expected move breakdown"
          style={{
            position: 'absolute',
            right: 0,
            top: 'calc(100% + 6px)',
            zIndex: 60,
            padding: '8px 10px',
            background: 'linear-gradient(180deg, var(--bg-3), var(--bg-2))',
            border: '1px solid var(--line-2)',
            borderRadius: 8,
            boxShadow: '0 12px 32px rgba(0,0,0,0.55)',
            minWidth: 188,
            fontSize: 11,
            lineHeight: 1.45,
            color: 'var(--ink-2)',
            textAlign: 'right',
            letterSpacing: 0,
            textTransform: 'none',
            fontWeight: 400,
            animation: 'qv-hover-pop 160ms cubic-bezier(.2,.8,.3,1) both',
            pointerEvents: 'none',
          }}
        >
          {mlLine ? (
            <TooltipLine label="ML forecast" value={`±${mlLine}`} />
          ) : ivLine ? (
            <TooltipLine label="IV forecast" value={`±${ivLine}`} />
          ) : straddleLine ? (
            <TooltipLine label="Straddle implied" value={`±${straddleLine}`} />
          ) : method === 'historical' && moveLine ? (
            <TooltipLine label="Historical median" value={`±${moveLine}`} />
          ) : method === 'historical_prior' && moveLine ? (
            <TooltipLine label="Historical prior" value={`±${moveLine}`} />
          ) : moveLine ? (
            <TooltipLine label={displayForecastLabel(method)} value={`±${moveLine}`} />
          ) : null}
          {mlLine && ivLine && (
            <TooltipLine label="IV forecast" value={`±${ivLine}`} marginTop={4} />
          )}
          {straddleLine && (
            <TooltipLine label="Straddle implied" value={`±${straddleLine}`} marginTop={4} />
          )}
          {!ivLine && !straddleLine && (method === 'historical' || method === 'historical_prior') && (
            <TooltipLine label="IV forecast" value="Unavailable" muted marginTop={4} />
          )}
          {!mlLine && method !== 'ml' && (
            <TooltipLine label="ML forecast" value="Unavailable" muted marginTop={4} />
          )}
          {bandLine != null && (
            <TooltipLine label="Typical" value={bandLine} marginTop={4} />
          )}
        </div>
      )}
    </div>
  );
}

function TickerRow({
  ev,
  live,
}: {
  ev: EarningsEvent;
  live?: LiveMap[string];
}) {
  const ivPct = ev.em_iv_pct ?? null;
  const straddlePct = ev.em_straddle_pct ?? null;
  const mlPct = ev.em_ml_pct ?? null;
  const resolvedForecast = resolveDisplayForecastCompat(ev);
  const method = resolvedForecast.method ?? legacyForecastMethod(ev);
  const movePct = resolvedForecast.pct;
  const bandLo = method === 'ml' ? ev.p25 : null;
  const bandHi = method === 'ml' ? ev.p75 : null;
  const realizedFromBackfill =
    live?.realizedDate && live.realizedDate === ev.earnings_date
      ? live.realizedMovePct ?? null
      : null;
  const reaction = resolveEarningsReactionDisplay({
    earningsDate: ev.earnings_date,
    timing: ev.timing,
    realizedMovePct: ev.realized_move_pct ?? realizedFromBackfill,
    liveChangePct: live?.changePct ?? null,
  });
  const changePct = reaction.changePct;
  const pctRounded = changePct !== null ? Math.round(changePct * 10000) / 10000 : null;
  const flat = pctRounded === 0;
  const up = !flat && (changePct ?? 0) >= 0;
  const arrow = flat ? '–' : up ? '▲' : '▼';
  const color = flat ? 'var(--ink-4)' : up ? 'var(--up)' : 'var(--down)';
  const moveTag = reaction.tag;
  const hover = useTickerHover(ev.ticker);
  return (
    <Link
      href={`/${ev.ticker}`}
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(0, 1fr) auto',
        alignItems: 'center',
        gap: 8,
        padding: '7px 8px',
        borderRadius: 6,
        textDecoration: 'none',
        transition: 'background 120ms ease',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = 'var(--bg-3)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent';
      }}
    >
      <div
        data-calendar-identity
        style={{ display: 'grid', gridTemplateColumns: '24px minmax(0, 1fr)', alignItems: 'center', gap: 8, minWidth: 0 }}
        {...hover}
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
              {moveTag && (
                <span
                  style={{
                    color: 'var(--ink-4)',
                    fontSize: 8.5,
                    letterSpacing: '0.08em',
                    textTransform: 'uppercase',
                    marginLeft: 4,
                  }}
                >
                  {moveTag}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
      <ExpectedMoveHover
        movePct={movePct}
        ivPct={ivPct}
        straddlePct={straddlePct}
        mlPct={mlPct}
        method={method}
        bandLo={bandLo}
        bandHi={bandHi}
      >
        <div className={`serif tnum ${forecastClass(method)}`}>
          {movePct != null ? (
            <>
              ±{(movePct * 100).toFixed(1)}
              <span style={{ fontSize: 9, color: 'currentColor', opacity: 0.72, marginLeft: 1 }}>%</span>
            </>
          ) : (
            <span style={{ fontSize: 11, color: 'var(--ink-4)' }}>—</span>
          )}
        </div>
        {bandLo != null && bandHi != null && (
          <div
            className="mono tnum"
            style={{ fontSize: 8.5, color: 'var(--ink-4)', letterSpacing: '0.02em', marginTop: 1 }}
          >
            {(bandLo * 100).toFixed(1)}–{(bandHi * 100).toFixed(1)}%
          </div>
        )}
      </ExpectedMoveHover>
    </Link>
  );
}

type LiveMap = LiveQuoteMap;

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
    movers: 'Tickers whose expected move is ≥ 10% this week.',
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
  const headingRef = useFitNowrapText<HTMLHeadingElement>(windowLabel, 56, 32);
  return (
    <div style={{ padding: '24px 0 20px', borderBottom: '1px solid var(--line)' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-end',
          gap: 24,
        }}
        className="qv-week-header-row"
      >
        <div className="qv-week-heading-col">
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
              minHeight: 22,
            }}
          >
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
            ref={headingRef}
            className="serif qv-m-h1 qv-week-heading"
            style={{
              margin: 0,
              fontWeight: 800,
              letterSpacing: '-0.032em',
              lineHeight: 0.94,
              color: 'var(--ink)',
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

        <div
          className="qv-week-picker"
          style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}
        >
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

export default function EarningsGrid({
  initialOffset = 0,
  initialFilter = 'popular',
  initialData = null,
}: {
  initialOffset?: number;
  initialFilter?: Filter;
  initialData?: WeeklyData | null;
}) {
  useEnsureCompanyNames();

  const [offset, setOffset] = useState(initialOffset);
  const [filter, setFilter] = useState<Filter>(initialFilter);
  const [urlStateReady, setUrlStateReady] = useState(false);
  const [search, setSearch] = useState('');

  useEffect(() => {
    const syncFromUrl = () => {
      const params = new URLSearchParams(window.location.search);
      const next = parseHomeSearchParams({
        offset: params.get('offset') ?? undefined,
        filter: params.get('filter') ?? undefined,
      });
      setOffset(next.initialOffset);
      setFilter(next.initialFilter);
      setUrlStateReady(true);
    };

    syncFromUrl();
    window.addEventListener('popstate', syncFromUrl);
    return () => window.removeEventListener('popstate', syncFromUrl);
  }, []);

  useEffect(() => {
    if (!urlStateReady) return;
    const next = new URLSearchParams();
    if (offset !== 0) next.set('offset', String(offset));
    if (filter !== 'popular') next.set('filter', filter);
    const qs = next.toString();
    const url = qs ? `/?${qs}` : '/';
    if (`${window.location.pathname}${window.location.search}` !== url) {
      window.history.replaceState(window.history.state, '', url);
    }
  }, [offset, filter, urlStateReady]);
  const initialWeekIso = (() => {
    const d = mondayOf(new Date());
    d.setDate(d.getDate() + 7 * initialOffset);
    return isoDay(d);
  })();
  const ssrSeedIso = initialData?.window?.start?.slice(0, 10) ?? null;
  const initialCalendarReleaseId =
    typeof initialData?.metadata.calendar_reference_release_id === 'string'
      ? initialData.metadata.calendar_reference_release_id
      : null;
  const [calendarReleaseId, setCalendarReleaseId] = useState<string | null>(
    initialCalendarReleaseId,
  );
  const initialWeekCacheKey = calendarCacheKey(initialCalendarReleaseId, initialWeekIso);
  const ssrSeedCacheKey = ssrSeedIso
    ? calendarCacheKey(initialCalendarReleaseId, ssrSeedIso)
    : null;
  if (ssrSeedCacheKey && initialData) primeWeekMemory(ssrSeedCacheKey, initialData);
  const warmStart = hasWeekCache(initialWeekCacheKey);
  const [data, setData] = useState<WeeklyData | null>(
    () => readWeekCache<WeeklyData>(initialWeekCacheKey),
  );
  const [isFetching, setIsFetching] = useState(() => !warmStart);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<LiveMap>(() => readLiveQuoteCache());
  const [marketOpen, setMarketOpen] = useState<boolean>(true);
  const [minLoadingDoneWeek, setMinLoadingDoneWeek] = useState<string | null>(
    () => (warmStart ? initialWeekIso : null),
  );
  const [quotesReadyWeek, setQuotesReadyWeek] = useState<string | null>(
    () => (warmStart ? initialWeekIso : null),
  );
  const [isPending, startTransition] = useTransition();

  const thisMonday = useMemo(() => mondayOf(new Date()), []);
  const weekStartIso = useMemo(() => {
    const d = new Date(thisMonday);
    d.setDate(d.getDate() + 7 * offset);
    return isoDay(d);
  }, [thisMonday, offset]);
  const weekCacheKey = useMemo(
    () => calendarCacheKey(calendarReleaseId, weekStartIso),
    [calendarReleaseId, weekStartIso],
  );

  useEffect(() => {
    if (hasWeekCache(weekCacheKey)) {
      setMinLoadingDoneWeek(weekStartIso);
      return;
    }
    setMinLoadingDoneWeek(null);
    const timeoutId = window.setTimeout(() => {
      setMinLoadingDoneWeek(weekStartIso);
    }, MIN_GRID_LOADING_MS);
    return () => window.clearTimeout(timeoutId);
  }, [weekCacheKey, weekStartIso]);

  const fetchWeek = useCallback(
    async (iso: string) => {
      const urls = [`/weeks/${iso}.json`, offset === 0 ? '/weekly.json' : null].filter(
        Boolean,
      ) as string[];
      let research: WeeklyData | null = null;
      for (const url of urls) {
        // Forecast methodology can change while the event identity stays the same.
        // Do not let an hour-old stale-while-revalidate week payload overwrite a
        // freshly deployed SSR snapshot with obsolete historical fallbacks.
        const res = await fetch(url, { cache: 'no-store' });
        if (res.ok) {
          research = (await res.json()) as WeeklyData;
          break;
        }
      }
      if (!research) throw new Error(`no data for ${iso}`);

      let reference: CalendarReference | null = null;
      try {
        const response = await fetch('/calendar-reference.json', { cache: 'no-store' });
        if (response.ok) reference = (await response.json()) as CalendarReference;
      } catch {
        // Retained research stays visible until the independent calendar is readable.
      }
      const merged = mergeCalendarReference(reference, research, iso);
      return { data: merged, releaseId: reference?.release_id ?? null };
    },
    [offset],
  );

  useEffect(() => {
    let cancelled = false;
    const cached = readWeekCache<WeeklyData>(weekCacheKey);
    if (cached) {
      setData(cached);
      setIsFetching(false);
    } else {
      setIsFetching(true);
    }
    setError(null);
    fetchWeek(weekStartIso)
      .then(({ data: json, releaseId }) => {
        if (cancelled) return;
        const nextCacheKey = calendarCacheKey(releaseId, weekStartIso);
        writeWeekCache(nextCacheKey, json);
        setCalendarReleaseId(releaseId);
        setData(json);
      })
      .catch((e) => {
        if (!cancelled && !hasWeekCache(weekCacheKey)) setError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setIsFetching(false);
      });
    return () => {
      cancelled = true;
    };
  }, [weekStartIso, weekCacheKey, fetchWeek]);

  useEffect(() => {
    writeLiveQuoteCache(live);
  }, [live]);

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
    const symbols = Array.from(
      new Set(
        data.events
          .filter(
            (e) =>
              shouldPollLiveQuote(e.earnings_date, e.timing, e.realized_move_pct) ||
              shouldFetchRealizedBackfill(e.earnings_date, e.timing, e.realized_move_pct),
          )
          .map((e) => e.ticker),
      ),
    );

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

    const quoteRequestUrl = earningsQuoteRequestUrl(symbols);
    if (quoteRequestUrl === null) {
      markQuotesReady();
      return () => {
        cancelled = true;
      };
    }

    if (!hasWeekCache(weekCacheKey)) {
      setQuotesReadyWeek(null);
      initialReadyTimer = setTimeout(markQuotesReady, 3_000);
    }

    const fetchOnce = async (): Promise<{ pending: number; marketOpen: boolean; quoteRefreshActive: boolean }> => {
      try {
        const res = await fetch(quoteRequestUrl, {
          cache: 'no-store',
        });
        if (!res.ok) {
          return { pending: 0, marketOpen: lastMarketOpen, quoteRefreshActive: lastQuoteRefreshActive };
        }
        const json = (await res.json()) as {
          pending?: number;
          marketOpen?: boolean;
          quoteRefreshActive?: boolean;
          data: {
            symbol: string;
            price: number | null;
            change: number | null;
            changePct: number | null;
            realizedMovePct?: number | null;
            realizedDate?: string | null;
          }[];
        };
        if (cancelled) {
          return { pending: 0, marketOpen: lastMarketOpen, quoteRefreshActive: lastQuoteRefreshActive };
        }
        setLive((prev) => {
          const next: LiveMap = { ...prev };
          for (const t of json.data) {
            if (t.price === null && t.realizedMovePct == null) continue;
            const prevEntry = next[t.symbol];
            next[t.symbol] = {
              change: t.price !== null ? t.change : prevEntry?.change ?? null,
              changePct: t.price !== null ? t.changePct : prevEntry?.changePct ?? null,
              realizedMovePct: t.realizedMovePct ?? prevEntry?.realizedMovePct ?? null,
              realizedDate: t.realizedDate ?? prevEntry?.realizedDate ?? null,
            };
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

    const fastPoll = async (attempt = 0) => {
      if (cancelled) return;
      if (document.visibilityState !== 'visible') {
        timer = setTimeout(() => fastPoll(attempt), 30_000);
        return;
      }
      const { pending, quoteRefreshActive: refreshOn } = await fetchOnce();
      markQuotesReady();
      if (refreshOn && pending > 0 && attempt < 30) {
        const delay = attempt < 10 ? 2_000 : 8_000;
        timer = setTimeout(() => fastPoll(attempt + 1), delay);
      } else {
        const slowLoop = () => {
          if (cancelled) return;
          const interval = lastQuoteRefreshActive ? 30_000 : 300_000;
          timer = setTimeout(async () => {
            if (document.visibilityState === 'visible') {
              await fetchOnce();
            }
            slowLoop();
          }, interval);
        };
        slowLoop();
      }
    };
    fastPoll();

    const onVisible = () => {
      if (document.visibilityState === 'visible' && !cancelled) {
        fetchOnce();
      }
    };
    const onPageShow = () => {
      if (!cancelled) fetchOnce();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    window.addEventListener('pageshow', onPageShow);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (initialReadyTimer) clearTimeout(initialReadyTimer);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
      window.removeEventListener('pageshow', onPageShow);
    };
  }, [data, weekCacheKey, weekStartIso]);

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
    if (filter === 'movers') {
      list = list.filter((e) => (resolveDisplayForecastCompat(e).pct ?? 0) >= 0.10);
    }
    if (search) list = list.filter((e) => e.ticker.startsWith(search));
    return list;
  }, [data, filter, search]);

  const allTickerKey = useMemo(() => {
    if (!data) return '';
    return Array.from(new Set(data.events.map((e) => e.ticker))).join('|');
  }, [data]);

  useEffect(() => {
    if (!weekReady) {
      return;
    }

    const tickers = allTickerKey ? allTickerKey.split('|') : [];
    const uncached = tickers.filter((ticker) => !hasTickerLogoState(ticker));
    if (uncached.length === 0) {
      return;
    }

    let cancelled = false;
    void preloadTickerLogos(uncached, LOGO_PRELOAD_TIMEOUT_MS).then(() => {
      if (cancelled) return;
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
  const contentReady =
    weekReady &&
    minLoadingDoneWeek === weekStartIso &&
    (quotesReadyWeek === weekStartIso ||
      (hasWeekCache(weekCacheKey) && Object.keys(live).length > 0));
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
              {filteredEvents.length} reports · calendar {data.metadata.calendar_reference_observed_at?.slice(0, 10) ?? 'retained'} · research as of {data.metadata.research_as_of_date ?? data.metadata.as_of_date}
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
