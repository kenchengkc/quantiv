'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ChevronLeft } from 'lucide-react';
import { companyName } from '@/lib/companyNames';
import { normalizeForecastQuantiles } from '@/lib/forecastQuantiles';
import { listingExchangeLabel } from '@/lib/listingExchanges';
import { useEnsureCompanyNames } from '@/lib/useCompanyNames';
import { useEnsureListingExchanges } from '@/lib/useListingExchanges';
import { TickerLogo } from '@/components/TickerLogo';
import {
  DetailHero,
  KpiCard,
  ProviderSignalsPanel,
  SkeletonBlock,
  Toast,
  usePrevAppLocation,
} from './SymbolPageHeader';
import { buildTermRows, TermFan } from './ForecastPanels';
import { buildHistorySeries, GreeksPanel, HistoryBlock, medianAbsoluteHistoryMove } from './HistoryRiskPanels';
import ScenarioRiskPanel from './ScenarioRiskPanel';
import ResearchSnapshotRibbon from './ResearchSnapshotRibbon';
import MoveComparisonChart from './MoveComparisonChart';
import type {
  IntradaySeries,
  LivePredictionResponse,
  LivePredictionState,
  LivePrice,
  PredictionMode,
  SymbolDetail,
} from './symbolPageTypes';
import { parseLocalDate } from './symbolPageUtils';

function SymbolPageLoading({ symbol }: { symbol: string }) {
  const name = companyName(symbol);

  return (
    <div className="qv-m-pad qv-symbol-page-shell" style={{ maxWidth: 1100, margin: '0 auto', padding: '0 28px 80px' }}>
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
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 18,
              minWidth: 0,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
              <SkeletonBlock width={56} height={56} radius={10} />
              <div style={{ minWidth: 0, flex: 1 }}>
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
                  {name}
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
                  }}
                >
                  {symbol}
                </div>
              </div>
            </div>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'auto auto 1fr',
                alignItems: 'baseline',
                columnGap: 14,
                minHeight: 44,
              }}
            >
              <SkeletonBlock width={128} height={42} radius={7} />
              <SkeletonBlock width={118} height={15} radius={5} />
              <div style={{ justifySelf: 'end' }}>
                <SkeletonBlock width={156} height={12} radius={5} />
              </div>
            </div>

            <div>
              <SkeletonBlock height={42} radius={8} />
              <div
                style={{
                  marginTop: 12,
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: 16,
                  paddingTop: 8,
                  minHeight: 29,
                  borderTop: '1px solid color-mix(in oklab, var(--line) 70%, transparent)',
                }}
              >
                <SkeletonBlock width={214} height={12} radius={5} />
                <SkeletonBlock width={60} height={12} radius={5} />
              </div>
            </div>

            <SkeletonBlock width={168} height={34} radius={999} />
          </div>

          <div
            className="qv-detail-hero-right"
            style={{
              borderLeft: '1px solid color-mix(in oklab, var(--line) 60%, transparent)',
              paddingLeft: 28,
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'space-between',
              gap: 20,
              minWidth: 0,
            }}
          >
            <div>
              <SkeletonBlock width={154} height={28} radius={999} />
              <div style={{ marginTop: 18 }}>
                <SkeletonBlock width={82} height={12} />
                <div style={{ marginTop: 8 }}>
                  <SkeletonBlock width={148} height={62} radius={8} />
                </div>
                <div style={{ marginTop: 12 }}>
                  <SkeletonBlock width={132} height={13} />
                </div>
              </div>
            </div>
            <div
              style={{
                borderTop: '1px solid color-mix(in oklab, var(--line) 60%, transparent)',
                paddingTop: 18,
              }}
            >
              <SkeletonBlock width={168} height={12} />
              <div style={{ marginTop: 14 }}>
                <SkeletonBlock width={194} height={72} radius={10} />
              </div>
              <div style={{ marginTop: 30 }}>
                <SkeletonBlock width={210} height={14} />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div
        className="qv-m-2col"
        style={{
          marginTop: 22,
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 14,
        }}
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="qv-card-hi" style={{ minHeight: 132, padding: '18px 18px' }}>
            <SkeletonBlock width="48%" height={12} />
            <div style={{ marginTop: 18 }}>
              <SkeletonBlock width="72%" height={38} radius={8} />
            </div>
            <div style={{ marginTop: 20 }}>
              <SkeletonBlock width="100%" height={12} />
            </div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 24 }}>
        <SkeletonBlock height={360} radius={8} />
      </div>
    </div>
  );
}

