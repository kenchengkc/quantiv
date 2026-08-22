import { describe, expect, it } from "vitest";

import { METRIC_GLOSSARY } from "./metricGlossary";

describe("metric glossary", () => {
  it("distinguishes IV rank from a percentile", () => {
    expect(METRIC_GLOSSARY.ivRank.definition).toContain("position");
    expect(METRIC_GLOSSARY.ivRank.interpretation).toContain("does not mean");
  });

  it("labels the provider score as heuristic rather than ML", () => {
    expect(METRIC_GLOSSARY.providerSignalScore.definition).toContain(
      "heuristic",
    );
    expect(METRIC_GLOSSARY.providerSignalScore.interpretation).toContain(
      "not the LightGBM",
    );
  });

  it("makes clear that forecast quantiles describe magnitude, not direction", () => {
    expect(METRIC_GLOSSARY.forecastDistribution.definition).toContain(
      "absolute earnings move",
    );
    expect(METRIC_GLOSSARY.forecastDistribution.caution).toContain(
      "not direction",
    );
  });
});
