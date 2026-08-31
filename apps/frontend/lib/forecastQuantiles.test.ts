import { describe, expect, it } from "vitest";

import {
  estimateQuantileExceedance,
  normalizeForecastQuantiles,
} from "./forecastQuantiles";

describe("normalizeForecastQuantiles", () => {
  it("orders crossed quantiles and clips absolute moves at zero", () => {
    expect(
      normalizeForecastQuantiles({
        "10": 0.05,
        "25": -0.01,
        "50": 0.03,
        "75": 0.09,
        "90": 0.07,
      }),
    ).toEqual({ p10: 0, p25: 0.03, p50: 0.05, p75: 0.07, p90: 0.09 });
  });

  it("fills optional inner quantiles from the required outer values", () => {
    expect(
      normalizeForecastQuantiles({ "10": 0.02, "50": 0.05, "90": 0.1 }),
    ).toEqual({
      p10: 0.02,
      p25: 0.02,
      p50: 0.05,
      p75: 0.1,
      p90: 0.1,
    });
  });

  it("rejects incomplete or non-finite core quantiles", () => {
    expect(
      normalizeForecastQuantiles({ "10": 0.02, "50": Number.NaN, "90": 0.1 }),
    ).toBeNull();
    expect(normalizeForecastQuantiles({ "10": 0.02, "90": 0.1 })).toBeNull();
  });
});

describe("estimateQuantileExceedance", () => {
  const quantiles = {
    p10: 0.02,
    p25: 0.04,
    p50: 0.06,
    p75: 0.09,
    p90: 0.14,
  };

  it("interpolates the probability above a straddle threshold", () => {
    expect(estimateQuantileExceedance(quantiles, 0.075)).toEqual({
      probability: 0.375,
      qualifier: "estimate",
    });
  });

  it("reports honest bounds outside the modeled quantile range", () => {
    expect(estimateQuantileExceedance(quantiles, 0.01)).toEqual({
      probability: 0.9,
      qualifier: "at_least",
    });
    expect(estimateQuantileExceedance(quantiles, 0.2)).toEqual({
      probability: 0.1,
      qualifier: "at_most",
    });
  });

  it("rejects invalid thresholds", () => {
    expect(estimateQuantileExceedance(quantiles, Number.NaN)).toBeNull();
    expect(estimateQuantileExceedance(quantiles, -0.01)).toBeNull();
  });
});
