import type { ReactNode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { MetricHelp } from "./MetricExplainer";

const roots: Root[] = [];

function renderMetricHelp(ui: ReactNode): HTMLElement {
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

describe("MetricHelp", () => {
  it("keeps only one metric guide open", () => {
    const container = renderMetricHelp(
      <>
        <MetricHelp metric="atmIv" />
        <MetricHelp metric="ivExpectedMove" />
      </>,
    );

    const details = container.querySelectorAll("details");
    const atmDetails = details.item(0);
    const moveDetails = details.item(1);

    atmDetails.open = true;
    atmDetails.dispatchEvent(new Event("toggle", { bubbles: true }));
    moveDetails.open = true;
    moveDetails.dispatchEvent(new Event("toggle", { bubbles: true }));

    expect(atmDetails.open).toBe(false);
    expect(moveDetails.open).toBe(true);
  });

  it("labels the ATM IV calculation stages and formula", () => {
    const container = renderMetricHelp(<MetricHelp metric="atmIv" />);
    const roles = Array.from(
      container.querySelectorAll(".qv-metric-help-flow-role"),
      (node) => node.textContent,
    );

    expect(roles).toEqual(["Input", "Calculation", "Output"]);
    expect(container.textContent).toContain("ATM call + put mids");
    expect(container.textContent).toContain("Solve each IV; average");
    expect(container.textContent).toContain(
      "Find each σ where model price = market price; then average",
    );
  });
});
