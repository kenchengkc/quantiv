import { resolveDisplayForecastCompat } from './displayForecast';

export type CalendarReferenceEvent = {
  ticker: string;
  earnings_date: string;
  timing: string;
};

export type CalendarReference = {
  schema: 'quantiv.calendar-reference.v1' | string;
  release_id: string;
  receipt_id?: string;
  observed_at?: string;
  source_revision?: string;
  window: { start: string; end: string };
  events: CalendarReferenceEvent[];
};

export type ResearchWeek<
  TEvent extends CalendarReferenceEvent = CalendarReferenceEvent,
  TMetadata extends { as_of_date?: string } = Record<string, unknown> & { as_of_date?: string },
> = {
  metadata: TMetadata;
  window: { start: string; end: string };
  events: TEvent[];
  summary?: Record<string, unknown>;
};

// The overlay has exactly two honest runtime shapes: a retained research row
// whose event identity still matches, or a dates/session-only reference row.
// A union models that directly without pretending missing research fields were
// populated and without forcing callers through `as never` casts.
export type CalendarOverlayEvent<TEvent extends CalendarReferenceEvent> =
  TEvent | CalendarReferenceEvent;

export type CalendarOverlayMetadata<TMetadata extends { as_of_date?: string }> =
  TMetadata & {
    calendar_reference_release_id?: string | null;
    calendar_reference_receipt_id?: string | null;
    calendar_reference_observed_at?: string | null;
    research_as_of_date?: string | null;
  };

const KNOWN_SESSIONS = new Set(['bmo', 'amc', 'dmh']);

type LegacyForecastOverlay = {
  display_forecast_pct?: number | null;
  display_forecast_method?: 'ml' | 'options_math' | 'options_indicative' | 'historical' | 'historical_prior' | null;
  em_ml_pct?: number | null;
  em_straddle_pct?: number | null;
  em_iv_pct?: number | null;
  hist_move_med_4q?: number | null;
  hist_move_avg_4q?: number | null;
};

function historicalCompatPct(event: LegacyForecastOverlay | null | undefined): number | null {
  if (!event) return null;
  const resolved = resolveDisplayForecastCompat({
    hist_move_med_4q: event.hist_move_med_4q,
    hist_move_avg_4q: event.hist_move_avg_4q,
  });
  return resolved.method === 'historical' ? resolved.pct : null;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[middle];
  return (sorted[middle - 1] + sorted[middle]) / 2;
}

export function normalizeCalendarTiming(value: string | null | undefined): 'bmo' | 'amc' | 'dmh' | 'unknown' {
  const key = (value ?? '').trim().toLowerCase().split('-').join('_').split(' ').join('_');
  if (key === 'bmo' || key === 'before_market_open' || key === 'before_open') return 'bmo';
  if (key === 'amc' || key === 'after_market_close' || key === 'after_close') return 'amc';
  if (key === 'dmh' || key === 'during_market_hours' || key === 'during_market_hour') return 'dmh';
  return 'unknown';
}