const EMPTY_LIVE_PREDICTION: LivePredictionState = {
  status: 'idle',
  key: null,
  response: null,
  error: null,
  updatedAt: 0,
};

function initialSymbolDetail(value: unknown, symbol: string): SymbolDetail | null {
  if (!value || typeof value !== 'object') return null;
  const detail = value as Partial<SymbolDetail>;
  if (typeof detail.symbol !== 'string') return null;
  if (detail.symbol.toUpperCase() !== symbol) return null;
  if (typeof detail.as_of_date !== 'string') return null;
  if (!Array.isArray(detail.straddle_features)) return null;
  return detail as SymbolDetail;
}

function livePredictionUnavailableMessage(status: number | null): string {
  if (status === 404) {
    return 'No fresh feature snapshot is available for this event yet.';
  }
  if (status === 400 || status === 422) {
    return 'This snapshot cannot be re-scored from the latest stock price.';
  }
  return 'The spot-updated forecast is unavailable right now.';
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

type EarningsHistoryRow = NonNullable<SymbolDetail['earnings_history']>[number];

function quarterNumber(q?: string | null): number | null {
  const m = /^Q([1-4])$/i.exec((q ?? '').trim());
  return m ? Number(m[1]) : null;
}

function fullYear(year?: number | string | null): number | null {
  if (year == null) return null;
  const n = typeof year === 'number' ? year : Number(year);
  if (!Number.isFinite(n)) return null;
  return n < 100 ? 2000 + n : n;
}

function quarterFromRow(row?: EarningsHistoryRow | null): { q: number; year: number } | null {
  if (!row) return null;

  const fiscalQ = quarterNumber(row.fiscal_q);
  const fiscalYear = fullYear(row.fiscal_year);
  if (fiscalQ && fiscalYear) return { q: fiscalQ, year: fiscalYear };

  const m = /Q([1-4])\s*(\d{2,4})/i.exec(row.q ?? '');
  if (!m) return null;
  return { q: Number(m[1]), year: fullYear(m[2]) ?? 2000 + Number(m[2]) };
}

function incrementQuarter(q: { q: number; year: number }): {
  q: number;
  year: number;
} {
  if (q.q >= 4) return { q: 1, year: q.year + 1 };
  return { q: q.q + 1, year: q.year };
}

function formatQuarterLabel(q: { q: number; year: number }): string {
  return `Q${q.q} ${q.year}`;
}

function eventLabelFor(data: SymbolDetail, earningsDate: string | null): string {
  const history = data.earnings_history ?? [];
  const eventDate = earningsDate?.slice(0, 10) ?? null;

  if (eventDate) {
    const exact = history.find((row) => row.date.slice(0, 10) === eventDate);
    const exactQuarter = quarterFromRow(exact);
    if (exactQuarter) return `${formatQuarterLabel(exactQuarter)} Earnings`;
  }

  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const future = history
    .filter((row) => parseLocalDate(row.date).getTime() >= today)
    .sort((a, b) => parseLocalDate(a.date).getTime() - parseLocalDate(b.date).getTime())
    .find((row) => quarterFromRow(row));
  const futureQuarter = quarterFromRow(future);
  if (futureQuarter) return `${formatQuarterLabel(futureQuarter)} Earnings`;

  const latestBeforeEvent = history
    .filter((row) => !eventDate || row.date.slice(0, 10) < eventDate)
    .sort((a, b) => parseLocalDate(b.date).getTime() - parseLocalDate(a.date).getTime())
    .find((row) => quarterFromRow(row));
  const previousQuarter = quarterFromRow(latestBeforeEvent);
  if (previousQuarter) return `${formatQuarterLabel(incrementQuarter(previousQuarter))} Earnings`;

  return 'Upcoming earnings';
}

/** One-line summary of where the spot price came from. Distinguishes
 *  live regular-hours vs. last-close vs. extended-hours IEX so the hero
 *  is honest about what the user is looking at. */
function quoteSourceLabel(live: LivePrice | null, ticker: string, asOfDate: string): string {
  if (!live) return `Snapshot · ${asOfDate}`;
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

function Reveal({
  children,
  as = 'section',
  style,
  className,
}: {
  children: ReactNode;
  as?: 'section' | 'div';
  delay?: number;
  style?: CSSProperties;
  className?: string;
}) {
  const cls = `reveal in${className ? ' ' + className : ''}`;
  if (as === 'div') {
    return (
      <div className={cls} style={style}>
        {children}
      </div>
    );
  }
  return (
    <section className={cls} style={style}>
      {children}
    </section>
  );
}

// ---------- Page ----------
export default function SymbolPage({
  initialData = null,
  initialEvidence = null,
  initialSymbol,
}: {
  initialData?: unknown;
  initialEvidence?: unknown;
  initialSymbol?: string;
}) {
  // Triggers EDGAR ticker-names fetch + re-render so the header company
  // name resolves even when the symbol isn't in the S&P 500 or curated map.
  useEnsureCompanyNames();
  useEnsureListingExchanges();

  const params = useParams();
  const router = useRouter();
  const symbol = (initialSymbol ?? (params.symbol as string) ?? '').toUpperCase();
  const prevLoc = usePrevAppLocation();
  const seededData = initialSymbolDetail(initialData, symbol);

  const [data, setData] = useState<SymbolDetail | null>(() => seededData);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(() => seededData === null);
  const [live, setLive] = useState<LivePrice | null>(null);
  const [quoteReady, setQuoteReady] = useState(false);
  const [predictionMode, setPredictionMode] = useState<PredictionMode>('snapshot');
  const [livePrediction, setLivePrediction] = useState<LivePredictionState>(EMPTY_LIVE_PREDICTION);
  const [toast, setToast] = useState<{ msg: string; key: number } | null>(null);
  const lastPredictionFetchAtRef = useRef(0);
  const inFlightPredictionKeyRef = useRef<string | null>(null);
  // Intraday sparkline state. Bars come from /api/stocks/intraday which
  // wraps Alpaca's IEX feed; we cache aggressively server-side and
  // refresh once a minute client-side during the regular session.
  const [intraday, setIntraday] = useState<IntradaySeries | null>(null);

  // Fetch intraday bars + auto-refresh every 60s during regular hours
  // so the sparkline stays in sync with the live price tick above it.
  // The endpoint caches at the edge for 30s, so polling is cheap.
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | null = null;
    const load = async () => {
      try {
        const res = await fetch(`/api/stocks/intraday?symbol=${symbol}`, {
          cache: 'no-store',
        });
        if (!res.ok || cancelled) return;
        const json = (await res.json()) as {
          bars?: { t: string; c: number }[];
          previousClose?: number | null;
          asOf?: string | null;
          sessionDate?: string | null;
          isCurrentSession?: boolean;
        };
        if (cancelled) return;
        setIntraday({
          symbol,
          bars: Array.isArray(json.bars) ? json.bars : [],
          previousClose: json.previousClose ?? null,
          asOf: json.asOf ?? null,
          sessionDate: json.sessionDate ?? null,
          isCurrentSession: json.isCurrentSession ?? false,
        });
      } catch {
        if (!cancelled) {
          setIntraday({
            symbol,
            bars: [],
            previousClose: null,
            asOf: null,
            sessionDate: null,
            isCurrentSession: false,
          });
        }
      }
    };
    void load();
    // Poll once a minute; only active while the tab is visible to avoid
    // burning quota on backgrounded pages.
    intervalId = setInterval(() => {
      if (document.visibilityState === 'visible') void load();
    }, 60_000);
    const onVisible = () => {
      if (document.visibilityState === 'visible') void load();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    const seeded = initialSymbolDetail(initialData, symbol);
    if (seeded) {
      setData(seeded);
      setError(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`/symbols/${symbol}.json`);
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
  }, [initialData, symbol]);

  useEffect(() => {
    if (!symbol) return;
    setLive(null);
    setQuoteReady(false);
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let lastQuoteRefreshActive = true;
    const fetchOnce = async (): Promise<number> => {
      try {
        const res = await fetch(`/api/stocks/batch-price?symbols=${symbol}&context=symbol`, { cache: 'no-store' });
        if (!res.ok) return 0;
        const json = (await res.json()) as {
          pending?: number;
          updated: string | null;
          source: 'finnhub' | 'alpaca_iex' | 'polygon_grouped' | 'mixed' | 'unavailable';
          session?: 'premarket' | 'regular' | 'afterhours' | 'delayed' | 'closed';
          marketOpen?: boolean;
          quoteRefreshActive?: boolean;
          data: Array<{
            symbol: string;
            price: number | null;
            previousClose: number | null;
            change: number | null;
            changePct: number | null;
            source?: 'finnhub' | 'alpaca_iex' | 'polygon_grouped';
            session?: 'premarket' | 'regular' | 'afterhours' | 'delayed' | 'closed';
          }>;
        };
        const open = json.marketOpen ?? true;
        const refreshOn = json.quoteRefreshActive ?? open;
        lastQuoteRefreshActive = refreshOn;
        const tick = json.data?.[0];
        if (!cancelled && tick && tick.price !== null) {
          setLive({
            symbol: (tick.symbol || symbol).toUpperCase(),
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
      } finally {
        if (!cancelled) setQuoteReady(true);
      }
    };

    const fastPoll = async (attempt = 0) => {
      if (cancelled) return;
      if (document.visibilityState !== 'visible') {
        timer = setTimeout(() => void fastPoll(attempt), 30_000);
        return;
      }
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
            if (document.visibilityState === 'visible') {
              await fetchOnce();
            }
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

  useEffect(() => {
    setPredictionMode('snapshot');
    setLivePrediction(EMPTY_LIVE_PREDICTION);
    lastPredictionFetchAtRef.current = 0;
    inFlightPredictionKeyRef.current = null;
  }, [symbol]);

  const livePredictionRequest = useMemo(() => {
    const em = data?.expected_move;
    const horizon = em?.model_horizon;
    const earningsDate = em?.earnings_date ?? data?.next_earnings ?? null;
    const price = live?.price ?? data?.spot_price ?? null;
    if (!symbol || !data || !em || !horizon || !earningsDate || !price || price <= 0) {
      return null;
    }
    if (![1, 2, 3, 7, 14, 21].includes(horizon)) return null;
    const roundedSpot = Math.round(price * 10) / 10;
    const eventDate = earningsDate.slice(0, 10);
    return {
      key: `${symbol}:${eventDate}:T${horizon}:${roundedSpot.toFixed(1)}`,
      body: {
        symbol,
        horizon_days: horizon,
        spot_override: roundedSpot,
        earnings_date: eventDate,
      },
    };
  }, [data, live?.price, symbol]);

  const loadLivePrediction = useCallback(
    async (force = false) => {
      if (!livePredictionRequest) return;
      const now = Date.now();
      if (
        !force &&
        livePrediction.key === livePredictionRequest.key &&
        livePrediction.status === 'ready' &&
        now - livePrediction.updatedAt < 30_000
      ) {
        return;
      }
      if (!force && now - lastPredictionFetchAtRef.current < 30_000) return;
      if (inFlightPredictionKeyRef.current === livePredictionRequest.key) return;

      inFlightPredictionKeyRef.current = livePredictionRequest.key;
      lastPredictionFetchAtRef.current = now;
      setLivePrediction((prev) => ({
        status: 'loading',
        key: livePredictionRequest.key,
        response: prev.key === livePredictionRequest.key ? prev.response : null,
        error: null,
        updatedAt: prev.updatedAt,
      }));

      try {
        const res = await fetch('/api/ml/predict', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(livePredictionRequest.body),
          cache: 'no-store',
        });
        const json = await res.json().catch(() => null);
        if (!res.ok) {
          setLivePrediction({
            status: 'unavailable',
            key: livePredictionRequest.key,
            response: null,
            error: livePredictionUnavailableMessage(res.status),
            updatedAt: Date.now(),
          });
          return;
        }
        setLivePrediction({
          status: 'ready',
          key: livePredictionRequest.key,
          response: json as LivePredictionResponse,
          error: null,
          updatedAt: Date.now(),
        });
      } catch {
        setLivePrediction({
          status: 'unavailable',
          key: livePredictionRequest.key,
          response: null,
          error: livePredictionUnavailableMessage(null),
          updatedAt: Date.now(),
        });
      } finally {
        inFlightPredictionKeyRef.current = null;
      }
    },
    [livePrediction.key, livePrediction.status, livePrediction.updatedAt, livePredictionRequest],
  );

  useEffect(() => {
    if (predictionMode === 'live') void loadLivePrediction();
  }, [loadLivePrediction, predictionMode]);

  useEffect(() => {
    if (predictionMode !== 'live') return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') void loadLivePrediction();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [loadLivePrediction, predictionMode]);

  const showToast = useCallback((msg: string) => {
    setToast({ msg, key: Date.now() });
  }, []);

  if (loading) {
    return <SymbolPageLoading symbol={symbol} />;
  }

  if (data && data.symbol.toUpperCase() !== symbol) {
    return <SymbolPageLoading symbol={symbol} />;
  }

  if (error || !data) {
    // Limited view for tickers that exist (search hit, valid ticker)
    // but have no pre-built /symbols/SYM.json (e.g. not in the options
    // universe yet, or in flight to be added). We still know the
    // company name (sp500 lookup) and can usually show a live quote
    // via the batch-price API.
    const tick = live?.symbol === symbol ? live : null;
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
            else router.push(prevLoc.path);
          }}
          className="chip"
          style={{
            border: 'none',
            color: 'var(--ink-3)',
            paddingLeft: 0,
            cursor: 'pointer',
          }}
        >
          <ChevronLeft size={14} /> {prevLoc.label}
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
          <TickerLogo
            ticker={symbol}
            size={60}
            radius={10}
            loading="eager"
            fallbackStyle={{
              fontSize: Math.max(14, 60 * 0.32),
              fontWeight: 700,
              letterSpacing: 0,
            }}
          />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              className="serif"
              style={{
                fontSize: 36,
                fontWeight: 800,
                letterSpacing: '-0.025em',
                lineHeight: 1,
              }}
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
                style={{
                  fontSize: 24,
                  fontWeight: 700,
                  letterSpacing: '-0.015em',
                }}
              >
                ${tick.price.toFixed(2)}
              </div>
              {pct != null && (
                <div className="mono tnum" style={{ fontSize: 12, color: tone, marginTop: 2 }}>
                  {arrow} {chg != null ? `$${Math.abs(chg).toFixed(2)} ` : ''}({Math.abs(pct * 100).toFixed(2)}%)
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
          <div
            className="serif"
            style={{
              fontSize: 18,
              fontWeight: 600,
              color: 'var(--ink)',
              marginBottom: 8,
            }}
          >
            We don&apos;t have an options snapshot for {symbol} yet.
          </div>
          <div style={{ color: 'var(--ink-3)', fontSize: 13 }}>
            Expected moves, IV rank, straddle quotes, and the historical implied-vs-actual chart all require an
            options-chain ingest for this symbol. Live spot quotes above{' '}
            {tick?.price != null ? 'are working' : 'will appear when the market is open'}.
          </div>
        </div>
      </div>
    );
  }

  const em = data.expected_move;
  const liveForSymbol = live?.symbol === symbol ? live : null;
  const intradayForSymbol = intraday?.symbol === symbol ? intraday : null;
  const livePrice = liveForSymbol?.price ?? null;
  const quotePending = !quoteReady && livePrice == null;
  const spot = livePrice ?? data.spot_price ?? 0;
  // Anchor the headline day-change to the OFFICIAL previous close from
  // batch-price (the Polygon prevclose override), not Alpaca's intraday
  // previousClose. The intraday value is the prior session's last IEX bar, and
  // for an AMC earnings reporter that bar already includes part of the
  // after-hours move — anchoring to it understates the reaction (ADBE read
  // −1.5% off the ~17:00 after-hours print vs the true −6.4% off the official
  // close). Fall back to intraday's close only when batch-price has none.
  const previousCloseForChange = liveForSymbol?.previousClose ?? intradayForSymbol?.previousClose ?? null;
  const useLiveChangeFallback = liveForSymbol != null;
  const change =
    livePrice != null && previousCloseForChange != null && previousCloseForChange > 0
      ? livePrice - previousCloseForChange
      : useLiveChangeFallback
        ? (liveForSymbol?.change ?? 0)
        : 0;
  const changePct =
    livePrice != null && previousCloseForChange != null && previousCloseForChange > 0
      ? change / previousCloseForChange
      : useLiveChangeFallback
        ? (liveForSymbol?.changePct ?? 0)
        : 0;

  const straddlePct = em?.straddle_pct ?? 0;
  const earningsDate = em?.earnings_date ?? data.next_earnings ?? null;
  const earningsTiming = timingText(em?.timing ?? data.next_earnings_timing);
  const daysLeft = daysFromToday(earningsDate);
  const eventLabel = eventLabelFor(data, earningsDate);

  const snapshotQuantiles = normalizeForecastQuantiles(
    em
      ? {
          '10': em.p10,
          '25': em.p25,
          '50': em.p50,
          '75': em.p75,
          '90': em.p90,
        }
      : null,
  );
  const liveQuantiles =
    livePrediction.status === 'ready' ? normalizeForecastQuantiles(livePrediction.response?.quantiles) : null;
  const showingLivePrediction =
    predictionMode === 'live' && livePrediction.status === 'ready' && livePrediction.response != null;
  const modelIsSpotUpdated =
    showingLivePrediction && livePrediction.response?.source !== 'nightly_fallback';
  const quantiles = showingLivePrediction && liveQuantiles ? liveQuantiles : snapshotQuantiles;
  const rawActivePredictionPct = showingLivePrediction
    ? (livePrediction.response?.em_ml_pct ?? null)
    : (em?.em_ml_pct ?? null);
  const activePredictionPct =
    rawActivePredictionPct != null && Number.isFinite(rawActivePredictionPct)
      ? Math.max(0, rawActivePredictionPct)
      : null;
  const quantileMeta = showingLivePrediction
    ? livePrediction.response?.source === 'nightly_fallback'
      ? 'Nightly snapshot · spot update unavailable'
      : `Latest stock price only; options and other inputs remain frozen at ${livePrediction.response?.feature_snapshot_date ?? 'the nightly snapshot'}.`
    : em?.ml_snapshot_date
      ? `Nightly LightGBM snapshot from ${em.ml_snapshot_date}.`
      : 'LightGBM ensemble · range of plausible absolute moves on print day';
  const liveUnavailableReason =
    predictionMode === 'live' && livePrediction.status === 'unavailable' ? livePrediction.error : null;

  const termRows = buildTermRows(data.straddle_features, em?.expiration ?? null);
  const historySeries = buildHistorySeries(data.earnings_history);
  const comparisonHistory = historySeries.slice(-8);
  const historicalMedianMovePct = medianAbsoluteHistoryMove(comparisonHistory);

  return (
    <div className="qv-m-pad qv-symbol-page-shell" style={{ maxWidth: 1100, margin: '0 auto', padding: '0 28px 80px' }}>
      {toast && <Toast key={toast.key} message={toast.msg} onDone={() => setToast(null)} />}

      <Reveal as="div" style={{ marginTop: 8 }}>
        <DetailHero
          ticker={companyName(symbol)}
          symbol={symbol}
          spot={spot}
          change={change}
          changePct={changePct}
          quotePending={quotePending}
          emPct={straddlePct}
          daysLeft={daysLeft}
          earningsDate={earningsDate}
          earningsTiming={earningsTiming}
          eventLabel={eventLabel}
          quoteLabel={quoteSourceLabel(liveForSymbol, symbol, data.as_of_date)}
          intradayBars={
            !quotePending && intradayForSymbol?.bars && intradayForSymbol.bars.length >= 2
              ? intradayForSymbol.bars
              : null
          }
          intradayLoading={quotePending || intradayForSymbol === null}
          intradaySessionDate={intradayForSymbol?.sessionDate ?? null}
          intradayIsCurrentSession={intradayForSymbol?.isCurrentSession ?? null}
          onBack={() => {
            if (window.history.length > 1) router.back();
            else router.push(prevLoc.path);
          }}
          backLabel={prevLoc.label}
          onToast={showToast}
        />
      </Reveal>

      {em && (
        <Reveal delay={60}>
          <ResearchSnapshotRibbon
            evidence={initialEvidence}
            optionsDate={data.as_of_date}
            earningsDate={earningsDate}
            earningsTiming={em.timing ?? data.next_earnings_timing}
            modelSnapshotDate={em.ml_snapshot_date}
            modelHorizon={em.model_horizon}
          />
        </Reveal>
      )}

      {/* KPI strip */}
      {em && (
        <Reveal delay={80}>
          <div
            className="qv-m-2col"
            style={{
              marginTop: 22,
              display: 'grid',
              gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
              gap: 14,
            }}
          >
            <KpiCard
              label="ATM IV"
              value={em.atm_iv != null ? `${(em.atm_iv * 100).toFixed(1)}%` : '–'}
              sub={
                em.term_slope != null
                  ? `Term slope ${(em.term_slope * 100).toFixed(1)} vol pts / 30d`
                  : `Front-month · ${em.dte} DTE`
              }
              accent="var(--brand-blue-1)"
              metric="atmIv"
              helpAlign="left"
            />
            <KpiCard
              label="IV-based expected move"
              value={em.iv_pct != null ? `±${(em.iv_pct * 100).toFixed(1)}%` : '–'}
              sub={em.atm_iv != null ? `${(em.atm_iv * 100).toFixed(0)}% annualized IV · ${em.dte} DTE` : undefined}
              metric="ivExpectedMove"
              helpAlign="left"
            />
            <KpiCard
              label="ATM Straddle"
              value={em.straddle_abs != null ? `$${em.straddle_abs.toFixed(2)}` : '–'}
              sub={`Call + put · strike $${em.atm_strike.toFixed(2)}`}
              metric="atmStraddle"
            />
            <KpiCard
              label={data.vol_regime?.iv_rank != null ? 'IV Rank' : 'ATM Skew'}
              value={
                data.vol_regime?.iv_rank != null
                  ? `${Math.round(data.vol_regime.iv_rank * 100)}%`
                  : em.skew_atm != null
                    ? `${(em.skew_atm * 100).toFixed(2)}v`
                    : '–'
              }
              sub={
                data.vol_regime?.iv_rank != null
                  ? data.vol_regime.iv_year_low != null && data.vol_regime.iv_year_high != null
                    ? `52w ${(data.vol_regime.iv_year_low * 100).toFixed(0)}–${(data.vol_regime.iv_year_high * 100).toFixed(0)}%`
                    : undefined
                  : em.total_vega != null
                    ? `Combined ATM vega ${em.total_vega.toFixed(3)}`
                    : undefined
              }
              metric={data.vol_regime?.iv_rank != null ? 'ivRank' : 'atmSkew'}
            />
          </div>
        </Reveal>
      )}

      {em && spot > 0 && (
        <Reveal delay={100}>
          <MoveComparisonChart
            spot={spot}
            optionsMovePct={em.straddle_pct ?? null}
            modelMovePct={activePredictionPct}
            modelQuantiles={quantiles}
            modelIsSpotUpdated={modelIsSpotUpdated}
            historicalMovePct={historicalMedianMovePct}
            historyCount={comparisonHistory.length}
            ivRank={data.vol_regime?.iv_rank ?? null}
            mode={predictionMode}
            onModeChange={setPredictionMode}
            spotUpdateDisabled={livePredictionRequest == null}
            spotUpdateStatus={livePrediction.status}
            modelMeta={quantileMeta}
            unavailableReason={liveUnavailableReason}
          />
        </Reveal>
      )}

      {termRows.length > 0 && (
        <Reveal delay={140}>
          <div style={{ marginTop: 18 }}>
            <TermFan rows={termRows} spot={spot} />
          </div>
        </Reveal>
      )}

      {em && termRows.length > 0 && spot > 0 && straddlePct > 0 && (
        <Reveal delay={195}>
          <div style={{ marginTop: 18 }}>
            <ScenarioRiskPanel
              spot={spot}
              strike={(termRows.find((row) => row.isEarnings) ?? termRows[0]).strike}
              premium={(termRows.find((row) => row.isEarnings) ?? termRows[0]).straddle}
              expectedMovePct={straddlePct}
              modelMovePct={activePredictionPct}
            />
          </div>
        </Reveal>
      )}

      {/* History + EPS surprise */}
      {historySeries.length >= 2 && (
        <Reveal delay={200}>
          <div style={{ marginTop: 18 }}>
            <HistoryBlock key={symbol} history={historySeries} symbol={symbol} />
          </div>
        </Reveal>
      )}

      {data.provider_enrichment && (
        <Reveal delay={220}>
          <ProviderSignalsPanel enrichment={data.provider_enrichment} />
        </Reveal>
      )}

      {/* Greeks panel */}
      {termRows.length > 0 && (
        <Reveal delay={240}>
          <div style={{ marginTop: 18 }}>
            <GreeksPanel rows={termRows} />
          </div>
        </Reveal>
      )}

      {/* Footer */}
      <Reveal delay={280}>
        <div
          style={{
            marginTop: 32,
            padding: '18px 0 0',
            borderTop: '1px solid var(--line)',
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: 11,
            color: 'var(--ink-4)',
            flexWrap: 'wrap',
            gap: 10,
          }}
        >
          <span className="mono">Options data · as of {data.as_of_date}</span>
          <span>
            Method:{' '}
            {em?.em_method === 'ml_lightgbm'
              ? 'ML forecast'
              : em?.em_method === 'ensemble'
                ? 'Math + ML'
                : 'options math baseline'}
          </span>
        </div>
      </Reveal>
    </div>
  );
}
