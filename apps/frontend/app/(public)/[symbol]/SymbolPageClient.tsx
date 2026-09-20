'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { useParams, useRouter } from 'next/navigation';
import SymbolResearchExport, { ComparableHistoryLink } from '@/components/SymbolResearchExport';
import type { ComparableResearchContext } from '@/lib/comparableResearch';
import { companyName } from '@/lib/companyNames';
import {
  daysUntilEarnings,
  displayEarningsDate,
  localTodayIso,
  publishedEventForTicker,
  researchMatchesPublishedDate,
  type CalendarReference,
  type CalendarReferenceEvent,
} from '@/lib/calendarReference';
import {
  displayForecastLabel,
  finiteDisplayForecast,
  resolveDisplayForecastCompat,
  type DisplayForecastMethod,
} from '@/lib/displayForecast';
import { normalizeForecastQuantiles } from '@/lib/forecastQuantiles';
import { listingExchangeLabel } from '@/lib/listingExchanges';
import { useEnsureCompanyNames } from '@/lib/useCompanyNames';
import { useEnsureListingExchanges } from '@/lib/useListingExchanges';
import {
  DetailHero,
  KpiCard,
  ProviderSignalsPanel,
  Toast,
  usePrevAppLocation,
} from './SymbolPageHeader';
import { buildTermRows, TermFan } from './ForecastPanels';
import { buildHistorySeries, GreeksPanel, HistoryBlock, medianAbsoluteHistoryMove } from './HistoryRiskPanels';
import ScenarioRiskPanel from './ScenarioRiskPanel';
import ResearchSnapshotRibbon from './ResearchSnapshotRibbon';
import MoveComparisonChart from './MoveComparisonChart';
import { SymbolPageLoading, SymbolPageUnavailable } from './SymbolPageStates';
import type {
  IntradaySeries,
  LivePredictionResponse,
  LivePredictionState,
  LivePrice,
  PredictionMode,
  SymbolDetail,
} from './symbolPageTypes';
import { parseLocalDate } from './symbolPageUtils';

const EMPTY_LIVE_PREDICTION: LivePredictionState = {
  status: 'idle',
  key: null,
  response: null,
  error: null,
  updatedAt: 0,
};
const EMPTY_CALENDAR_EVENTS: CalendarReferenceEvent[] = [];

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

