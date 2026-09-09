/** Display semantics only: never alters control statuses or publication gates. */
export type PublicationControl = {
  generated_at?: string | null;
  publication_eligible?: boolean | null;
  data: {
    status?: string;
    source_date?: string | null;
    expected_source_date?: string | null;
    source_session_lag?: number | null;
    quote_quality_errors?: string[];
  };
  model: { status?: string };
  exceptions: Array<{ code: string; severity: string; summary?: string; count?: number }>;
};

export type PublishedForecast = {
  receipt_id?: string | null;
  validated_at?: string | null;
  quality: { status?: string; issue_count?: number | null };
  controls: { exceptions?: number | null };
};

const OPTIONS_ONLY = new Set([
  'options_stale', 'event_quote_coverage_below_limit', 'option_quote_quality_below_limit',
]);
const ACCEPTABLE = new Set(['passed', 'degraded', 'warning']);

export function publishedForecastStatus(forecast: PublishedForecast): 'passed' | 'failed' | 'unavailable' | 'degraded' {
  if (forecast.quality.status === 'failed'
    || (forecast.quality.issue_count ?? 0) > 0 || (forecast.controls.exceptions ?? 0) > 0) return 'failed';
  if (!forecast.receipt_id || !forecast.validated_at
    || !Number.isFinite(Date.parse(forecast.validated_at))) return 'unavailable';
  if (forecast.quality.status === 'passed' && forecast.quality.issue_count === 0
    && forecast.controls.exceptions === 0) return 'passed';
  return forecast.quality.status === 'degraded' || forecast.quality.status === 'warning'
    ? 'degraded' : 'unavailable';
}

export function researchUpdatePresentation(control: PublicationControl, forecast: PublishedForecast) {
  const critical = control.exceptions.filter((item) => item.severity === 'critical');
  const modelUsable = ACCEPTABLE.has(control.model.status ?? '');
  if (!control.generated_at || !Number.isFinite(Date.parse(control.generated_at))
    || control.data.status === 'unavailable' || !control.data.status
    || control.model.status === 'unavailable' || !control.model.status
    || control.publication_eligible == null) {
    return { label: 'Unavailable', tone: 'ink-3', detail: 'Current publication evidence is incomplete.' } as const;
  }
  if (control.publication_eligible === true && critical.length === 0
    && ACCEPTABLE.has(control.data.status ?? '') && modelUsable) {
    return { label: 'Eligible', tone: 'up', detail: 'Current controls permit a new research release.' } as const;
  }
  if (control.publication_eligible === false && publishedForecastStatus(forecast) === 'passed'
    && modelUsable && critical.length > 0 && critical.every((item) => OPTIONS_ONLY.has(item.code))) {
    return {
      label: 'Held', tone: 'flag',
      detail: 'Awaiting eligible options data. The last validated forecast release is retained; new research publication remains blocked.',
    } as const;
  }
  return { label: 'Blocked', tone: 'down', detail: 'Current controls require attention before new research can be published.' } as const;
}

export function optionsSnapshotFreshness(data: PublicationControl['data']): string | null {
  const lag = data.source_session_lag;
  if (lag != null && lag > 0 && data.source_date && data.expected_source_date) {
    return `Snapshot ${data.source_date} is ${lag} market session${lag === 1 ? '' : 's'} behind expected ${data.expected_source_date}`;
  }
  if (lag === 0 && data.source_date && data.expected_source_date) {
    return `Snapshot ${data.source_date} matches the expected ${data.expected_source_date} market session`;
  }
  if (data.source_date) return `Snapshot ${data.source_date} is the latest assessed options evidence`;
  return null;
}

export function quoteEligibilityExplanation(data: PublicationControl['data']): string {
  const details = data.quote_quality_errors?.filter((error) => typeof error === 'string' && error.trim());
  if (details?.length) return `Options evidence did not pass the latest assessed controls. ${details.join('; ')}.`;
  // Legacy projections omitted the actual quote-quality errors. Do not claim
  // rejection rates exceeded limits just because this composite gate failed.
  const freshness = optionsSnapshotFreshness(data);
  if (freshness && (data.source_session_lag ?? 0) > 0) {
    return `Options evidence did not pass the latest assessed controls. ${freshness}; fresh evidence is required for new research.`;
  }
  return 'Options evidence did not pass the latest assessed freshness, quote-quality or source-capability controls.';
}

export function controlExceptionExplanation(
  exception: PublicationControl['exceptions'][number],
  data: PublicationControl['data'],
): string {
  if (exception.code === 'option_quote_quality_below_limit') {
    return quoteEligibilityExplanation(data);
  }
  if (exception.code === 'options_stale') {
    const freshness = optionsSnapshotFreshness(data);
    if (freshness) {
      return `${freshness}. This is the assessed snapshot state; new research stays blocked until fresh option evidence passes controls.`;
    }
  }
  return exception.summary?.trim() || exception.code.split('_').join(' ');
}
