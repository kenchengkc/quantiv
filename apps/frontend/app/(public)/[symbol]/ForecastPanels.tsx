'use client';

import { useLayoutEffect, useRef, useState } from 'react';
import { MetricHelp } from '@/components/MetricExplainer';
import type { Straddle } from './symbolPageTypes';
import { axisDate, parseLocalDate, shortDate } from './symbolPageUtils';

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
  // expressed in target screen pixels instead of viewBox units. This keeps
  // the text stable as the full-width chart moves between desktop and mobile.
  // A ResizeObserver updates it before paint and whenever the layout changes.
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
