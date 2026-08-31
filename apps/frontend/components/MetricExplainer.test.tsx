import type { ReactNode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

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

  it("closes when the user clicks outside the metric guide", async () => {
    const container = renderMetricHelp(<MetricHelp metric="atmIv" />);
    const details = container.querySelector("details") as HTMLDetailsElement;

    flushSync(() => {
      details.open = true;
      details.dispatchEvent(new Event("toggle", { bubbles: true }));
    });
    await vi.waitFor(() => {
      expect(container.querySelector(".qv-metric-help-popover")).not.toBeNull();
    });
    const popover = container.querySelector(
      ".qv-metric-help-popover",
    ) as HTMLElement;

    popover.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect(details.open).toBe(true);

    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    expect(details.open).toBe(false);
  });

  it("labels the ATM IV calculation stages and formula", async () => {
    const container = renderMetricHelp(<MetricHelp metric="atmIv" />);
    const details = container.querySelector("details") as HTMLDetailsElement;
    flushSync(() => {
      details.open = true;
      details.dispatchEvent(new Event("toggle", { bubbles: true }));
    });
    await vi.waitFor(() => {
      expect(container.querySelector("[role='math']")).not.toBeNull();
    });
    const roles = Array.from(
      container.querySelectorAll(".qv-metric-help-flow-role"),
      (node) => node.textContent,
    );

    expect(roles).toEqual(["Inputs", "Method", "Result"]);
    expect(container.textContent).toContain("ATM call + put mids");
    expect(container.textContent).toContain("Solve each IV; average");
    expect(container.textContent).toContain("Same symbol, expiry, and strike");
    expect(
      container.querySelector("[role='math']")?.getAttribute("aria-label"),
    ).toContain("model price = market price");
    expect(container.querySelector("a")?.getAttribute("href")).toBe(
      "/about#methodology-atm-iv",
    );
  });
});
