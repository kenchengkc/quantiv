import {
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
        <p>{definition.definition}</p>
        <div className="qv-metric-help-section">
          <span>How to read it</span>
          {definition.interpretation}
        </div>
        {definition.formula && (
          <div className="qv-metric-help-section">
            <span>Formula</span>
            <code>{definition.formula}</code>
          </div>
        )}
        {definition.caution && (
          <div className="qv-metric-help-caution">{definition.caution}</div>
        )}
      </div>
    </details>
  );
}

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function quickRead(
  optionsMovePct: number | null,
  mlMovePct: number | null,
  ivRank: number | null,
): string {
  const parts: string[] = [];
  if (optionsMovePct != null)
    parts.push(`Options price about ±${pct(optionsMovePct)} of movement`);
  if (mlMovePct != null) {
    if (optionsMovePct != null) {
      const gap = mlMovePct - optionsMovePct;
      const relation =
        Math.abs(gap) < 0.002
          ? "in line with"
          : gap > 0
            ? "wider than"
            : "tighter than";
      parts.push(
        `the ML point estimate is ${pct(mlMovePct)}, ${relation} that benchmark`,
      );
    } else {
      parts.push(`The ML median absolute-move estimate is ${pct(mlMovePct)}`);
    }
  }
  if (ivRank != null) {
    parts.push(
      `current IV is ${Math.round(ivRank * 100)}% of the way through its 52-week range`,
    );
  }
  return parts.length > 0
    ? `${parts.join("; ")}.`
    : "Read market-implied, model-estimated, and historical numbers as separate evidence layers.";
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
  const layers = [
    {
      icon: LineChart,
      label: "Market-implied",
      title:
        optionsMovePct != null
          ? `±${pct(optionsMovePct)} priced move`
          : "Options pricing",
      text: "IV and straddles reflect prices traders are paying for magnitude, not direction.",
    },
    {
      icon: BrainCircuit,
      label: "Model-estimated",
      title:
        mlMovePct != null ? `${pct(mlMovePct)} point estimate` : "ML distribution",
      text: "P10–P90 are LightGBM estimates of absolute move size from comparable events.",
    },
    {
      icon: History,
      label: "Observed history",
      title:
        historyCount > 0
          ? `${historyCount} prior reports`
          : "Realized outcomes",
      text: "Signed close-to-close reactions show what happened, with EPS as supporting context.",
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
          <div className="qv-reading-guide-eyebrow">
            How to read this dashboard
          </div>
          <h2 id="qv-reading-guide-title">Three evidence layers, one event</h2>
          <p>{quickRead(optionsMovePct, mlMovePct, ivRank)}</p>
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
                <p>{layer.text}</p>
              </div>
            </div>
          );
        })}
      </div>
      <div className="qv-reading-guide-note">
        Unless marked signed, move figures describe magnitude. None of these
        layers predicts direction or guarantees a range.
      </div>
    </section>
  );
}
