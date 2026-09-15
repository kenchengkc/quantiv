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
 * A revised ticker/date or a conflicting known session still renders dates-only
 * so old options/model metrics can never migrate onto a different event.
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
  for (const event of research.events ?? []) {
    researchByTickerDate.set(`${event.ticker}|${event.earnings_date}`, event);
  }

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
        return {
          ...researchMatch,
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
      // Do not spread any research object here. Absence of metrics is the
      // fail-closed state for revised dates or known-session conflicts.
      return {
        ticker: event.ticker,
        earnings_date: event.earnings_date,
        timing: referenceTiming,
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
