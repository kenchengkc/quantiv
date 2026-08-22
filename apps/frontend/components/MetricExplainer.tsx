import {
  ArrowRight,
  BookOpen,
  BrainCircuit,
  CircleHelp,
  History,
  LineChart,
} from "lucide-react";

import { METRIC_GLOSSARY, type MetricKey } from "@/lib/metricGlossary";

export function MetricHelp({
  metric,
  align = "right",
}: {
  metric: MetricKey;
  align?: "left" | "right";
}) {
  const definition = METRIC_GLOSSARY[metric];

  return (
    <details className={`qv-metric-help qv-metric-help-${align}`}>
      <summary
        aria-label={`Explain ${definition.label}`}
        title={`Explain ${definition.label}`}
      >
        <CircleHelp aria-hidden="true" size={14} strokeWidth={1.8} />
      </summary>
      <div className="qv-metric-help-popover">
        <div className="qv-metric-help-eyebrow">Metric guide</div>
        <div className="qv-metric-help-title">{definition.label}</div>
        <div
          className="qv-metric-help-flow"
          role="img"
          aria-label={definition.definition}
        >
          {definition.visual.map((node, index) => (
            <div className="qv-metric-help-flow-step" key={node}>
              {index > 0 && (
                <ArrowRight
                  className="qv-metric-help-flow-arrow"
                  aria-hidden="true"
                  size={13}
                  strokeWidth={1.8}
                />
              )}
              <span>{node}</span>
            </div>
          ))}
        </div>
        <div className="qv-metric-help-read">
          <span>Read</span>
          <strong>{definition.interpretation}</strong>
        </div>
        <div className="qv-metric-help-tags">
          {definition.formula && <code>{definition.formula}</code>}
          <span>{definition.caution}</span>
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
  if (gapPoints < 0.2) return "ML ≈ market";
  return `ML ${gapPoints.toFixed(1)} pts ${mlMovePct > optionsMovePct ? "wider" : "tighter"}`;
}

export function DashboardReadingGuide({
  optionsMovePct,
  mlMovePct,
  ivRank,
  historyCount,
}: {
  optionsMovePct: number | null;
  mlMovePct: number | null;
  ivRank: number | null;
  historyCount: number;
}) {
  const comparison = modelComparison(optionsMovePct, mlMovePct);
  const layers = [
    {
      icon: LineChart,
      label: "Market",
      title: optionsMovePct != null ? `±${pct(optionsMovePct)}` : "No snapshot",
      caption: "Priced magnitude",
    },
    {
      icon: BrainCircuit,
      label: "Model",
      title: mlMovePct != null ? pct(mlMovePct) : "No forecast",
      caption: "Conditional magnitude",
    },
    {
      icon: History,
      label: "History",
      title: historyCount > 0 ? `${historyCount} reports` : "No sample",
      caption: "Observed reactions",
    },
  ];

  return (
    <section
      className="qv-reading-guide"
      aria-labelledby="qv-reading-guide-title"
    >
      <div className="qv-reading-guide-heading">
        <div className="qv-reading-guide-icon" aria-hidden="true">
          <BookOpen size={18} strokeWidth={1.8} />
        </div>
        <div>
          <div className="qv-reading-guide-eyebrow">Event lens</div>
          <h2 id="qv-reading-guide-title">Market · Model · History</h2>
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
        <span>Magnitude only</span>
        <span>No direction</span>
      </div>
    </section>
  );
}
