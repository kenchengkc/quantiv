'use client';

import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { MouseEvent } from 'react';
import { MetricHelp } from '@/components/MetricExplainer';
import type { LivePredictionStatus, PredictionMode, Straddle } from './symbolPageTypes';
import { axisDate, formatSvgNumber, normCDF, parseLocalDate, shortDate } from './symbolPageUtils';

export function InteractiveBar({
  spot,
  em,
  emIV,
  atmIV,
  dte,
}: {
  spot: number;
  em: number;
  emIV: number;
  atmIV: number;
  dte: number;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<
    { x: number; pct: number; price: number; prob: number } | null
  >(null);
  const [pinned, setPinned] = useState<
    { x: number; pct: number; price: number; prob: number } | null
  >(null);

  const widest = Math.max(em, emIV, 0.01) * 1.5;
  const minPct = -widest;
  const maxPct = widest;
  const toX = (pct: number) => ((pct - minPct) / (maxPct - minPct)) * 100;

  const sigma = (atmIV || 0.3) * Math.sqrt((dte || 28) / 365);

  // Two-sided log-normal: P(|S/S0 − 1| ≥ |x|)
  //   = P(S ≥ S0(1 + |x|)) + P(S ≤ S0(1 − |x|))
  //   = [1 − Φ(log(1+|x|)/σ)] + Φ(log(1−|x|)/σ)
  // The previous form (`2 (1 − Φ(z_up))`) treated the two tails as
  // symmetric in `pct`, which slightly overstates the probability for
  // any non-trivial move because the downside tail in `pct` is fatter
  // in log-space.
  const probBeyond = useCallback(
    (pct: number) => {
      const ax = Math.abs(pct);
      if (ax === 0) return 1;
      if (ax >= 1) return 0; // can't physically lose 100%+
      const zUp = Math.log(1 + ax) / sigma;
      const zDn = Math.log(1 - ax) / sigma; // negative
      return (1 - normCDF(zUp)) + normCDF(zDn);
    },
    [sigma],
  );

  const onMove = (e: MouseEvent) => {
    if (!wrapRef.current) return;
    const rect = wrapRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const pct = minPct + x * (maxPct - minPct);
    const price = spot * (1 + pct);
    const prob = probBeyond(pct);
    setHover({ x: x * 100, pct, price, prob });
  };
  const onLeave = () => setHover(null);
  const onClick = () => {
    if (hover) setPinned(hover);
  };

  // PDF of the *return* `pct = S/S0 − 1` under log-normal. By change of
  // variable from log-return to simple-return we pick up the Jacobian
  // `1 / (1 + pct)`, which makes the curve correctly skewed (the
  // downside tail is fatter in pct-space than the upside). The curve
  // is normalized to peak at 1.0 just for display.
  const density = useMemo(() => {
    const n = 80;
    const arr: { x: number; y: number }[] = [];
    for (let i = 0; i <= n; i++) {
      const x = i / n;
      const pct = minPct + x * (maxPct - minPct);
      // Guard against pct ≤ −1 (would blow up log); our visible
      // window always sits well above −1, so this is just defensive.
      if (1 + pct <= 0) {
        arr.push({ x: x * 100, y: 0 });
        continue;
      }
      const z = Math.log(1 + pct) / sigma;
      const pdf =
        Math.exp(-0.5 * z * z) / Math.sqrt(2 * Math.PI) / sigma / (1 + pct);
      arr.push({ x: x * 100, y: pdf });
    }
    const maxP = Math.max(...arr.map((p) => p.y));
    return arr.map((p) => ({ x: p.x, y: maxP > 0 ? p.y / maxP : 0 }));
  }, [sigma, minPct, maxPct]);
  const densityAreaPath = useMemo(
    () =>
      `M 0,90 ${density
        .map((p) => `L ${formatSvgNumber(p.x)},${formatSvgNumber(90 - p.y * 76)}`)
        .join(' ')} L 100,90 Z`,
    [density],
  );
  const densityStrokePath = useMemo(
    () =>
      density
        .map(
          (p, i) =>
            `${i ? 'L' : 'M'}${formatSvgNumber(p.x)},${formatSvgNumber(90 - p.y * 76)}`,
        )
        .join(' '),
    [density],
  );

  const emLow = toX(-em);
  const emHigh = toX(em);
  const ivLow = toX(-emIV);
  const ivHigh = toX(emIV);
  const spotX = toX(0);

  const current = hover || pinned;

  return (
    <div style={{ marginTop: 18 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 12,
          color: 'var(--ink-4)',
          letterSpacing: '0.1em',
          textTransform: 'uppercase',
          marginBottom: 10,
        }}
      >
        <span className="mono tnum">
          ${(spot * (1 + minPct)).toFixed(2)} · {(minPct * 100).toFixed(1)}%
        </span>
        <span>Spot ${spot.toFixed(2)}</span>
        <span className="mono tnum">
          ${(spot * (1 + maxPct)).toFixed(2)} · +{(maxPct * 100).toFixed(1)}%
        </span>
      </div>

      <div
        ref={wrapRef}
        onMouseMove={onMove}
        onMouseLeave={onLeave}
        onClick={onClick}
        style={{
          position: 'relative',
          height: 110,
          cursor: 'crosshair',
          userSelect: 'none',
        }}
      >
        <svg
          viewBox="0 0 100 110"
          preserveAspectRatio="none"
          width="100%"
          height="110"
          style={{ position: 'absolute', inset: 0 }}
        >
          <defs>
            <linearGradient id="den-grad" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--brand-blue-1)" stopOpacity="0.5" />
              <stop offset="100%" stopColor="var(--brand-blue-1)" stopOpacity="0" />
            </linearGradient>
            <linearGradient id="den-stroke" x1="0" x2="1" y1="0" y2="0">
              <stop offset="0%" stopColor="var(--down)" stopOpacity="0.9" />
              <stop offset="50%" stopColor="var(--brand-blue-1)" />
              <stop offset="100%" stopColor="var(--up)" stopOpacity="0.9" />
            </linearGradient>
          </defs>
          <path
            d={densityAreaPath}
            fill="url(#den-grad)"
          />
          <path
            d={densityStrokePath}
            stroke="url(#den-stroke)"
            strokeWidth="0.8"
            fill="none"
            vectorEffect="non-scaling-stroke"
          />
        </svg>

        {/* Straddle band */}
        <div
          style={{
            position: 'absolute',
            top: 68,
            height: 14,
            left: `${emLow}%`,
            width: `${emHigh - emLow}%`,
            background:
              'linear-gradient(90deg, var(--brand-blue-2), var(--brand-blue-1))',
            borderRadius: 999,
            boxShadow:
              '0 4px 14px color-mix(in oklab, var(--brand-blue-1) 35%, transparent)',
          }}
        />
        {/* IV band ticks */}
        <div
          style={{
            position: 'absolute',
            top: 64,
            height: 22,
            left: `${ivLow}%`,
            width: `${ivHigh - ivLow}%`,
            borderLeft: '1px dashed var(--ink-3)',
            borderRight: '1px dashed var(--ink-3)',
            pointerEvents: 'none',
          }}
        />

        {/* Baseline */}
        <div
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            top: 75,
            height: 1,
            background: 'var(--line)',
          }}
        />

        {/* Spot marker */}
        <div
          style={{
            position: 'absolute',
            left: `${spotX}%`,
            top: 56,
            bottom: 18,
            width: 1,
            background: 'var(--ink-2)',
            transform: 'translateX(-0.5px)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: `${spotX}%`,
            bottom: 0,
            transform: 'translateX(-50%)',
            fontSize: 12,
            color: 'var(--ink-2)',
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            fontWeight: 600,
          }}
        >
          Spot
        </div>

        <div
          className="mono tnum"
          style={{
            position: 'absolute',
            top: 52,
            left: `${emLow}%`,
            transform: 'translateX(-50%)',
            fontSize: 13,
            color: 'var(--down)',
            fontWeight: 600,
          }}
        >
          −{(em * 100).toFixed(1)}%
        </div>
        <div
          className="mono tnum"
          style={{
            position: 'absolute',
            top: 52,
            left: `${emHigh}%`,
            transform: 'translateX(-50%)',
            fontSize: 13,
            color: 'var(--up)',
            fontWeight: 600,
          }}
        >
          +{(em * 100).toFixed(1)}%
        </div>

        {hover && (
          <div
            style={{
              position: 'absolute',
              left: `${hover.x}%`,
              top: 0,
              bottom: 0,
              width: 1,
              background: 'var(--ink)',
              transform: 'translateX(-0.5px)',
              pointerEvents: 'none',
            }}
          />
        )}
        {pinned && !hover && (
          <div
            style={{
              position: 'absolute',
              left: `${pinned.x}%`,
              top: 0,
              bottom: 0,
              width: 1,
              background: 'var(--flag)',
              transform: 'translateX(-0.5px)',
              pointerEvents: 'none',
            }}
          />
        )}

        {current && (
          <div
            style={{
              position: 'absolute',
              left: `${Math.min(82, Math.max(10, current.x))}%`,
              top: -10,
              transform: 'translate(-50%, -100%)',
              background: 'var(--bg-3)',
              border: '1px solid var(--line-2)',
              padding: '8px 12px',
              borderRadius: 8,
              fontSize: 11,
              whiteSpace: 'nowrap',
              boxShadow: '0 12px 36px rgba(0,0,0,.5)',
              pointerEvents: 'none',
            }}
          >
            <div
              className="serif tnum"
              style={{ fontSize: 18, color: 'var(--ink)', lineHeight: 1, fontWeight: 700 }}
            >
              ${current.price.toFixed(2)}
            </div>
            <div
              className="mono tnum"
              style={{
                fontSize: 10.5,
                marginTop: 4,
                color: current.pct >= 0 ? 'var(--up)' : 'var(--down)',
              }}
            >
              {current.pct >= 0 ? '+' : ''}
              {(current.pct * 100).toFixed(2)}% from spot
            </div>
            <div
              className="mono tnum"
              style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 2 }}
            >
              {(current.prob * 100).toFixed(1)}% chance to move this far
            </div>
          </div>
        )}
      </div>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginTop: 16,
          fontSize: 13,
          color: 'var(--ink-3)',
        }}
      >
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 18,
                height: 4,
                borderRadius: 2,
                background:
                  'linear-gradient(90deg, var(--brand-blue-2), var(--brand-blue-1))',
              }}
            />
            Straddle band
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 18,
                height: 10,
                borderLeft: '1px dashed var(--ink-3)',
                borderRight: '1px dashed var(--ink-3)',
              }}
            />
            IV band
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 18,
                height: 4,
                borderRadius: 2,
                background: 'linear-gradient(90deg, var(--down), var(--brand-blue-1), var(--up))',
              }}
            />
            Log-normal density
          </span>
        </div>
        <span style={{ fontStyle: 'italic' }}>Hover for probability · click to pin</span>
      </div>
    </div>
  );
}

