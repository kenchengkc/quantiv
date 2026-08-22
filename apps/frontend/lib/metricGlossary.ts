export type MetricKey =
  | "atmIv"
  | "ivExpectedMove"
  | "atmStraddle"
  | "ivRank"
  | "atmSkew"
  | "daysToCover"
  | "putCallVolume"
  | "putCallOpenInterest"
  | "corporateActions"
  | "providerSignalScore"
  | "probabilityDensity"
  | "forecastDistribution"
  | "termStructure"
  | "history"
  | "greeks";

export interface MetricDefinition {
  label: string;
  definition: string;
  visual: readonly [string, string, string];
  interpretation: string;
  formula?: string;
  caution: string;
}

export const METRIC_GLOSSARY: Record<MetricKey, MetricDefinition> = {
  atmIv: {
    label: "At-the-money implied volatility",
    definition:
      "Option prices translated into annualized volatility at the nearest strike.",
    visual: ["Option price", "ATM IV", "Move size"],
    interpretation: "Higher = more movement priced",
    caution: "No direction",
  },
  ivExpectedMove: {
    label: "IV-based expected move",
    definition:
      "A volatility-scaled move range for the selected time to expiry.",
    visual: ["ATM IV + DTE", "√ time", "± range"],
    interpretation: "One-sigma range estimate",
    formula: "ATM IV × √(days to expiry ÷ 365)",
    caution: "Range, not promise",
  },
  atmStraddle: {
    label: "At-the-money straddle",
    definition: "The nearest-strike call and put midpoint premiums combined.",
    visual: ["ATM call", "+ ATM put", "Priced move"],
    interpretation: "Dollar cost of two-sided movement",
    formula: "ATM call midpoint + ATM put midpoint",
    caution: "Before spread + fees",
  },
  ivRank: {
    label: "IV rank",
    definition: "Current IV positioned between its 52-week low and high.",
    visual: ["52w low", "Current IV", "52w high"],
    interpretation: "Higher = nearer the yearly high",
    formula: "(current IV − 52w low) ÷ (52w high − 52w low)",
    caution: "Rank ≠ percentile",
  },
  atmSkew: {
    label: "ATM skew",
    definition:
      "The implied-volatility difference between comparable puts and calls.",
    visual: ["Put IV", "− Call IV", "Skew"],
    interpretation: "Positive = puts richer",
    caution: "Context, not forecast",
  },
  daysToCover: {
    label: "Short interest days to cover",
    definition: "Reported short shares scaled by typical daily trading volume.",
    visual: ["Shares short", "÷ Daily volume", "Days"],
    interpretation: "Higher = more crowded to unwind",
    caution: "Reported with lag",
  },
  putCallVolume: {
    label: "Put/call volume ratio",
    definition: "Observed put contract volume divided by call contract volume.",
    visual: ["Put volume", "÷ Call volume", "P/C ratio"],
    interpretation: "> 1 = more puts traded",
    caution: "Intent unknown",
  },
  putCallOpenInterest: {
    label: "Put/call open-interest ratio",
    definition:
      "Outstanding put contracts divided by outstanding call contracts.",
    visual: ["Put OI", "÷ Call OI", "P/C ratio"],
    interpretation: "> 1 = more put OI",
    caution: "Position intent unknown",
  },
  corporateActions: {
    label: "Corporate actions",
    definition: "Dividend and split events found in the provider window.",
    visual: ["Dividends", "+ Splits", "Adjustments"],
    interpretation: "Check contract + price continuity",
    caution: "Not a signal",
  },
  providerSignalScore: {
    label: "Provider signal score",
    definition:
      "A descriptive blend of crowding, flow, and corporate-action flags.",
    visual: ["Flow + short", "+ Actions", "Heuristic"],
    interpretation: "Higher = more flags present",
    caution: "Heuristic, not ML",
  },
  probabilityDensity: {
    label: "Probability density",
    definition:
      "A smooth log-normal illustration built from spot, IV, and time.",
    visual: ["Spot + IV", "+ DTE", "Density"],
    interpretation: "Curve height = relative likelihood",
    caution: "Earnings have fat tails",
  },
  forecastDistribution: {
    label: "ML forecast distribution",
    definition:
      "LightGBM quantiles for the conditional absolute earnings move.",
    visual: ["Event features", "LightGBM", "P10 … P90"],
    interpretation: "P50 = median magnitude",
    caution: "Magnitude, not direction",
  },
  termStructure: {
    label: "Expected-move term structure",
    definition: "Option-implied ranges compared across expiration dates.",
    visual: ["Expiry EM", "× Spot", "Price fan"],
    interpretation: "Find kinks around the event",
    formula: "spot × (1 ± expected-move percentage)",
    caution: "Not path probability",
  },
  history: {
    label: "Implied versus realized history",
    definition: "Past priced ranges compared with signed earnings reactions.",
    visual: ["Implied range", "vs Realized", "Hit / miss"],
    interpretation: "Outside band = underpriced move",
    caution: "Small sample",
  },
  greeks: {
    label: "ATM option Greeks",
    definition:
      "Local option-price sensitivities to spot, volatility, and time.",
    visual: ["Spot + IV + time", "Option price", "Δ Γ ν Θ"],
    interpretation: "Local risk, recomputed continuously",
    caution: "Sensitivity, not P&L",
  },
};
