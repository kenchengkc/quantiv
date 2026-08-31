'use client';

import { useState } from 'react';
import { Download } from 'lucide-react';
import { MetricHelp } from '@/components/MetricExplainer';
import type { SymbolDetail } from './symbolPageTypes';
import type { TermRow } from './ForecastPanels';
import { axisDate } from './symbolPageUtils';

export type HistoryPoint = {
  q: string;
  date: string;
  timing: string;
  /** Implied move (always ≥ 0). Null until at-the-time chain data is wired. */
  implied: number | null;
  /** Signed realized move (e.g. -0.034 = -3.4%). */
  actual: number;
  /** Non-GAAP EPS fundamentals from Finnhub. */
  epsActual: number | null;
  epsEstimate: number | null;
  /** Signed EPS surprise (e.g. 0.061 = beat by 6.1%). */
  epsSurprise: number | null;
  revActual: number | null;
  revEstimate: number | null;
  revSurprise: number | null;
};

function pickNum(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/** Build the chart series from earnings_history rows that carry an
 *  `actual` close-to-close move. `implied` is optional — when present
 *  the chart draws an implied band around zero; when absent the chart
 *  shows just the realized dot. EPS / revenue surprise come from the
 *  Finnhub overlay and may be null for older rows that predate the
 *  overlay's coverage window. Returns rows in chronological order
 *  (oldest → newest) and keeps up to three years for the local event study. */
export function buildHistorySeries(
  raw: SymbolDetail['earnings_history'] | undefined,
): HistoryPoint[] {
  if (!raw || raw.length === 0) return [];
  const usable = raw
    .filter(
      (h): h is typeof h & { actual: number } =>
        h.actual != null && Number.isFinite(h.actual),
    )
    .map((h) => {
      const d = new Date(h.date);
      const yy = String(d.getFullYear() % 100).padStart(2, '0');
      const q = h.q ?? `Q${Math.floor(d.getMonth() / 3) + 1} ${yy}`;
      const implied =
        h.implied != null && Number.isFinite(h.implied)
          ? Math.abs(h.implied)
          : null;
      return {
        q,
        date: h.date,
        timing: h.timing,
        implied,
        actual: h.actual,
        epsActual: pickNum(h.eps_actual),
        epsEstimate: pickNum(h.eps_estimate),
        epsSurprise: pickNum(h.eps_surprise_pct),
        revActual: pickNum(h.revenue_actual),
        revEstimate: pickNum(h.revenue_estimate),
        revSurprise: pickNum(h.rev_surprise_pct),
      };
    })
    .sort((a, b) => a.date.localeCompare(b.date));
  return usable.slice(-12);
}

export function medianAbsoluteHistoryMove(history: HistoryPoint[]): number | null {
  if (history.length === 0) return null;
  const moves = history.map((point) => Math.abs(point.actual)).sort((a, b) => a - b);
  const middle = Math.floor(moves.length / 2);
  return moves.length % 2 === 0
    ? (moves[middle - 1] + moves[middle]) / 2
    : moves[middle];
}

export function eventStudyEvidenceCounts(history: HistoryPoint[]) {
  const impliedObservations = history.filter(
    (point) => point.implied != null,
  ).length;
  const impliedExceedances = history.filter(
    (point) =>
      point.implied != null && Math.abs(point.actual) > point.implied,
  ).length;
  const epsObservations = history.filter(
    (point) => point.epsSurprise != null,
  ).length;
  const epsBeats = history.filter(
    (point) => point.epsSurprise != null && point.epsSurprise >= 0,
  ).length;
  return {
    impliedObservations,
    impliedExceedances,
    epsObservations,
    epsBeats,
  };
}

function csvCell(value: string | number | boolean | null): string {
  if (value == null) return '';
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function historyRowsToCsv(
  symbol: string,
  history: HistoryPoint[],
): string {
  const header = [
    'symbol',
    'quarter',
    'earnings_date',
    'timing',
    'realized_move_pct',
    'absolute_move_pct',
    'direction',
    'implied_move_pct',
    'exceeded_implied',
    'eps_actual',
    'eps_estimate',
    'eps_surprise_pct',
    'revenue_actual',
    'revenue_estimate',
    'revenue_surprise_pct',
  ];
  const rows = history.map((point) => [
    symbol,
    point.q,
    point.date,
    point.timing,
    (point.actual * 100).toFixed(6),
    (Math.abs(point.actual) * 100).toFixed(6),
    point.actual >= 0 ? 'up' : 'down',
    point.implied == null ? null : (point.implied * 100).toFixed(6),
    point.implied == null ? null : Math.abs(point.actual) > point.implied,
    point.epsActual,
    point.epsEstimate,
    point.epsSurprise == null ? null : (point.epsSurprise * 100).toFixed(6),
    point.revActual,
    point.revEstimate,
    point.revSurprise == null ? null : (point.revSurprise * 100).toFixed(6),
  ]);
  return [header, ...rows]
    .map((row) => row.map((value) => csvCell(value)).join(','))
    .join('\n');
}

function EventStudyMetric({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        padding: '9px 10px',
        border: '1px solid var(--line)',
        borderRadius: 8,
        background: 'color-mix(in oklab, var(--bg-3) 54%, transparent)',
      }}
    >
      <div
        style={{
          color: 'var(--ink-4)',
          fontSize: 8.5,
          letterSpacing: '0.1em',
          textTransform: 'uppercase',
        }}
      >
        {label}
      </div>
      <div
        className="mono tnum"
        style={{
          marginTop: 3,
          color: 'var(--ink-2)',
          fontSize: 13,
          fontWeight: 650,
        }}
      >
        {value}
      </div>
    </div>
  );
}

