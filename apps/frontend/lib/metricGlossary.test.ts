import { describe, expect, it } from "vitest";

import { METRIC_GLOSSARY } from "./metricGlossary";

describe("metric glossary", () => {
  it("distinguishes IV rank from a percentile", () => {
    expect(METRIC_GLOSSARY.ivRank.calculation).toEqual([
      "Current + 52w range",
      "Normalize low to high",
      "0–100 rank",
    ]);
    expect(METRIC_GLOSSARY.ivRank.caution).toContain("not the percentage");
  });

  it("labels the provider score as heuristic rather than ML", () => {
    expect(METRIC_GLOSSARY.providerSignalScore.calculation).toContain(
      "Cap + average components",
    );
    expect(METRIC_GLOSSARY.providerSignalScore.caution).toContain("LightGBM");
  });

  it("makes clear that forecast quantiles describe magnitude, not direction", () => {
    expect(METRIC_GLOSSARY.forecastDistribution.definition).toContain(
      "absolute earnings move",
    );
    expect(METRIC_GLOSSARY.forecastDistribution.caution).toContain(
      "not direction",
    );
  });

  it("gives every metric an explicit input, calculation, output, use, and warning", () => {
    for (const metric of Object.values(METRIC_GLOSSARY)) {
      expect(metric.calculation).toHaveLength(3);
      expect(metric.calculation.every((node) => node.length <= 28)).toBe(true);
      expect(metric.formula.length).toBeLessThanOrEqual(62);
      expect(metric.formulaTex.length).toBeGreaterThan(4);
      expect(metric.methodologyHref).toMatch(/^\/about#/);
      expect(metric.use.length).toBeLessThanOrEqual(66);
      expect(metric.caution.length).toBeLessThanOrEqual(62);
    }
  });

  it("explains that ATM IV is back-solved from an observed option price", () => {
    expect(METRIC_GLOSSARY.atmIv.calculation).toEqual([
      "ATM call + put mids",
      "Solve each IV; average",
      "Annualized ATM IV %",
    ]);
    expect(METRIC_GLOSSARY.atmIv.formula).toContain(
      "model price = market price",
    );
  });
});