function frozenHistoryForecastForEvent(
  data: SymbolDetail,
  earningsDate: string | null,
  todayIso: string,
) {
  const eventIso = earningsDate?.slice(0, 10) ?? null;
  if (!eventIso || eventIso >= todayIso) return null;
  const row = (data.earnings_history ?? []).find(
    (item) => item.date.slice(0, 10) === eventIso,
  );
  if (!row) return null;

  const timing = (row.timing ?? '').toLowerCase();
  const afterClose =
    timing === 'amc' ||
    timing === 'after_market_close' ||
    timing === 'after_close' ||
    timing.includes('after');
  const impliedAsOf = row.implied_as_of?.slice(0, 10) ?? null;
  const optionPointInTime =
    impliedAsOf != null &&
    (afterClose ? impliedAsOf <= eventIso : impliedAsOf < eventIso) &&
    (row.implied_quality_status == null ||
      row.implied_quality_status === 'decision_eligible_eod');

  const atmIv = finiteDisplayForecast(row.implied_atm_iv);
  const dte =
    row.implied_dte != null && Number.isFinite(row.implied_dte) && row.implied_dte > 0
      ? row.implied_dte
      : null;
  const ivPct =
    optionPointInTime && atmIv != null && dte != null
      ? atmIv * Math.sqrt(dte / 365)
      : null;
  const straddlePct = optionPointInTime
    ? finiteDisplayForecast(row.implied)
    : null;

  const mlSnapshot = row.ml_snapshot_date?.slice(0, 10) ?? null;
  const mlPct =
    mlSnapshot != null && mlSnapshot < eventIso
      ? finiteDisplayForecast(row.em_ml_pct)
      : null;

  return {
    row,
    ivPct,
    straddlePct,
    mlPct,
    mlSnapshot,
  };
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
  comparableContext = null,
  calendarEvents: initialCalendarEvents = EMPTY_CALENDAR_EVENTS,
}: {
  initialData?: unknown;
  initialEvidence?: unknown;
  initialSymbol?: string;
  comparableContext?: ComparableResearchContext | null;
  calendarEvents?: CalendarReferenceEvent[];
}) {
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
  const [intraday, setIntraday] = useState<IntradaySeries | null>(null);
  const [calendarEvents, setCalendarEvents] = useState<CalendarReferenceEvent[]>(
    initialCalendarEvents,
  );

  useEffect(() => {
    setCalendarEvents(initialCalendarEvents);
  }, [initialCalendarEvents]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch('/calendar-reference.json', { cache: 'no-store' });
        if (!res.ok) return;
        const json = (await res.json()) as CalendarReference;
        if (!cancelled && Array.isArray(json.events)) setCalendarEvents(json.events);
      } catch {
        // Seeded publication dates remain until the independent calendar is readable.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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

  const todayIso = localTodayIso();
  const publishedEvent = publishedEventForTicker(calendarEvents, symbol, todayIso);
  const researchMatchesPublished =
    !publishedEvent ||
    researchMatchesPublishedDate(data?.expected_move?.earnings_date, publishedEvent.earnings_date);

  const livePredictionRequest = useMemo(() => {
    const em = data?.expected_move;
    const horizon = em?.model_horizon;
    const earningsDate = em?.earnings_date ?? data?.next_earnings ?? null;
    const price = live?.price ?? data?.spot_price ?? null;
    if (!researchMatchesPublished || em?.forecast_frozen) return null;
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
  }, [data, live?.price, researchMatchesPublished, symbol]);

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
    if (predictionMode === 'spot_updated') void loadLivePrediction();
  }, [loadLivePrediction, predictionMode]);

  useEffect(() => {
    if (predictionMode !== 'spot_updated') return;
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
    return (
      <SymbolPageUnavailable
        symbol={symbol}
        live={live}
        backLabel={prevLoc.label}
        onBack={() => {
          if (window.history.length > 1) router.back();
          else router.push(prevLoc.path);
        }}
      />
    );
  }

  const em = researchMatchesPublished ? data.expected_move : undefined;
  const historySeries = buildHistorySeries(data.earnings_history);
  const comparisonHistory = historySeries.slice(-8);
  const compatibilityHistory = historySeries.slice(-4);
  const historicalMedianMovePct = medianAbsoluteHistoryMove(comparisonHistory);
  const historicalCompatPct =
    compatibilityHistory.length >= 2
      ? medianAbsoluteHistoryMove(compatibilityHistory)
      : null;

  const liveForSymbol = live?.symbol === symbol ? live : null;
  const intradayForSymbol = intraday?.symbol === symbol ? intraday : null;
  const livePrice = liveForSymbol?.price ?? null;
  const quotePending = !quoteReady && livePrice == null;
  const spot = livePrice ?? data.spot_price ?? 0;
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

  const straddlePct = finiteDisplayForecast(em?.straddle_pct) ?? 0;
  const earningsDate = displayEarningsDate({
    todayIso,
    publishedDate: publishedEvent?.earnings_date,
    nextEarnings: data.next_earnings,
    researchDate: data.expected_move?.earnings_date,
  });
  const earningsTiming = timingText(
    publishedEvent?.timing ?? em?.timing ?? data.next_earnings_timing,
  );
  const daysLeft = daysUntilEarnings(earningsDate, todayIso);
  const eventLabel = eventLabelFor(data, earningsDate);

  const frozenHistoryForecast = frozenHistoryForecastForEvent(
    data,
    earningsDate,
    todayIso,
  );
  const modelSnapshot = em ?? frozenHistoryForecast?.row ?? null;
  const snapshotQuantiles = normalizeForecastQuantiles(
    modelSnapshot
      ? {
          '10': modelSnapshot.p10,
          '25': modelSnapshot.p25,
          '50': modelSnapshot.p50,
          '75': modelSnapshot.p75,
          '90': modelSnapshot.p90,
        }
      : null,
  );
  const liveQuantiles =
    livePrediction.status === 'ready' ? normalizeForecastQuantiles(livePrediction.response?.quantiles) : null;
  const showingLivePrediction =
    livePredictionRequest != null &&
    predictionMode === 'spot_updated' &&
    livePrediction.status === 'ready' &&
    livePrediction.response != null;
  const modelIsSpotUpdated =
    showingLivePrediction &&
    (livePrediction.response?.inference_mode === 'spot_updated_snapshot' ||
      (livePrediction.response?.inference_mode == null &&
        livePrediction.response?.source !== 'nightly_fallback'));
  const quantiles = showingLivePrediction && liveQuantiles ? liveQuantiles : snapshotQuantiles;
  const rawActivePredictionPct = showingLivePrediction
    ? (livePrediction.response?.em_ml_pct ?? null)
    : (em?.em_ml_pct ?? frozenHistoryForecast?.mlPct ?? null);
  const activePredictionPct =
    rawActivePredictionPct != null && Number.isFinite(rawActivePredictionPct)
      ? Math.max(0, rawActivePredictionPct)
      : null;
  const headlineForecastFields = {
    ...(em ?? {}),
    iv_pct:
      finiteDisplayForecast(em?.iv_pct) ??
      frozenHistoryForecast?.ivPct ??
      null,
    straddle_pct:
      finiteDisplayForecast(em?.straddle_pct) ??
      frozenHistoryForecast?.straddlePct ??
      null,
    em_ml_pct:
      finiteDisplayForecast(em?.em_ml_pct) ??
      frozenHistoryForecast?.mlPct ??
      null,
  };
  const compatDisplayForecast = resolveDisplayForecastCompat(
    headlineForecastFields,
    historicalCompatPct,
  );
  const staticDisplayPct = compatDisplayForecast.pct;
  // The hero uses the same canonical hierarchy as every other surface:
  // validated ML first, then point-in-time IV/options, then history.
  // Spot-updated ML remains a separate interactive comparison and does not
  // rewrite the frozen static research snapshot.
  const displayForecastPct = staticDisplayPct;
  const displayForecastMethod: DisplayForecastMethod | null = compatDisplayForecast.method;
  const quantileMeta = showingLivePrediction
    ? livePrediction.response?.source === 'nightly_fallback'
      ? 'Nightly snapshot · spot update unavailable'
      : `End-of-day research · latest stock price only; options and other inputs remain frozen at ${livePrediction.response?.feature_snapshot_date ?? 'the nightly snapshot'}.`
    : em?.forecast_frozen && (em?.ml_snapshot_date ?? frozenHistoryForecast?.mlSnapshot)
      ? `Final pre-event LightGBM snapshot from ${em?.ml_snapshot_date ?? frozenHistoryForecast?.mlSnapshot} · frozen after earnings.`
      : (em?.ml_snapshot_date ?? frozenHistoryForecast?.mlSnapshot)
        ? `Nightly LightGBM snapshot from ${em?.ml_snapshot_date ?? frozenHistoryForecast?.mlSnapshot}.`
        : 'LightGBM ensemble · range of plausible absolute moves on print day';
  const liveUnavailableReason =
    predictionMode === 'spot_updated' && livePrediction.status === 'unavailable' ? livePrediction.error : null;

  const termRows = buildTermRows(data.straddle_features, em?.expiration ?? null);
  const hasOptionsEvidence = Boolean(
    em &&
      em.atm_strike != null &&
      em.dte != null &&
      (em.straddle_pct != null || em.straddle_abs != null || em.atm_iv != null || em.iv_pct != null),
  );

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
          emPct={displayForecastPct}
          emMethod={displayForecastMethod}
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

      {em && spot > 0 && (
        <Reveal>
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

      {historySeries.length >= 2 ? (
        <Reveal>
          <div style={{ marginTop: 18 }}>
            <HistoryBlock
              key={symbol}
              history={historySeries}
              symbol={symbol}
              relatedResearch={comparableContext ? <ComparableHistoryLink context={comparableContext} /> : null}
            />
          </div>
        </Reveal>
      ) : comparableContext ? (
        <Reveal style={{ marginTop: 18 }}>
          <ComparableHistoryLink context={comparableContext} />
        </Reveal>
      ) : null}

      {data.provider_enrichment && (
        <Reveal>
          <ProviderSignalsPanel enrichment={data.provider_enrichment} />
        </Reveal>
      )}

      {em && hasOptionsEvidence && em.atm_strike != null && em.dte != null && (
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

      {termRows.length > 0 && (
        <Reveal delay={240}>
          <div style={{ marginTop: 18 }}>
            <GreeksPanel rows={termRows} />
          </div>
        </Reveal>
      )}

      <Reveal style={{ marginTop: 22 }}>
        {em && (em.em_ml_pct != null || em.straddle_pct != null) && (
          <ResearchSnapshotRibbon
            evidence={initialEvidence}
            optionsDate={data.as_of_date}
            earningsDate={earningsDate}
            earningsTiming={em.timing ?? data.next_earnings_timing}
            modelSnapshotDate={em.ml_snapshot_date}
            modelHorizon={em.model_horizon}
          />
        )}
        <SymbolResearchExport symbol={symbol} />
      </Reveal>

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
          <span className="mono">Research data · as of {data.as_of_date}</span>
          <span>Method: {displayForecastLabel(displayForecastMethod)}</span>
        </div>
      </Reveal>
    </div>
  );
}
