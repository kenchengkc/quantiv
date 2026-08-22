import type { ReactNode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import type { ForecastDashboardEvidence } from "@/lib/forecastEvidence";
import { DashboardEvidence } from "./DashboardEvidence";

const roots: Root[] = [];

function render(ui: ReactNode): HTMLElement {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  roots.push(root);
  flushSync(() => root.render(ui));
  return container;
}

afterEach(() => {
  roots.splice(0).forEach((root) => {
    flushSync(() => root.unmount());
  });
  document.body.replaceChildren();
});

const EVIDENCE: ForecastDashboardEvidence = {
  schema: "quantiv.dashboard-evidence.v1",
  receipt_id: `sha256:${"5c7f46c41478".padEnd(64, "0")}`,
  validated_at: "2026-08-22T20:00:00+00:00",
  quality: { status: "passed", issue_count: 0, issue_codes: [] },
  coverage: { rows: 196, symbols: 81, events: 82, horizons: [3, 7, 14, 21] },
  observation_window: {
    snapshot_min: "2026-08-03",
    snapshot_max: "2026-08-21",
  },
  controls: {
    evaluated: 16,
    exceptions: 0,
    results: { duplicate_serving_keys: 0 },
  },
  artifact_bundles: [
    {
      name: "model_bundle",
      producer: "apps/ml/model_trainer_v3.py",
      member_count: 28,
      bytes: 100,
      sha256:
        "modelhash0000000000000000000000000000000000000000000000000000000",
    },
    {
      name: "forecast_snapshot",
      producer: "scripts/daily_score.py",
      member_count: 1,
      bytes: 100,
      sha256:
        "forecasthash0000000000000000000000000000000000000000000000000000",
    },
  ],
};

describe("DashboardEvidence", () => {
  it("shows one run-level status and a four-stage lineage", () => {
    const container = render(
      <DashboardEvidence
        evidence={EVIDENCE}
        forecastSnapshotDate="2026-08-19"
        optionsAsOf="2026-08-21"
      />,
    );

    expect(container.textContent).toContain("Decision evidence");
    expect(container.textContent).toContain("Validated");
    expect(container.textContent).toContain("Forecast Aug 19, 2026");
    expect(container.textContent).toContain("Receipt 5c7f46c41478");
    expect(container.textContent).toContain("16 controls · 0 exceptions");
    expect(
      Array.from(
        container.querySelectorAll(".qv-evidence-node-role span"),
        (node) => node.textContent,
      ),
    ).toEqual(["Inputs", "Observed", "Computed", "Quality"]);
  });

  it("fails visibly when no receipt is available", () => {
    const container = render(
      <DashboardEvidence
        evidence={null}
        forecastSnapshotDate={null}
        optionsAsOf="2026-08-21"
      />,
    );

    expect(container.textContent).toContain("Unavailable");
    expect(container.textContent).toContain("No validated forecast receipt");
    expect(container.textContent).not.toContain("Validated");
  });
});
