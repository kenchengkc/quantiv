import type { PredictRequestBody, SymbolJson } from './_shared';

/** Bind public inference to the event selected by verified nightly publication. */
export function guardPublishedPrediction(
  request: PredictRequestBody,
  nightly: SymbolJson | null,
  policyActive = false,
): { request: PredictRequestBody; holdReason: string | null; publicationBound: boolean } {
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date());
  if (policyActive && request.earnings_date != null && request.earnings_date < today) {
    return { request, holdReason: null, publicationBound: false };
  }
  const expectedMove = nightly?.expected_move;
  const status = expectedMove?.ml_publication_status;
  // Older and historical artifacts predate per-event publication eligibility.
  if (status == null) {
    return { request, holdReason: policyActive ? 'event_not_eligible' : null,
      publicationBound: false };
  }
  if (status !== 'eligible') {
    return { request, holdReason: expectedMove?.ml_publication_reason || 'publication_held',
      publicationBound: false };
  }

  const eventDate = typeof expectedMove?.earnings_date === 'string'
    ? expectedMove.earnings_date.slice(0, 10) : null;
  if (!eventDate || !/^\d{4}-\d{2}-\d{2}$/.test(eventDate)) {
    return { request, holdReason: 'approved_event_unavailable', publicationBound: false };
  }
  if (request.earnings_date != null && request.earnings_date !== eventDate) {
    return { request, holdReason: 'event_not_eligible', publicationBound: false };
  }
  if (expectedMove?.model_horizon !== request.horizon_days) {
    return { request, holdReason: 'horizon_not_eligible', publicationBound: false };
  }
  // An omitted date must not allow the backend to choose another stored event.
  return { request: { ...request, earnings_date: eventDate }, holdReason: null, publicationBound: true };
}

function sameScoringTime(approved: unknown, received: unknown): boolean {
  if (typeof approved !== 'string' || typeof received !== 'string') return false;
  const timestamp = /T\d{2}:\d{2}:\d{2}(?:\.(\d+))?(?:Z|[+-]\d{2}:\d{2})$/;
  const first = timestamp.exec(approved);
  const second = timestamp.exec(received);
  if (!first || !second || !Number.isFinite(Date.parse(approved))) return false;
  // Date.parse truncates microseconds; preserve the full scoring clock too.
  return Date.parse(approved) === Date.parse(received)
    && (first[1] || '').replace(/0+$/, '') === (second[1] || '').replace(/0+$/, '');
}

export function matchesPublishedPrediction(
  request: PredictRequestBody,
  nightly: SymbolJson | null,
  response: unknown,
): boolean {
  if (!response || typeof response !== 'object') return false;
  const received = response as Record<string, unknown>;
  const approved = nightly?.expected_move;
  const bundle = approved?.forecast_model_bundle_id;
  return (received.source === 'computed' || received.source === 'cached')
    && received.symbol === request.symbol
    && received.earnings_date === request.earnings_date
    && received.horizon_days === request.horizon_days
    && typeof approved?.ml_snapshot_date === 'string'
    && received.feature_snapshot_date === approved.ml_snapshot_date
    && sameScoringTime(approved.forecast_scored_at, received.forecast_scored_at)
    && typeof bundle === 'string' && /^[a-f0-9]{64}$/.test(bundle)
    && received.model_bundle_id === bundle;
}

export function heldPrediction(request: PredictRequestBody, reason: string) {
  return {
    ok: false,
    symbol: request.symbol,
    horizon_days: request.horizon_days,
    earnings_date: request.earnings_date ?? null,
    error_status: 404,
    error: 'ML forecast publication held',
    publication_status: 'held',
    publication_reason: reason,
  };
}