function addDays(iso: string, days: number): string {
  const [year, month, day] = iso.slice(0, 10).split('-').map(Number);
  const value = new Date(Date.UTC(year, (month ?? 1) - 1, day ?? 1));
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

/**
 * Treat calendar-reference as authoritative for ticker/date membership while
 * research remains an optional, immutable overlay. A known reference session
 * is authoritative and research is inherited only when it matches. An unknown
 * session is absence of session evidence, not a contradictory value: overlay
 * on an exact ticker/date match when sessions do not disagree, including when
 * both sides are still unknown.
 *
 * A revised ticker/date or a conflicting known session still rejects old
 * options/model metrics. During migration from older pinned public releases,
 * point-in-time historical summaries may still supply display-only fallback
 * provenance because they are independent of the revised event's option/model
 * identity. If no ticker history exists, a median of the legacy week's
 * historical summaries is used only as a temporary historical prior.
 *
 * The reference is authoritative only inside its declared publication window.
 * If the browser advances to a week the retained reference does not fully cover
 * (for example across a Sunday→Monday rollover before the next refresh), keep
 * the retained research week instead of misreading "not published" as "zero
 * earnings".
 */
export function mergeCalendarReference<
  TEvent extends CalendarReferenceEvent,
  TMetadata extends { as_of_date?: string },
>(
  reference: CalendarReference | null | undefined,
  research: ResearchWeek<TEvent, TMetadata>,
  weekStart: string,
): ResearchWeek<CalendarOverlayEvent<TEvent>, CalendarOverlayMetadata<TMetadata>> {
  if (!reference?.release_id || !Array.isArray(reference.events)) return research;

  const start = weekStart.slice(0, 10);
  const end = addDays(start, 4);
  if (start < reference.window.start || end > reference.window.end) return research;

  const researchByTickerDate = new Map<string, TEvent>();
  const legacyHistoryByTicker = new Map<string, number>();
  for (const event of research.events ?? []) {
    researchByTickerDate.set(`${event.ticker}|${event.earnings_date}`, event);
    const historicalPct = historicalCompatPct(event as TEvent & LegacyForecastOverlay);
    if (historicalPct != null && !legacyHistoryByTicker.has(event.ticker)) {
      legacyHistoryByTicker.set(event.ticker, historicalPct);
    }
  }
  const legacyHistoricalPrior = median([...legacyHistoryByTicker.values()]);

  const events: CalendarOverlayEvent<TEvent>[] = reference.events
    .filter((event) => event.earnings_date >= start && event.earnings_date <= end)
    .map((event): CalendarOverlayEvent<TEvent> => {
      const referenceTiming = normalizeCalendarTiming(event.timing);
      const researchMatch = researchByTickerDate.get(`${event.ticker}|${event.earnings_date}`);
      const researchTiming = normalizeCalendarTiming(researchMatch?.timing);
      const referenceSessionKnown = KNOWN_SESSIONS.has(referenceTiming);
      const researchSessionKnown = KNOWN_SESSIONS.has(researchTiming);
      // Same ticker+date is the same event. A known BMO/AMC/DMH disagreement
      // is a different reaction window, so those metrics stay fail-closed.
      // Unknown is missing session evidence, not a conflicting value: overlay
      // when both sides are unknown, or when research has a known session the
      // reference does not contradict.
      const knownSessionConflict =
        referenceSessionKnown && researchSessionKnown && referenceTiming !== researchTiming;
      const sessionsCompatible =
        researchMatch != null &&
        !knownSessionConflict &&
        (researchSessionKnown || !referenceSessionKnown);

      if (researchMatch && sessionsCompatible) {
        const compatForecast = resolveDisplayForecastCompat(
          researchMatch as TEvent & LegacyForecastOverlay,
        );
        // R2 can temporarily serve a pre-migration week artifact while the
        // independent calendar reference is newer. Preserve strict ML/options
        // semantics, but hydrate history-only exact matches so the UI does not
        // turn an available historical estimate into a dash. If the old row has
        // no usable display estimate at all, use the legacy prior rather than a
        // synthetic zero.
        const displayCompat =
          compatForecast.method === 'historical' && compatForecast.pct != null
            ? {
                display_forecast_pct: compatForecast.pct,
                display_forecast_method: 'historical' as const,
              }
            : compatForecast.pct == null && legacyHistoricalPrior != null
              ? {
                  display_forecast_pct: legacyHistoricalPrior,
                  display_forecast_method: 'historical_prior' as const,
                }
              : {};
        return {
          ...researchMatch,
          ...displayCompat,
          ticker: event.ticker,
          earnings_date: event.earnings_date,
          // A known reference session remains authoritative. When the reference
          // is unknown, retain a known research session so the row stays in
          // the correct BMO/AMC/DMH group together with its matched metrics.
          timing: referenceSessionKnown
            ? referenceTiming
            : researchSessionKnown
              ? researchTiming
              : referenceTiming,
        };
      }

      // Do not spread the stale research row here. A revised date or conflicting
      // session may reuse only ticker-level historical context; old ML/options
      // remain fail-closed. If that is unavailable, the legacy week median is a
      // presentation-only prior until the canonical R2 release catches up.
      const tickerHistoricalPct = legacyHistoryByTicker.get(event.ticker) ?? null;
      const fallbackDisplay =
        tickerHistoricalPct != null
          ? {
              display_forecast_pct: tickerHistoricalPct,
              display_forecast_method: 'historical' as const,
            }
          : legacyHistoricalPrior != null
            ? {
                display_forecast_pct: legacyHistoricalPrior,
                display_forecast_method: 'historical_prior' as const,
              }
            : {};
      return {
        ticker: event.ticker,
        earnings_date: event.earnings_date,
        timing: referenceTiming,
        ...fallbackDisplay,
      };
    })
    .sort((a, b) => a.earnings_date.localeCompare(b.earnings_date) || a.ticker.localeCompare(b.ticker));

  const metadata: CalendarOverlayMetadata<TMetadata> = {
    ...research.metadata,
    calendar_reference_release_id: reference.release_id,
    calendar_reference_receipt_id: reference.receipt_id ?? null,
    calendar_reference_observed_at: reference.observed_at ?? null,
    research_as_of_date: research.metadata.as_of_date ?? null,
  };

  return {
    ...research,
    metadata,
    window: { start, end },
    events,
    ...(research.summary
      ? { summary: { ...research.summary, total_events: events.length } }
      : {}),
  };
}

export function calendarCacheKey(releaseId: string | null | undefined, weekStart: string): string {
  return `${releaseId || 'legacy'}:${weekStart}`;
}

export function localTodayIso(now = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/** The calendar-reference event to display for a ticker. Upcoming dates win
 *  over a superseded research print so "Reports in" cannot go negative while
 *  the homepage still lists the name next week. */
export function publishedEventForTicker(
  events: readonly CalendarReferenceEvent[] | undefined,
  ticker: string,
  todayIso: string,
): CalendarReferenceEvent | null {
  const rows = (events ?? []).filter(
    (event) => event.ticker.toUpperCase() === ticker.toUpperCase(),
  );
  if (rows.length === 0) return null;
  const today = todayIso.slice(0, 10);
  const upcoming = rows
    .filter((event) => event.earnings_date >= today)
    .sort((a, b) => a.earnings_date.localeCompare(b.earnings_date));
  if (upcoming.length > 0) return upcoming[0];
  return [...rows].sort((a, b) => b.earnings_date.localeCompare(a.earnings_date))[0];
}

/** Date shown in Reports / Reports-in. Published calendar identity wins;
 *  otherwise the latest still-upcoming research date. */
export function displayEarningsDate(args: {
  todayIso: string;
  publishedDate?: string | null;
  nextEarnings?: string | null;
  researchDate?: string | null;
}): string | null {
  if (args.publishedDate) return args.publishedDate.slice(0, 10);
  const today = args.todayIso.slice(0, 10);
  const dates = [args.nextEarnings, args.researchDate]
    .map((value) => value?.slice(0, 10) ?? null)
    .filter((value): value is string => Boolean(value));
  const upcoming = dates.filter((value) => value >= today).sort();
  if (upcoming.length > 0) return upcoming[upcoming.length - 1];
  return dates.sort().at(-1) ?? null;
}

export function researchMatchesPublishedDate(
  researchDate: string | null | undefined,
  publishedDate: string | null | undefined,
): boolean {
  return Boolean(
    researchDate &&
      publishedDate &&
      researchDate.slice(0, 10) === publishedDate.slice(0, 10),
  );
}

/** Whole days until `iso`. Null when missing or already in the past so a
 *  stale research print cannot render as "Reports · -19d". */
export function daysUntilEarnings(iso: string | null | undefined, todayIso: string): number | null {
  if (!iso) return null;
  const date = iso.slice(0, 10);
  const today = todayIso.slice(0, 10);
  const start = Date.parse(`${today}T00:00:00Z`);
  const target = Date.parse(`${date}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(target)) return null;
  const days = Math.round((target - start) / 86_400_000);
  return days < 0 ? null : days;
}
