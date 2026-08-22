import { describe, expect, it } from "vitest";

import { METRIC_GLOSSARY } from "./metricGlossary";

describe("metric glossary", () => {
  it("distinguishes IV rank from a percentile", () => {
    expect(METRIC_GLOSSARY.ivRank.visual).toEqual([
      "52w low",
      "Current IV",
      "52w high",
    ]);
    expect(METRIC_GLOSSARY.ivRank.caution).toContain("≠ percentile");
  });

  it("labels the provider score as heuristic rather than ML", () => {
    expect(METRIC_GLOSSARY.providerSignalScore.visual.at(-1)).toBe("Heuristic");
    expect(METRIC_GLOSSARY.providerSignalScore.caution).toContain("not ML");
  });

  it("makes clear that forecast quantiles describe magnitude, not direction", () => {
    expect(METRIC_GLOSSARY.forecastDistribution.definition).toContain(
      "absolute earnings move",
    );
    expect(METRIC_GLOSSARY.forecastDistribution.caution).toContain(
      "not direction",
    );
  });

  it("keeps every quick guide visual and scan-friendly", () => {
    for (const metric of Object.values(METRIC_GLOSSARY)) {
      expect(metric.visual).toHaveLength(3);
      expect(metric.visual.every((node) => node.length <= 18)).toBe(true);
      expect(metric.interpretation.length).toBeLessThanOrEqual(38);
      expect(metric.caution.length).toBeLessThanOrEqual(24);
    }
  });
});
