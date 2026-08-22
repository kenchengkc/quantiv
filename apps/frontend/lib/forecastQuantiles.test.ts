import { describe, expect, it } from "vitest";

import { normalizeForecastQuantiles } from "./forecastQuantiles";

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