// EPS surprise strip — one bar per quarter, sharing column positions with
// HistoryChart so beats / misses line up vertically with their realized
// dots. Bars are green for beat, red for miss, hollow gray for missing.
// Renders nothing when the entire series lacks EPS data, so older AAPL
// payloads (pre-Finnhub overlay) degrade gracefully.
// Compact fundamentals strip showing EPS + revenue surprise as
// colored chips per quarter. Aligned with HistoryChart's columns so
// hover state highlights the same quarter across both. Rendered
// BELOW the realized-move chart (the chart is the hero — surprise
// is supporting context). Hidden entirely when the series has no
// fundamentals data.


// Owns the shared hoveredIndex so HistoryChart + SurpriseStrip light up
// the same quarter together — hovering a column in either highlights
// the matching column in the other. Header summarizes implied-beat and
// EPS-beat rates so the eye can scan the headline before reading the
// chart. Layout: chart (hero, the realized moves are the primary
// signal) → SurpriseStrip (secondary, fundamentals context).
// ---------- History block (chart + EPS strip combined) ----------
type DirectionCohort = 'all' | 'up' | 'down';

export function HistoryBlock({
  history: completeHistory,
  symbol,
}: {
  history: HistoryPoint[];
  symbol: string;
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  const [windowSize, setWindowSize] = useState<8 | 'all'>(8);
  const [direction, setDirection] = useState<DirectionCohort>('all');
  if (completeHistory.length === 0) return null;

  const windowedHistory =
    windowSize === 'all' ? completeHistory : completeHistory.slice(-windowSize);
  const directionCount = {
    up: windowedHistory.filter((point) => point.actual >= 0).length,
    down: windowedHistory.filter((point) => point.actual < 0).length,
  };
  const effectiveDirection =
    direction === 'all' || directionCount[direction] > 0 ? direction : 'all';
  const history =
    effectiveDirection === 'all'
      ? windowedHistory
      : windowedHistory.filter((point) =>
          effectiveDirection === 'up' ? point.actual >= 0 : point.actual < 0,
        );

  const hasImplied = history.some((h) => h.implied != null);
  const hasEps = history.some((h) => h.epsSurprise != null);

  const W = 700;
  const H = 240;
  const P = 48;
  const colW = (W - P * 2) / history.length;
  const max =
    Math.max(
      ...history.map((h) =>
        Math.max(h.implied != null ? h.implied : 0, Math.abs(h.actual)),
      ),
    ) * 1.18 || 0.05;
  const y = (v: number) => H / 2 - (v / max) * (H / 2 - 26);

  const epsH = 72;
  const epsCap = 0.20;
  const epsY = (v: number) =>
    epsH / 2 - (Math.max(-epsCap, Math.min(epsCap, v)) / epsCap) * (epsH / 2 - 10);

  const hovered_ = hovered != null ? history[hovered] : null;

  const {
    impliedObservations,
    impliedExceedances,
    epsObservations,
    epsBeats,
  } = eventStudyEvidenceCounts(history);
  const medianMove = medianAbsoluteHistoryMove(history) ?? 0;
  const meanMove =
    history.reduce((total, point) => total + point.actual, 0) / history.length;
  const largestMove = Math.max(...history.map((point) => Math.abs(point.actual)));

  const downloadHistory = () => {
    const csv = historyRowsToCsv(symbol, history);
    const url = URL.createObjectURL(
      new Blob([csv], { type: 'text/csv;charset=utf-8' }),
    );
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${symbol.toUpperCase()}-earnings-event-study.csv`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  return (
    <div className="qv-card">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 8,
          gap: 16,
        }}
      >
        <div>
          <span
            className="qv-pill"
            style={{
              background: 'color-mix(in oklab, var(--up) 14%, transparent)',
              color: 'var(--up)',
              borderColor: 'color-mix(in oklab, var(--up) 30%, transparent)',
            }}
          >
            Historical
          </span>
          <div className="qv-title-with-help">
            <h3
              className="serif"
              style={{
                margin: '10px 0 0',
                fontSize: 20,
                fontWeight: 700,
                letterSpacing: '-0.01em',
              }}
            >
              Event study · {history.length}{' '}
              {history.length === 1 ? 'event' : 'events'}
            </h3>
            <MetricHelp metric="history" align="left" />
          </div>
          <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 4 }}>
            {hasImplied
              ? 'Realized earnings reactions versus the option-implied range available before each event.'
              : 'Close-to-close earnings reactions; historical option ranges are not available for this cohort.'}
          </div>
        </div>
        <button
          type="button"
          onClick={downloadHistory}
          aria-label={`Export ${symbol} event study as CSV`}
          style={{
            minHeight: 32,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 7,
            padding: '6px 10px',
            border: '1px solid var(--line)',
            borderRadius: 8,
            color: 'var(--ink-3)',
            background: 'var(--bg-2)',
            fontFamily: 'inherit',
            fontSize: 10,
            cursor: 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          <Download aria-hidden="true" size={13} />
          Export rows
        </button>
      </div>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 10,
          flexWrap: 'wrap',
          marginTop: 14,
        }}
      >
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {completeHistory.length > 8
            ? ([8, 'all'] as const).map((item) => {
                const active = windowSize === item;
                return (
                  <button
                    key={item}
                    type="button"
                    aria-pressed={active}
                    onClick={() => {
                      setHovered(null);
                      setWindowSize(item);
                    }}
                    style={{
                      minHeight: 27,
                      padding: '4px 9px',
                      border: '1px solid var(--line)',
                      borderRadius: 999,
                      color: active ? 'var(--ink)' : 'var(--ink-4)',
                      background: active ? 'var(--bg-3)' : 'transparent',
                      fontFamily: 'inherit',
                      fontSize: 10,
                      cursor: 'pointer',
                    }}
                  >
                    {item === 'all' ? `${completeHistory.length}Q` : '8Q'}
                  </button>
                );
              })
            : null}
          {(['all', 'up', 'down'] as DirectionCohort[]).map((item) => {
            const active = effectiveDirection === item;
            const disabled = item !== 'all' && directionCount[item] === 0;
            return (
              <button
                key={item}
                type="button"
                aria-pressed={active}
                disabled={disabled}
                onClick={() => {
                  setHovered(null);
                  setDirection(item);
                }}
                style={{
                  minHeight: 27,
                  padding: '4px 9px',
                  border: '1px solid var(--line)',
                  borderRadius: 999,
                  color: disabled
                    ? 'var(--ink-4)'
                    : active
                      ? item === 'up'
                        ? 'var(--up)'
                        : item === 'down'
                          ? 'var(--down)'
                          : 'var(--ink)'
                      : 'var(--ink-4)',
                  opacity: disabled ? 0.45 : 1,
                  background: active ? 'var(--bg-3)' : 'transparent',
                  fontFamily: 'inherit',
                  fontSize: 10,
                  cursor: disabled ? 'not-allowed' : 'pointer',
                  textTransform: 'capitalize',
                }}
              >
                {item === 'all'
                  ? `All ${windowedHistory.length}`
                  : `${item} ${directionCount[item]}`}
              </button>
            );
          })}
        </div>
        <div
          className="mono tnum"
          style={{ display: 'flex', gap: 9, color: 'var(--ink-4)', fontSize: 9.5 }}
        >
          {hasImplied ? <span>Beat implied {impliedExceedances}/{impliedObservations}</span> : null}
          {hasEps ? <span>EPS beat {epsBeats}/{epsObservations}</span> : null}
        </div>
      </div>

      <div
        className="qv-m-2col"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 8,
          marginTop: 10,
        }}
      >
        <EventStudyMetric label="Observations" value={String(history.length)} />
        <EventStudyMetric label="Median |move|" value={percent(medianMove)} />
        <EventStudyMetric
          label="Mean move"
          value={`${meanMove >= 0 ? '+' : ''}${percent(meanMove)}`}
        />
        <EventStudyMetric label="Largest |move|" value={percent(largestMove)} />
      </div>

      {/* Wrap the chart in a relative container so the hover tooltip can
          anchor to the chart itself, regardless of whether the EPS strip
          below renders. (Earlier, the tooltip lived inside the EPS strip
          and was double-gated on EPS data, so for most tickers hover did
          nothing.) */}
      <div style={{ position: 'relative', marginTop: 10 }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', height: 'auto' }}
      >
        {/* Alternating column bands give the chart visual rhythm,
            especially important when the implied bands are missing and
            the only marks are realized dots floating on the zero line. */}
        {history.map((_, i) =>
          i % 2 === 1 ? (
            <rect
              key={`band-${i}`}
              x={P + colW * i}
              y={0}
              width={colW}
              height={H - 22}
              fill="var(--ink)"
              opacity={0.018}
            />
          ) : null,
        )}

        <line x1={P} x2={W - P} y1={H / 2} y2={H / 2} stroke="var(--line)" />
        <line
          x1={P}
          x2={W - P}
          y1={y(max)}
          y2={y(max)}
          stroke="var(--line)"
          strokeDasharray="2 4"
          opacity={0.5}
        />
        <line
          x1={P}
          x2={W - P}
          y1={y(-max)}
          y2={y(-max)}
          stroke="var(--line)"
          strokeDasharray="2 4"
          opacity={0.5}
        />

        {/* Faint trajectory line connecting realized dots — only when
            implied bands are absent, so the chart still feels composed
            in the no-options-history case. */}
        {!hasImplied && history.length > 1 && (
          <path
            d={history
              .map((h, i) => {
                const cx = P + colW * i + colW / 2;
                const cy = y(h.actual);
                return `${i === 0 ? 'M' : 'L'} ${cx} ${cy}`;
              })
              .join(' ')}
            stroke="var(--ink-4)"
            strokeWidth="1"
            strokeDasharray="3 4"
            fill="none"
            opacity={0.6}
          />
        )}
        <text
          x={P - 8}
          y={y(max) + 3}
          textAnchor="end"
          fill="var(--ink-3)"
          fontSize="9"
          style={{ fontFamily: 'var(--font-jetbrains-mono), ui-monospace, monospace' }}
        >
          +{(max * 100).toFixed(0)}%
        </text>
        <text
          x={P - 8}
          y={y(-max) + 3}
          textAnchor="end"
          fill="var(--ink-3)"
          fontSize="9"
          style={{ fontFamily: 'var(--font-jetbrains-mono), ui-monospace, monospace' }}
        >
          −{(max * 100).toFixed(0)}%
        </text>
        <text
          x={P - 8}
          y={H / 2 + 3}
          textAnchor="end"
          fill="var(--ink-3)"
          fontSize="9"
          style={{ fontFamily: 'var(--font-jetbrains-mono), ui-monospace, monospace' }}
        >
          0%
        </text>

        {history.map((h, i) => {
          const cx = P + colW * i + colW / 2;
          const impliedDecimal = h.implied ?? 0;
          const actualDecimal = h.actual;
          const up = actualDecimal >= 0;
          const moveColor = up ? 'var(--up)' : 'var(--down)';
          const beat = h.implied != null && Math.abs(actualDecimal) > impliedDecimal;
          const active = hovered === i;
          return (
            <g
              key={`${h.q}-${h.date}`}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
              style={{ cursor: 'default' }}
            >
              <rect x={cx - colW / 2} y={0} width={colW} height={H} fill="transparent" />
              {h.implied != null && (
                <>
                  <rect
                    x={cx - 13}
                    y={y(impliedDecimal)}
                    width="26"
                    height={Math.max(1, y(-impliedDecimal) - y(impliedDecimal))}
                    fill={
                      active
                        ? 'color-mix(in oklab, var(--brand-blue-1) 32%, transparent)'
                        : 'color-mix(in oklab, var(--brand-blue-1) 18%, transparent)'
                    }
                    rx="2"
                  />
                  <line
                    x1={cx - 13}
                    x2={cx + 13}
                    y1={y(impliedDecimal)}
                    y2={y(impliedDecimal)}
                    stroke="var(--brand-blue-1)"
                    strokeWidth="1.2"
                  />
                  <line
                    x1={cx - 13}
                    x2={cx + 13}
                    y1={y(-impliedDecimal)}
                    y2={y(-impliedDecimal)}
                    stroke="var(--brand-blue-1)"
                    strokeWidth="1.2"
                  />
                </>
              )}
              {beat && (
                <circle
                  cx={cx}
                  cy={y(actualDecimal)}
                  r="7"
                  fill="none"
                  stroke={moveColor}
                  strokeWidth="1.2"
                  opacity="0.5"
                />
              )}
              <circle
                cx={cx}
                cy={y(actualDecimal)}
                // Larger dots when no implied bands so the realized
                // marker reads as the chart's primary signal.
                r={active ? (hasImplied ? 4.6 : 5.4) : hasImplied ? 3.8 : 4.6}
                fill={moveColor}
                style={{ transition: 'r 160ms ease' }}
              />
              <text
                x={cx}
                y={H - 8}
                textAnchor="middle"
                fill={active ? 'var(--ink)' : 'var(--ink-2)'}
                fontSize="9.5"
                style={{ fontFamily: 'var(--font-jetbrains-mono), ui-monospace, monospace' }}
                fontWeight={active ? 700 : 500}
              >
                {h.q}
              </text>
            </g>
          );
        })}
      </svg>

      {hovered_ && hovered != null && (() => {
        const cx = P + colW * hovered + colW / 2;
        const cy = y(hovered_.actual);
        const placeRight = cx < W * 0.64;
        return (
          <div
            style={{
              position: 'absolute',
              left: `${(cx / W) * 100}%`,
              top: `${Math.max(16, Math.min(82, (cy / H) * 100))}%`,
              transform: placeRight
                ? 'translate(12px, -50%)'
                : 'translate(calc(-100% - 12px), -50%)',
              width: 172,
              background: 'var(--bg-3)',
              border: '1px solid var(--line-2)',
              padding: '7px 9px',
              borderRadius: 8,
              fontSize: 9.5,
              lineHeight: 1.35,
              color: 'var(--ink-2)',
              pointerEvents: 'none',
              boxShadow: '0 12px 36px rgba(0,0,0,.5)',
            }}
          >
            <div
              style={{
                fontSize: 8.5,
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
                color: 'var(--ink-4)',
              }}
            >
              {hovered_.q}
            </div>
            <div
              className="serif tnum"
              style={{
                fontSize: 13,
                color: hovered_.actual >= 0 ? 'var(--up)' : 'var(--down)',
                marginTop: 2,
              }}
            >
              {hovered_.actual >= 0 ? '+' : ''}
              {(hovered_.actual * 100).toFixed(1)}%
            </div>
            <div
              className="mono tnum"
              style={{ fontSize: 8.5, color: 'var(--ink-4)', marginTop: 1 }}
            >
              realized
            </div>
            {hovered_.epsSurprise != null && (
              <div
                className="mono tnum"
                style={{
                  borderTop: '1px solid var(--line)',
                  marginTop: 6,
                  paddingTop: 5,
                  fontSize: 9,
                }}
              >
                <div style={{ color: 'var(--ink-3)' }}>
                  EPS {hovered_.epsActual?.toFixed(2) ?? '–'} vs{' '}
                  {hovered_.epsEstimate?.toFixed(2) ?? '–'}
                </div>
                <div
                  style={{
                    color: hovered_.epsSurprise >= 0 ? 'var(--up)' : 'var(--down)',
                    marginTop: 2,
                  }}
                >
                  {hovered_.epsSurprise >= 0 ? '+' : ''}
                  {(hovered_.epsSurprise * 100).toFixed(1)}% surprise
                </div>
              </div>
            )}
          </div>
        );
      })()}
      </div>

      {/* EPS surprise strip (hidden when no fundamentals data) */}
      {hasEps && (
        <div style={{ marginTop: 14, position: 'relative' }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 12,
              color: 'var(--ink-3)',
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              marginBottom: 6,
            }}
          >
            <span>EPS surprise</span>
            <span style={{ color: 'var(--ink-4)', letterSpacing: '0.06em', textTransform: 'none' }}>
              vs sell-side consensus
            </span>
          </div>
          <svg
            viewBox={`0 0 ${W} ${epsH}`}
            style={{ width: '100%', height: 'auto' }}
          >
            <line x1={P} x2={W - P} y1={epsH / 2} y2={epsH / 2} stroke="var(--line)" />
            {history.map((h, i) => {
              const cx = P + colW * i + colW / 2;
              const s = h.epsSurprise;
              if (s == null) {
                return (
                  <line
                    key={`eps-${h.q}-${h.date}`}
                    x1={cx - 8}
                    x2={cx + 8}
                    y1={epsH / 2}
                    y2={epsH / 2}
                    stroke="var(--ink-4)"
                    strokeWidth="1"
                    opacity="0.45"
                  />
                );
              }
              const beat = s >= 0;
              const top = beat ? epsY(s) : epsH / 2;
              const bot = beat ? epsH / 2 : epsY(s);
              const height = Math.max(2, bot - top);
              const active = hovered === i;
              return (
                <g
                  key={`eps-${h.q}-${h.date}`}
                  onMouseEnter={() => setHovered(i)}
                  onMouseLeave={() => setHovered(null)}
                >
                  <rect
                    x={cx - 12}
                    y={top}
                    width="24"
                    height={height}
                    fill={beat ? 'var(--up)' : 'var(--down)'}
                    opacity={hovered == null || active ? 0.85 : 0.4}
                    rx="2"
                  />
                </g>
              );
            })}
          </svg>
        </div>
      )}

      <div
        style={{
          marginTop: 14,
          display: 'flex',
          gap: 18,
          fontSize: 13,
          color: 'var(--ink-3)',
          flexWrap: 'wrap',
        }}
      >
        {hasImplied && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 18,
                height: 8,
                background: 'color-mix(in oklab, var(--brand-blue-1) 22%, transparent)',
                borderTop: '1px solid var(--brand-blue-1)',
                borderBottom: '1px solid var(--brand-blue-1)',
              }}
            />
            Implied range
          </span>
        )}
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: 999, background: 'var(--up)' }} />
          Realized (up)
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: 999, background: 'var(--down)' }} />
          Realized (down)
        </span>
        {hasImplied && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 12,
                height: 12,
                borderRadius: 999,
                border: '1.2px solid var(--ink-3)',
                display: 'inline-block',
              }}
            />
            Beat implied
          </span>
        )}
      </div>
    </div>
  );
}

// ---------- Greeks panel ----------
export function GreeksPanel({ rows }: { rows: TermRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="qv-card">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 16,
        }}
      >
        <div>
          <span
            className="qv-pill"
            style={{
              background: 'color-mix(in oklab, var(--accent) 14%, transparent)',
              color: 'var(--accent-hi)',
              borderColor: 'color-mix(in oklab, var(--accent) 30%, transparent)',
            }}
          >
            ATM greeks
          </span>
          <div className="qv-title-with-help">
            <h3
              className="serif"
              style={{
                margin: '10px 0 0',
                fontSize: 20,
                fontWeight: 700,
                letterSpacing: '-0.01em',
              }}
            >
              Greeks by expiry
            </h3>
            <MetricHelp metric="greeks" align="left" />
          </div>
          <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 4 }}>
            ATM call · delta hedge ratios, sensitivity to vol and time.
          </div>
        </div>
      </div>

      <div className="qv-m-table-wrap" style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--line)' }}>
              {['Expiry', 'DTE', 'ATM IV', 'Δ Delta', 'Γ Gamma', '𝜈 Vega', 'Θ Theta', 'Straddle'].map(
                (h, i) => (
                  <th
                    key={h}
                    className={i === 0 ? 'qv-m-sticky-cell' : undefined}
                    style={{
                      textAlign: i === 0 ? 'left' : 'right',
                      padding: '10px 12px',
                      fontSize: 11.5,
                      letterSpacing: '0.16em',
                      textTransform: 'uppercase',
                      color: 'var(--ink-3)',
                      fontWeight: 500,
                    }}
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const baseBg = r.isEarnings
                ? 'color-mix(in oklab, var(--brand-blue-1) 5%, transparent)'
                : 'transparent';
              return (
                <tr
                  key={r.expiration}
                  style={{
                    borderBottom: '1px solid var(--line)',
                    background: baseBg,
                    transition: 'background 140ms ease',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = 'var(--bg-3)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = baseBg;
                  }}
                >
                  <td className="qv-m-sticky-cell" style={{ padding: '12px 12px' }}>
                    <div
                      className="serif"
                      style={{
                        fontSize: 14,
                        fontWeight: 600,
                        color: 'var(--ink)',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                      }}
                    >
                      {axisDate(r.expiration)}
                      {r.isEarnings && (
                        <span
                          className="qv-pill warm"
                          style={{ fontSize: 8.5, padding: '2px 6px' }}
                        >
                          Earnings
                        </span>
                      )}
                    </div>
                    <div
                      className="mono"
                      style={{
                        fontSize: 12,
                        color: 'var(--ink-4)',
                        marginTop: 2,
                        letterSpacing: '0.08em',
                        textTransform: 'uppercase',
                      }}
                    >
                      K ${r.strike.toFixed(0)}
                    </div>
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--ink-2)' }}
                  >
                    {r.dte}d
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--ink)' }}
                  >
                    {(r.iv * 100).toFixed(2)}%
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--ink-2)' }}
                  >
                    {r.delta != null ? r.delta.toFixed(3) : '–'}
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--ink-2)' }}
                  >
                    {r.gamma != null ? r.gamma.toFixed(4) : '–'}
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--ink-2)' }}
                  >
                    {r.vega != null ? r.vega.toFixed(2) : '–'}
                  </td>
                  <td
                    className="mono tnum"
                    style={{ textAlign: 'right', padding: '12px 12px', color: 'var(--down)' }}
                  >
                    {r.theta != null ? r.theta.toFixed(2) : '–'}
                  </td>
                  <td
                    className="mono tnum"
                    style={{
                      textAlign: 'right',
                      padding: '12px 12px',
                      color: 'var(--ink)',
                      fontWeight: 600,
                    }}
                  >
                    {r.straddle > 0 ? `$${r.straddle.toFixed(2)}` : '–'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}


// ---------- Small bits ----------


/** Lightweight section wrapper. Detail pages used to mount sections hidden
 *  and reveal them after IntersectionObserver ran, which caused a visible
 *  one-frame "hero only" flash on ticker-page loads. Keep the same callsite
 *  shape, but render visible from the first paint. */
