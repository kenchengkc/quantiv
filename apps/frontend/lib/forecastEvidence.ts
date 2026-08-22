export interface ForecastEvidenceArtifactBundle {
  name: string;
  producer: string;
  member_count: number;
  bytes: number;
  sha256: string;
}

export interface ForecastDashboardEvidence {
  schema: "quantiv.dashboard-evidence.v1";
  receipt_id: string;
  receipt_file?: string | null;
  validated_at?: string | null;
  quality: {
    status: "passed" | "failed";
    issue_count: number;
    issue_codes: Array<{ stage: string; code: string }>;
  };
  coverage: {
    rows: number;
    symbols: number;
    events: number;
    horizons: number[];
  };
  observation_window: {
    snapshot_min?: string | null;
    snapshot_max?: string | null;
    earnings_min?: string | null;
    earnings_max?: string | null;
    scored_at_min?: string | null;
    scored_at_max?: string | null;
  };
  controls: {
    evaluated: number;
    exceptions: number;
    results: Record<string, number | null>;
  };
  artifact_bundles: ForecastEvidenceArtifactBundle[];
}

export function shortEvidenceHash(
  value: string | null | undefined,
  length = 12,
): string {
  return (
    (value ?? "").replace(/^sha256:/, "").slice(0, length) || "unavailable"
  );
}
