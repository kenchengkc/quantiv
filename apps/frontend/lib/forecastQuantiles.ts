export interface ForecastQuantiles {
  p10: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
}

export interface QuantileExceedanceEstimate {
  probability: number;
  qualifier: "estimate" | "at_least" | "at_most";
}

type QuantileInput =
  | Record<string, number | null | undefined>
  | null
  | undefined;

function finite(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function normalizeForecastQuantiles(
  quantiles: QuantileInput,
): ForecastQuantiles | null {
  if (!quantiles) return null;
  const p10 = quantiles["10"];
  const p50 = quantiles["50"];
  const p90 = quantiles["90"];
  if (!finite(p10) || !finite(p50) || !finite(p90)) return null;

  const values = [
    p10,
    finite(quantiles["25"]) ? quantiles["25"] : p10,
    p50,
    finite(quantiles["75"]) ? quantiles["75"] : p90,
    p90,
  ]
    .map((value) => Math.max(0, value))
    .sort((a, b) => a - b);

  return {
    p10: values[0],
    p25: values[1],
    p50: values[2],
    p75: values[3],
    p90: values[4],
  };
}

/**
 * Estimate P(|earnings move| > threshold) from the five served quantiles.
 * This is deterministic interpolation of the validated quantile heads, not a
 * sixth model. Values outside P10–P90 are reported as bounds so the UI does
 * not pretend to know the unmodeled tails.
 */
export function estimateQuantileExceedance(
  quantiles: ForecastQuantiles,
  threshold: number,
): QuantileExceedanceEstimate | null {
  if (!Number.isFinite(threshold) || threshold < 0) return null;
  const points = [
    { value: quantiles.p10, probability: 0.1 },
    { value: quantiles.p25, probability: 0.25 },
    { value: quantiles.p50, probability: 0.5 },
    { value: quantiles.p75, probability: 0.75 },
    { value: quantiles.p90, probability: 0.9 },
  ];
  if (points.some((point) => !Number.isFinite(point.value))) return null;

  if (threshold < points[0].value) {
    return { probability: 0.9, qualifier: "at_least" };
  }
  if (threshold > points[points.length - 1].value) {
    return { probability: 0.1, qualifier: "at_most" };
  }

  for (let index = 1; index < points.length; index += 1) {
    const lower = points[index - 1];
    const upper = points[index];
    if (threshold > upper.value) continue;

    if (upper.value === lower.value) {
      return {
        probability: 1 - (lower.probability + upper.probability) / 2,
        qualifier: "estimate",
      };
    }
    const weight = (threshold - lower.value) / (upper.value - lower.value);
    const cdf = lower.probability + weight * (upper.probability - lower.probability);
    return {
      probability: Math.max(0, Math.min(1, 1 - cdf)),
      qualifier: "estimate",
    };
  }

  return null;
}
