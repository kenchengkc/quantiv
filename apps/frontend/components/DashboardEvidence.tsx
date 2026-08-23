import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Cpu,
  Database,
} from "lucide-react";
import type { ForecastDashboardEvidence } from "@/lib/forecastEvidence";

const UTC_DATE_FORMAT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});
const UTC_MONTH_FORMAT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  timeZone: "UTC",
});

function formatUtcDate(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return value;
  return UTC_DATE_FORMAT.format(parsed);
}

function formatObservationWindow(
  minimum: string | null | undefined,
  maximum: string | null | undefined,
): string {
  if (!minimum && !maximum) return "Observation time unavailable";
  if (!minimum || !maximum || minimum === maximum) {
    return formatUtcDate(maximum ?? minimum);
  }
  const minDate = new Date(`${minimum.slice(0, 10)}T00:00:00Z`);
  const maxDate = new Date(`${maximum.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(minDate.getTime()) || Number.isNaN(maxDate.getTime())) {
    return `${minimum} → ${maximum}`;
  }
  const sameYear = minDate.getUTCFullYear() === maxDate.getUTCFullYear();
  const sameMonth = sameYear && minDate.getUTCMonth() === maxDate.getUTCMonth();
  if (sameMonth) {
    const month = UTC_MONTH_FORMAT.format(minDate);
    return `${month} ${minDate.getUTCDate()}–${maxDate.getUTCDate()}, ${maxDate.getUTCFullYear()}`;
  }
  return `${formatUtcDate(minimum)} → ${formatUtcDate(maximum)}`;
}

function EvidenceNode({
  role,
  title,
  detail,
  icon,
}: {
  role: string;
  title: string;
  detail: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="qv-evidence-node">
      <div className="qv-evidence-node-role">
        {icon}
        <span>{role}</span>
      </div>
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

export function DashboardEvidence({
  evidence,
  forecastSnapshotDate,
  optionsAsOf,
}: {
  evidence: ForecastDashboardEvidence | null;
  forecastSnapshotDate?: string | null;
  optionsAsOf: string;
}) {
  const passed =
    evidence?.quality.status === "passed" &&
    evidence.quality.issue_count === 0 &&
    evidence.controls.exceptions === 0;
  const window = evidence?.observation_window;
  const modelBundle = evidence?.artifact_bundles.find(
    (artifact) => artifact.name === "model_bundle",
  );
  const horizons = evidence?.coverage.horizons
    .map((horizon) => `T${horizon}`)
    .join(" · ");

  return (
    <details
      className={`qv-evidence-strip ${passed ? "is-passed" : "needs-review"}`}
    >
      <summary>
        <span className="qv-evidence-summary-status">
          {passed ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          <strong>Decision evidence</strong>
          <span className="qv-evidence-status-label">
            {passed ? "Validated" : "Unavailable"}
          </span>
        </span>
        <span className="qv-evidence-summary-fact">
          Options <b>{formatUtcDate(optionsAsOf)}</b>
        </span>
        <span className="qv-evidence-summary-fact">
          Forecast <b>{formatUtcDate(forecastSnapshotDate)}</b>
        </span>
        <ChevronDown
          className="qv-evidence-chevron"
          size={15}
          aria-hidden="true"
        />
      </summary>

      {evidence ? (
        <div className="qv-evidence-body">
          <div
            className="qv-evidence-flow"
            aria-label="Dashboard evidence lineage"
          >
            <EvidenceNode
              role="Inputs"
              title="Options + earnings features"
              detail={`${evidence.coverage.rows} exact scoring vectors retained`}
              icon={<Database size={14} aria-hidden="true" />}
            />
            <span className="qv-evidence-arrow" aria-hidden="true">
              →
            </span>
            <EvidenceNode
              role="Observed"
              title={formatObservationWindow(
                window?.snapshot_min,
                window?.snapshot_max,
              )}
              detail={`${evidence.coverage.symbols} symbols · ${evidence.coverage.events} events`}
              icon={<Clock3 size={14} aria-hidden="true" />}
            />
            <span className="qv-evidence-arrow" aria-hidden="true">
              →
            </span>
            <EvidenceNode
              role="Computed"
              title={`LightGBM move · ${horizons ?? ""}`.trim()}
              detail={`daily_score.py · ${modelBundle?.member_count ?? 0} versioned model files`}
              icon={<Cpu size={14} aria-hidden="true" />}
            />
            <span className="qv-evidence-arrow" aria-hidden="true">
              →
            </span>
            <EvidenceNode
              role="Quality"
              title={
                passed ? "Publication gates passed" : "No publishable receipt"
              }
              detail={`${evidence.controls.evaluated} controls · ${evidence.controls.exceptions} exceptions`}
              icon={
                passed ? (
                  <CheckCircle2 size={14} aria-hidden="true" />
                ) : (
                  <AlertTriangle size={14} aria-hidden="true" />
                )
              }
            />
          </div>
        </div>
      ) : (
        <div className="qv-evidence-empty">
          No validated forecast receipt is published for this dashboard
          snapshot.
        </div>
      )}
    </details>
  );
}
