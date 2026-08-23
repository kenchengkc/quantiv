import type { ReactNode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { ExpectedMoveComparison, MetricHelp } from "./MetricExplainer";

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

  it("closes when the user clicks outside the metric guide", async () => {
    const container = renderMetricHelp(<MetricHelp metric="atmIv" />);
    const details = container.querySelector("details") as HTMLDetailsElement;
    const popover = container.querySelector(
      ".qv-metric-help-popover",
    ) as HTMLElement;

    flushSync(() => {
      details.open = true;
      details.dispatchEvent(new Event("toggle", { bubbles: true }));
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    popover.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect(details.open).toBe(true);

    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect(details.open).toBe(false);
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

describe("ExpectedMoveComparison", () => {
  it("compares options, model, and realized moves in plain language", () => {
    const container = renderMetricHelp(
      <ExpectedMoveComparison
        optionsMovePct={0.072}
        mlMovePct={0.037}
        historicalMovePct={0.051}
        historyCount={8}
        ivRank={0.36}
      />,
    );

    expect(container.textContent).toContain("Expected move comparison");
    expect(container.textContent).toContain("Options-implied±7.2%ATM straddle");
    expect(container.textContent).toContain(
      "Model forecast±3.7%Expected absolute move",
    );
    expect(container.textContent).toContain(
      "Historical median±5.1%Last 8 earnings",
    );
    expect(container.textContent).toContain(
      "Model 3.5 percentage points below options-implied",
    );
    expect(container.textContent).toContain(
      "Absolute move only; not direction",
    );
    expect(container.textContent).not.toContain("Event lens");
  });
});