// Forecast distribution — quantile band card. Plots P10/P25/P50/P75/P90 of
// the model's |move| distribution against a 0..max axis, with a straddle
// reference tick. A 5-cell grid beneath shows each quintile's value with
// the corresponding price range.
export function QuantileBand({
  q,
  straddleAbs,
  spot,
  mode,
  onModeChange,
  liveDisabled,
  liveStatus,
  pointPct,
  modelMeta,
  unavailableReason,
}: {
  q: { p10: number; p25: number; p50: number; p75: number; p90: number };
  straddleAbs: number;
  spot: number;
  mode: PredictionMode;
  onModeChange: (mode: PredictionMode) => void;
  liveDisabled: boolean;
  liveStatus: LivePredictionStatus;
  pointPct: number | null;
  modelMeta: string;
  unavailableReason: string | null;
}) {
  const max = Math.max(q.p90, straddleAbs, 0.001) * 1.08;
  const pct = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  const cells: Array<[string, number, string]> = [
    ['P10', q.p10, 'var(--ink-3)'],
    ['P25', q.p25, 'var(--ink-2)'],
    ['P50', q.p50, 'var(--brand-blue-1)'],
    ['P75', q.p75, 'var(--ink-2)'],
    ['P90', q.p90, 'var(--ink-3)'],
  ];
  return (
    <div className="qv-card">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: 16,
          // Was 14, which left no room for the STRADDLE callout that sits
          // at `top: -22` above the bar. The callout was punching up into
          // the "LightGBM ensemble · range of plausible absolute moves on
          // print day" line. 32 leaves a clear ~10px gap below the
          // callout's top edge.
          marginBottom: 32,
        }}
      >
        <div>
          <span className="qv-pill warm">
            {mode === 'live' && liveStatus === 'ready' ? 'Spot-updated' : 'ML model'}
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
              Forecast distribution
            </h3>
            <MetricHelp metric="forecastDistribution" align="left" />
          </div>
          <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 4 }}>
            {modelMeta}
          </div>
          {mode === 'live' && liveStatus === 'unavailable' && unavailableReason && (
            <div style={{ fontSize: 12, color: 'var(--ink-4)', marginTop: 6 }}>
              Spot update unavailable · showing nightly snapshot. {unavailableReason}
            </div>
          )}
        </div>
        <div style={{ display: 'grid', justifyItems: 'end', gap: 10 }}>
          <div
            role="tablist"
            aria-label="Forecast input mode"
            style={{
              display: 'inline-grid',
              gridTemplateColumns: '1fr 1fr',
              minWidth: 164,
              padding: 3,
              borderRadius: 8,
              border: '1px solid var(--line)',
              background: 'var(--bg-2)',
            }}
          >
            {(['snapshot', 'live'] as PredictionMode[]).map((item) => {
              const active = mode === item;
              const disabled = item === 'live' && liveDisabled;
              return (
                <button
                  key={item}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  disabled={disabled}
                  onClick={() => onModeChange(item)}
                  title={disabled ? 'No model horizon is available for this snapshot' : undefined}
                  style={{
                    minHeight: 28,
                    border: 'none',
                    borderRadius: 6,
                    background: active ? 'var(--bg-3)' : 'transparent',
                    color: disabled
                      ? 'var(--ink-4)'
                      : active
                        ? 'var(--ink)'
                        : 'var(--ink-3)',
                    fontSize: 12,
                    fontWeight: active ? 700 : 500,
                    cursor: disabled ? 'not-allowed' : 'pointer',
                    fontFamily: 'inherit',
                  }}
                >
                  {item === 'snapshot'
                    ? 'Nightly'
                    : liveStatus === 'loading'
                      ? 'Updating…'
                      : 'Spot-updated'}
                </button>
              );
            })}
          </div>
          <div
            className="mono tnum"
            style={{ textAlign: 'right', fontSize: 13, color: 'var(--ink-3)' }}
          >
            {pointPct != null && (
              <div>
                Point <span style={{ color: 'var(--ink-2)' }}>±{(pointPct * 100).toFixed(1)}%</span>
              </div>
            )}
            <div style={{ marginTop: pointPct != null ? 2 : 0 }}>
              Median <span style={{ color: 'var(--ink-2)' }}>±{(q.p50 * 100).toFixed(1)}%</span>
            </div>
            <div style={{ marginTop: 2 }}>
              80% band{' '}
              <span style={{ color: 'var(--ink-2)' }}>
                {(q.p10 * 100).toFixed(1)}–{(q.p90 * 100).toFixed(1)}%
              </span>
            </div>
          </div>
        </div>
      </div>

      <div
        style={{
          position: 'relative',
          height: 44,
          background: 'var(--bg-3)',
          borderRadius: 8,
          overflow: 'visible',
        }}
      >
        <div
          style={{
            position: 'absolute',
            left: pct(q.p10),
            width: `calc(${pct(q.p90)} - ${pct(q.p10)})`,
            top: 0,
            bottom: 0,
            background: 'color-mix(in oklab, var(--brand-blue-1) 18%, transparent)',
            borderRadius: 8,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: pct(q.p25),
            width: `calc(${pct(q.p75)} - ${pct(q.p25)})`,
            top: 0,
            bottom: 0,
            background: 'color-mix(in oklab, var(--brand-blue-1) 38%, transparent)',
            borderRadius: 8,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: pct(q.p50),
            top: -4,
            bottom: -4,
            width: 2,
            background: 'var(--brand-blue-1)',
            boxShadow:
              '0 0 12px color-mix(in oklab, var(--brand-blue-1) 60%, transparent)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: pct(straddleAbs),
            top: -10,
            bottom: -10,
            width: 1,
            background: 'var(--flag)',
          }}
        />
        <div
          className="mono tnum"
          style={{
            position: 'absolute',
            left: pct(straddleAbs),
            top: -22,
            transform: 'translateX(-50%)',
            fontSize: 9.5,
            color: 'var(--flag)',
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
          }}
        >
          Straddle
        </div>
        <div
          className="mono tnum"
          style={{
            position: 'absolute',
            left: pct(q.p50),
            bottom: -22,
            transform: 'translateX(-50%)',
            fontSize: 9.5,
            color: 'var(--brand-blue-1)',
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            fontWeight: 600,
          }}
        >
          P50
        </div>
      </div>

      <div
        style={{
          marginTop: 38,
          display: 'grid',
          gridTemplateColumns: 'repeat(5, 1fr)',
          gap: 12,
          paddingTop: 12,
          borderTop: '1px solid var(--line)',
        }}
      >
        {cells.map(([label, val, tone]) => (
          <div key={label}>
            <div
              style={{
                fontSize: 11.5,
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                color: 'var(--ink-4)',
                fontWeight: 600,
              }}
            >
              {label}
            </div>
            <div
              className="serif tnum"
              style={{
                fontSize: 18,
                color: tone,
                fontWeight: 700,
                marginTop: 2,
              }}
            >
              ±{(val * 100).toFixed(1)}%
            </div>
            <div
              className="mono tnum"
              style={{ fontSize: 12, color: 'var(--ink-4)', marginTop: 2 }}
            >
              ${(spot * (1 - val)).toFixed(0)}–${(spot * (1 + val)).toFixed(0)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Adapter row consumed by TermFan + GreeksPanel. Built once from
 *  `data.straddle_features` and tagged with `isEarnings` so the
 *  earnings expiry can be highlighted. */
export type TermRow = {
  expiration: string;
  dte: number;
  iv: number;        // ATM IV (decimal)
  emPct: number;     // straddle-implied move (decimal fraction)
  straddle: number;  // $
  strike: number;    // $
  delta: number | null;
  gamma: number | null;
  vega: number | null;
  theta: number | null;
  isEarnings: boolean;
};

export function buildTermRows(
  expiries: Straddle[],
  earningsExpiration: string | null,
): TermRow[] {
  return expiries
    .filter(
      (e) =>
        e.em_straddle_pct != null &&
        Number.isFinite(e.em_straddle_pct) &&
        e.atm_iv != null &&
        Number.isFinite(e.atm_iv),
    )
    .sort(
      (a, b) =>
        parseLocalDate(a.expiration).getTime() -
        parseLocalDate(b.expiration).getTime(),
    )
    .map((e) => ({
      expiration: e.expiration,
      dte: e.dte,
      iv: e.atm_iv as number,
      emPct: e.em_straddle_pct as number,
      straddle: e.straddle_mid ?? 0,
      strike: e.atm_strike,
      delta: e.call_delta,
      gamma: e.call_gamma,
      vega: e.call_vega,
      theta: e.call_theta,
      isEarnings: earningsExpiration != null && e.expiration === earningsExpiration,
    }));
}

// Term-structure fan — two lines from today's spot: spot×(1±EM) at each
// expiry. X axis is days-to-expiry. Hover dots show DTE, IV, EM, straddle.
export function TermFan({ rows, spot }: { rows: TermRow[]; spot: number }) {
  const [hovered, setHovered] = useState<number | null>(null);

  // Measure the SVG's rendered width so axis/label/tooltip sizes can be
  // expressed in target screen pixels instead of viewBox units. Without
  // this, the chart's text shrinks dramatically in the 2-col layout (when
  // QuantileBand is rendered alongside, the container drops from ~1000px
  // to ~498px wide, scaling 720-unit text by 0.69×) and explodes in the
  // 1-col layout. useLayoutEffect runs before the browser paints, so the
  // corrected chartScale is in place for the first user-visible frame.
  // A ResizeObserver keeps it in sync on window resize / layout-column
  // flips (e.g. when QuantileBand mounts/unmounts). The W = 720 viewBox
  // width is constant for the chart's geometry; it has to be re-declared
  // here because the const below sits after the early-return.
  const svgRef = useRef<SVGSVGElement>(null);
  const [chartScale, setChartScale] = useState(1);
  useLayoutEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const update = () => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0) setChartScale(rect.width / 720);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  if (rows.length === 0) return null;
  const maxDte = Math.max(...rows.map((r) => r.dte), 1);
  const maxPrice = Math.max(...rows.map((r) => spot * (1 + r.emPct)));
  const minPrice = Math.min(...rows.map((r) => spot * (1 - r.emPct)));
  const pad = (maxPrice - minPrice) * 0.08;
  const yMax = maxPrice + pad;
  const yMin = minPrice - pad;

  const W = 720;
  const H = 320;
  const M = { top: 16, right: 96, bottom: 48, left: 72 };
  const innerW = W - M.left - M.right;
  const innerH = H - M.top - M.bottom;
  // Screen-px → viewBox units. Always returns a finite number so a 0
  // initial scale (before measurement) doesn't propagate NaN into the DOM.
  const px = (screenPx: number) => screenPx / (chartScale || 1);
  const x = (dte: number) => M.left + (dte / maxDte) * innerW;
  const y = (p: number) =>
    M.top + innerH - ((p - yMin) / (yMax - yMin)) * innerH;

  const upPath =
    `M ${x(0)} ${y(spot)} ` +
    rows.map((r) => `L ${x(r.dte)} ${y(spot * (1 + r.emPct))}`).join(' ');
  const dnPath =
    `M ${x(0)} ${y(spot)} ` +
    rows.map((r) => `L ${x(r.dte)} ${y(spot * (1 - r.emPct))}`).join(' ');
  const areaPath =
    upPath +
    ' ' +
    rows
      .slice()
      .reverse()
      .map((r) => `L ${x(r.dte)} ${y(spot * (1 - r.emPct))}`)
      .join(' ') +
    ' Z';

  const yTicks = Array.from(
    { length: 5 },
    (_, i) => yMin + ((yMax - yMin) * i) / 4,
  );

  return (
    <div className="qv-card">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: 16,
          marginBottom: 8,
        }}
      >
        <div>
          <span className="qv-pill">Term structure</span>
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
              Implied range across expiries
            </h3>
            <MetricHelp metric="termStructure" align="left" />
          </div>
          <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 4 }}>
            Spot × (1 ± EM) at each expiry. Hover an expiry for details.
          </div>
        </div>
        <div className="mono tnum" style={{ fontSize: 12.5, color: 'var(--ink-4)' }}>
          {rows.length} {rows.length === 1 ? 'expiry' : 'expiries'}
        </div>
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', height: 'auto', display: 'block', marginTop: 10 }}
      >
        <defs>
          <linearGradient id="fan-up" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="var(--up)" stopOpacity="1" />
            <stop offset="100%" stopColor="var(--up)" stopOpacity="0.6" />
          </linearGradient>
          <linearGradient id="fan-dn" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="var(--down)" stopOpacity="1" />
            <stop offset="100%" stopColor="var(--down)" stopOpacity="0.6" />
          </linearGradient>
          <linearGradient id="fan-area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--brand-blue-1)" stopOpacity="0.16" />
            <stop offset="100%" stopColor="var(--brand-blue-1)" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {yTicks.map((v, i) => (
          <g key={i}>
            <line
              x1={M.left}
              x2={W - M.right}
              y1={y(v)}
              y2={y(v)}
              stroke="var(--line)"
              strokeDasharray={i === 0 || i === yTicks.length - 1 ? 'none' : '2 4'}
              strokeWidth={i === 0 || i === yTicks.length - 1 ? 1 : 0.5}
            />
            <text
              x={M.left - 12}
              y={y(v)}
              textAnchor="end"
              dominantBaseline="central"
              fontSize={px(12.5)}
              fontFamily="ui-monospace, monospace"
              fill="var(--ink-3)"
            >
              ${v.toFixed(0)}
            </text>
          </g>
        ))}

        <path d={areaPath} fill="url(#fan-area)" />

        <line
          x1={M.left}
          x2={W - M.right}
          y1={y(spot)}
          y2={y(spot)}
          stroke="var(--ink-3)"
          strokeDasharray="3 4"
          strokeWidth={1}
        />

        <path d={upPath} fill="none" stroke="url(#fan-up)" strokeWidth={2.2} strokeLinejoin="round" />
        <path d={dnPath} fill="none" stroke="url(#fan-dn)" strokeWidth={2.2} strokeLinejoin="round" />

        <circle cx={x(0)} cy={y(spot)} r={5} fill="var(--ink)" stroke="var(--bg)" strokeWidth={2} />

        {rows.map((r, i) => {
          const cx = x(r.dte);
          const uy = y(spot * (1 + r.emPct));
          const dy = y(spot * (1 - r.emPct));
          const active = hovered === i;
          return (
            <g
              key={r.expiration}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
            >
              <rect
                x={cx - 18}
                y={M.top}
                width={36}
                height={innerH}
                fill="transparent"
                style={{ cursor: 'pointer' }}
              />
              {active && (
                <line
                  x1={cx}
                  x2={cx}
                  y1={M.top}
                  y2={M.top + innerH}
                  stroke="var(--ink-2)"
                  strokeWidth={1}
                  strokeDasharray="3 3"
                  pointerEvents="none"
                />
              )}
              <circle
                cx={cx}
                cy={uy}
                r={active ? 5 : 3.2}
                fill="var(--up)"
                stroke={active ? 'var(--bg)' : 'none'}
                strokeWidth={1.5}
                pointerEvents="none"
              />
              <circle
                cx={cx}
                cy={dy}
                r={active ? 5 : 3.2}
                fill="var(--down)"
                stroke={active ? 'var(--bg)' : 'none'}
                strokeWidth={1.5}
                pointerEvents="none"
              />
              <text
                x={cx}
                y={H - M.bottom + px(18)}
                textAnchor="middle"
                fontSize={px(12.5)}
                fontFamily="ui-monospace, monospace"
                fill={active ? 'var(--ink)' : 'var(--ink-2)'}
                fontWeight={active ? 600 : 500}
                pointerEvents="none"
              >
                {axisDate(r.expiration)}
              </text>
              <text
                x={cx}
                y={H - M.bottom + px(32)}
                textAnchor="middle"
                fontSize={px(11)}
                fontFamily="ui-monospace, monospace"
                fill="var(--ink-4)"
                pointerEvents="none"
              >
                {r.dte}d
              </text>
            </g>
          );
        })}

        {(() => {
          const last = rows[rows.length - 1];
          return (
            <g>
              <text
                x={x(last.dte) + 10}
                y={y(spot * (1 + last.emPct))}
                dominantBaseline="central"
                fontSize={px(12)}
                fontFamily="ui-monospace, monospace"
                fontWeight="700"
                fill="var(--up)"
              >
                ${(spot * (1 + last.emPct)).toFixed(0)}
              </text>
              <text
                x={x(last.dte) + 10}
                y={y(spot * (1 - last.emPct))}
                dominantBaseline="central"
                fontSize={px(12)}
                fontFamily="ui-monospace, monospace"
                fontWeight="700"
                fill="var(--down)"
              >
                ${(spot * (1 - last.emPct)).toFixed(0)}
              </text>
            </g>
          );
        })()}

        {hovered != null &&
          (() => {
            const r = rows[hovered];
            // Tooltip box dimensions in target screen pixels, converted to
            // viewBox units via px() so the box reads the same size whether
            // the chart is at 498px (2-col) or 1004px (1-col) wide.
            const tooltipW = px(200);
            const padX = px(12);
            const padTop = px(18);
            const lineH = px(15);
            const lines: Array<[string, string]> = [
              ['DTE', `${r.dte}d`],
              ['ATM IV', `${(r.iv * 100).toFixed(2)}%`],
              ['EM', `±${(r.emPct * 100).toFixed(2)}%`],
              ['Straddle', `$${r.straddle.toFixed(2)}`],
            ];
            const tooltipH = padTop + px(8) + lines.length * lineH;
            const anchorX = x(r.dte);
            const midY = (y(spot * (1 + r.emPct)) + y(spot * (1 - r.emPct))) / 2;
            const tx = Math.max(
              M.left + 4,
              Math.min(
                anchorX > W - M.right - tooltipW - 24
                  ? anchorX - tooltipW - 14
                  : anchorX + 14,
                W - M.right - tooltipW,
              ),
            );
            const ty = Math.max(
              M.top + 8,
              Math.min(midY - tooltipH / 2, H - M.bottom - tooltipH - 4),
            );
            return (
              <g pointerEvents="none">
                <rect
                  x={tx}
                  y={ty}
                  width={tooltipW}
                  height={tooltipH}
                  rx={px(8)}
                  fill="var(--bg-3)"
                  stroke="var(--line-2)"
                  strokeWidth={1}
                />
                <text
                  x={tx + padX}
                  y={ty + padTop}
                  fontSize={px(12)}
                  fontFamily="Mulish, sans-serif"
                  fontWeight="700"
                  fill="var(--ink)"
                >
                  {r.isEarnings ? 'Earnings · ' : ''}
                  {shortDate(r.expiration)}
                </text>
                {lines.map(([k, v], i) => (
                  <g key={k}>
                    <text
                      x={tx + padX}
                      y={ty + padTop + px(18) + i * lineH}
                      fontSize={px(10.5)}
                      fill="var(--ink-3)"
                      fontFamily="sans-serif"
                    >
                      {k}
                    </text>
                    <text
                      x={tx + tooltipW - padX}
                      y={ty + padTop + px(18) + i * lineH}
                      textAnchor="end"
                      fontSize={px(11)}
                      fill="var(--ink)"
                      fontFamily="ui-monospace, monospace"
                      fontWeight="600"
                    >
                      {v}
                    </text>
                  </g>
                ))}
              </g>
            );
          })()}
      </svg>
    </div>
  );
}

// Placeholder kept until the page body's old TermStructureFan callsite
// is removed. Returns null so existing markup quietly drops out.

// ---------- Historical implied vs actual ----------
