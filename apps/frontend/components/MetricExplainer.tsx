"use client";

import {
  ArrowRight,
  BrainCircuit,
  CircleHelp,
  History,
  LineChart,
  Scale,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type SyntheticEvent,
} from "react";
import Link from "next/link";
import dynamic from "next/dynamic";

import { METRIC_GLOSSARY, type MetricKey } from "@/lib/metricGlossary";

const METRIC_HELP_GROUP = "qv-ticker-metric-help";
const CALCULATION_ROLES = ["Inputs", "Method", "Result"] as const;
const MathFormula = dynamic(
  () => import("@/components/MathFormula").then((module) => module.MathFormula),
  {
    ssr: false,
    loading: () => (
      <span className="qv-metric-help-math-loading">Loading equation…</span>
    ),
  },
);

function closeOtherMetricHelp(event: SyntheticEvent<HTMLDetailsElement>) {
  const current = event.currentTarget;
  if (!current.open) return;

  document
    .querySelectorAll<HTMLDetailsElement>(
      `details[name="${METRIC_HELP_GROUP}"][open]`,
    )
    .forEach((details) => {
      if (details !== current) details.open = false;
    });
}

export function MetricHelp({
  metric,
  align = "right",
}: {
  metric: MetricKey;
  align?: "left" | "right";
}) {
  const definition = METRIC_GLOSSARY[metric];
  const [isOpen, setIsOpen] = useState(false);
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const outsidePointerListenerRef = useRef<
    ((event: PointerEvent) => void) | null
  >(null);

  const removeOutsidePointerListener = () => {
    const listener = outsidePointerListenerRef.current;
    if (!listener) return;
    document.removeEventListener("pointerdown", listener);
    outsidePointerListenerRef.current = null;
  };

  useEffect(
    () => () => {
      const listener = outsidePointerListenerRef.current;
      if (listener) document.removeEventListener("pointerdown", listener);
    },
    [],
  );

  const handleToggle = (event: SyntheticEvent<HTMLDetailsElement>) => {
    closeOtherMetricHelp(event);
    removeOutsidePointerListener();
    setIsOpen(event.currentTarget.open);
    if (!event.currentTarget.open) return;

    const details = event.currentTarget;
    const closeOnOutsidePointer = (pointerEvent: PointerEvent) => {
      if (
        pointerEvent.target instanceof Node &&
        !details.contains(pointerEvent.target)
      ) {
        details.open = false;
      }
    };
    outsidePointerListenerRef.current = closeOnOutsidePointer;
    document.addEventListener("pointerdown", closeOnOutsidePointer);
  };

  return (
    <details
      ref={detailsRef}
      className={`qv-metric-help qv-metric-help-${align}`}
      name={METRIC_HELP_GROUP}
      onToggle={handleToggle}
    >
      <summary
        aria-label={`Explain ${definition.label}`}
        title={`Explain ${definition.label}`}
      >
        <CircleHelp aria-hidden="true" size={14} strokeWidth={1.8} />
      </summary>
      {isOpen ? (
        <div className="qv-metric-help-popover">
          <div className="qv-metric-help-eyebrow">Metric guide</div>
          <div className="qv-metric-help-title">{definition.label}</div>
          <div className="qv-metric-help-definition">
            {definition.definition}
          </div>
          <div
            className="qv-metric-help-flow"
            role="img"
            aria-label={`${definition.definition} Inputs: ${definition.calculation[0]}. Method: ${definition.calculation[1]}. Result: ${definition.calculation[2]}.`}
          >
            {definition.calculation.map((node, index) => (
              <div className="qv-metric-help-flow-step" key={node}>
                {index > 0 ? (
                  <ArrowRight
                    className="qv-metric-help-flow-arrow"
                    aria-hidden="true"
                    size={13}
                    strokeWidth={1.8}
                  />
                ) : null}
                <span className="qv-metric-help-flow-role">
                  {CALCULATION_ROLES[index]}
                </span>
                <strong>{node}</strong>
                {definition.calculationDetails?.[index] ? (
                  <span className="qv-metric-help-flow-detail">
                    {definition.calculationDetails[index]}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
          <div className="qv-metric-help-formula">
            <span>Formula</span>
            <MathFormula
              className="qv-metric-help-math"
              displayMode
              label={definition.formula}
              math={definition.formulaTex}
            />
          </div>
          <div className="qv-metric-help-notes">
            <div>
              <span>Use</span>
              <strong>{definition.use}</strong>
            </div>
            <div>
              <span>Watch</span>
              <strong>{definition.caution}</strong>
            </div>
          </div>
          <Link
            className="qv-metric-help-methodology"
            href={definition.methodologyHref}
          >
            Full methodology <ArrowRight aria-hidden="true" size={12} />
          </Link>
        </div>
      ) : null}
    </details>
  );
}

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function modelComparison(
  optionsMovePct: number | null,
  mlMovePct: number | null,
): string | null {
  if (optionsMovePct == null || mlMovePct == null) return null;
  const gapPoints = Math.abs(mlMovePct - optionsMovePct) * 100;
  if (gapPoints < 0.2) return "Model and options-implied moves agree";
  return `Model ${gapPoints.toFixed(1)} percentage points ${mlMovePct > optionsMovePct ? "above" : "below"} options-implied`;
}

export function ExpectedMoveComparison({
  optionsMovePct,
  mlMovePct,
  ivRank,
  historicalMovePct,
  historyCount,
}: {
  optionsMovePct: number | null;
  mlMovePct: number | null;
  ivRank: number | null;
  historicalMovePct: number | null;
  historyCount: number;
}) {
  const comparison = modelComparison(optionsMovePct, mlMovePct);
  const layers = [
    ...(optionsMovePct != null
      ? [
          {
            icon: LineChart,
            label: "Options-implied",
            title: `±${pct(optionsMovePct)}`,
            caption: "ATM straddle",
          },
        ]
      : []),
    ...(mlMovePct != null
      ? [
          {
            icon: BrainCircuit,
            label: "Model forecast",
            title: `±${pct(mlMovePct)}`,
            caption: "Expected absolute move",
          },
        ]
      : []),
    ...(historicalMovePct != null
      ? [
          {
            icon: History,
            label: "Historical median",
            title: `±${pct(historicalMovePct)}`,
            caption:
              historyCount > 0
                ? `Last ${historyCount} earnings`
                : "Realized moves",
          },
        ]
      : []),
  ];

  if (layers.length < 2) return null;

  return (
    <section
      className="qv-reading-guide"
      aria-labelledby="qv-expected-move-comparison-title"
    >
      <div className="qv-reading-guide-heading">
        <div className="qv-reading-guide-icon" aria-hidden="true">
          <Scale size={18} strokeWidth={1.8} />
        </div>
        <div>
          <div className="qv-reading-guide-eyebrow">Earnings move</div>
          <h2 id="qv-expected-move-comparison-title">
            Expected move comparison
          </h2>
        </div>
      </div>
      <div
        className="qv-reading-guide-grid"
        style={
          {
            "--qv-reading-guide-columns": layers.length,
          } as CSSProperties
        }
      >
        {layers.map((layer) => {
          const Icon = layer.icon;
          return (
            <div key={layer.label} className="qv-reading-guide-layer">
              <Icon aria-hidden="true" size={16} strokeWidth={1.8} />
              <div>
                <div className="qv-reading-guide-layer-label">
                  {layer.label}
                </div>
                <div className="qv-reading-guide-layer-title">
                  {layer.title}
                </div>
                <div className="qv-reading-guide-layer-caption">
                  {layer.caption}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="qv-reading-guide-footer">
        {comparison && <strong>{comparison}</strong>}
        {ivRank != null && <span>IV rank {Math.round(ivRank * 100)}%</span>}
        <span>Absolute move only; not direction</span>
      </div>
    </section>
  );
}
