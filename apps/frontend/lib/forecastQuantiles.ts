export interface ForecastQuantiles {
  p10: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
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
