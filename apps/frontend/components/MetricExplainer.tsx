import {
  ArrowRight,
  BrainCircuit,
  CircleHelp,
  History,
  LineChart,
  Scale,
} from "lucide-react";
import { useEffect, useRef, useState, type SyntheticEvent } from "react";

import { METRIC_GLOSSARY, type MetricKey } from "@/lib/metricGlossary";

const METRIC_HELP_GROUP = "qv-ticker-metric-help";
const CALCULATION_ROLES = ["Input", "Calculation", "Output"] as const;

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
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    if (!isOpen) return;

    const closeOnOutsidePointer = (event: PointerEvent) => {
      const details = detailsRef.current;
      if (
        details &&
        event.target instanceof Node &&
        !details.contains(event.target)
      ) {
        details.open = false;
      }
    };

    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
    };
  }, [isOpen]);

  const handleToggle = (event: SyntheticEvent<HTMLDetailsElement>) => {
    closeOtherMetricHelp(event);
    setIsOpen(event.currentTarget.open);
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
      <div className="qv-metric-help-popover">
        <div className="qv-metric-help-eyebrow">Metric guide</div>
        <div className="qv-metric-help-title">{definition.label}</div>
        <div className="qv-metric-help-definition">{definition.definition}</div>
        <div
          className="qv-metric-help-flow"
          role="img"
          aria-label={`${definition.definition} Input: ${definition.calculation[0]}. Calculation: ${definition.calculation[1]}. Output: ${definition.calculation[2]}.`}
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
            </div>
          ))}
        </div>
        <div className="qv-metric-help-formula">
          <span>Formula</span>
          <code>{definition.formula}</code>
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
      </div>
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
    {
      icon: LineChart,
      label: "Options-implied",
      title: optionsMovePct != null ? `±${pct(optionsMovePct)}` : "No snapshot",
      caption: "ATM straddle",
    },
    {
      icon: BrainCircuit,
      label: "Model forecast",
      title: mlMovePct != null ? `±${pct(mlMovePct)}` : "No forecast",
      caption: "Expected absolute move",
    },
    {
      icon: History,
      label: "Historical median",
      title:
        historicalMovePct != null ? `±${pct(historicalMovePct)}` : "No sample",
      caption:
        historyCount > 0 ? `Last ${historyCount} earnings` : "Realized moves",
    },
  ];

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
      <div className="qv-reading-guide-grid">
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
