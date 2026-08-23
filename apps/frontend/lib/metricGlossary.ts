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
  calculation: readonly [string, string, string];
  formula: string;
  use: string;
  caution: string;
}

export const METRIC_GLOSSARY: Record<MetricKey, MetricDefinition> = {
  atmIv: {
    label: "At-the-money implied volatility",
    definition:
      "The average call/put IV at the nearest strike, back-solved from their option quotes.",
    calculation: [
      "ATM call + put mids",
      "Solve each IV; average",
      "Annualized ATM IV %",
    ],
    formula: "Find each σ where model price = market price; then average",
    use: "Compare priced volatility across expiries and dates",
    caution: "Not direction; quote, rate, and dividend inputs matter",
  },
  ivExpectedMove: {
    label: "IV-based expected move",
    definition:
      "A volatility-scaled move range for the selected time to expiry.",
    calculation: ["ATM IV + DTE", "Scale by √time", "± move %"],
    formula: "ATM IV × √(days to expiry ÷ 365)",
    use: "Benchmark the range priced through this expiry",
    caution: "A one-sigma estimate, not a hard boundary",
  },
  atmStraddle: {
    label: "At-the-money straddle",
    definition: "The nearest-strike call and put midpoint premiums combined.",
    calculation: ["Call + put mids", "Add; divide by spot", "$ move + move %"],
    formula: "ATM call midpoint + ATM put midpoint",
    use: "See the market cost of two-sided movement",
    caution: "Before spread, fees, and the post-event IV drop",
  },
  ivRank: {
    label: "IV rank",
    definition: "Current IV positioned between its 52-week low and high.",
    calculation: ["Current + 52w range", "Normalize low to high", "0–100 rank"],
    formula: "(current IV − 52w low) ÷ (52w high − 52w low)",
    use: "Judge whether this ticker's IV is high versus its own year",
    caution: "Rank is not the percentage of days below current IV",
  },
  atmSkew: {
    label: "ATM skew",
    definition:
      "The implied-volatility difference between comparable puts and calls.",
    calculation: [
      "Matched put + call IV",
      "Put IV − call IV",
      "Skew in vol points",
    ],
    formula: "Mean put IV − mean call IV near ATM",
    use: "Compare downside-option richness with upside",
    caution: "Positioning context, not a price forecast",
  },
  daysToCover: {
    label: "Short interest days to cover",
    definition: "Reported short shares scaled by typical daily trading volume.",
    calculation: ["Short shares + ADV", "Short shares ÷ ADV", "Trading days"],
    formula: "Reported shares short ÷ average daily volume",
    use: "Flag short crowding and potential unwind friction",
    caution: "Short-interest reports lag current positions",
  },
  putCallVolume: {
    label: "Put/call volume ratio",
    definition: "Observed put contract volume divided by call contract volume.",
    calculation: ["Put + call volume", "Put volume ÷ call", "Session ratio"],
    formula: "Observed put volume ÷ observed call volume",
    use: "Spot unusually put- or call-heavy trading",
    caution: "Does not reveal buy/sell or open/close intent",
  },
  putCallOpenInterest: {
    label: "Put/call open-interest ratio",
    definition:
      "Outstanding put contracts divided by outstanding call contracts.",
    calculation: ["Put + call OI", "Put OI ÷ call OI", "Position ratio"],
    formula: "Outstanding put contracts ÷ outstanding call contracts",
    use: "Compare outstanding put and call contract balance",
    caution: "Multi-leg and hedged positions obscure intent",
  },
  corporateActions: {
    label: "Corporate actions",
    definition: "Dividend and split events found in the provider window.",
    calculation: [
      "Dividend + split records",
      "Count recent events",
      "Action context",
    ],
    formula: "Provider events inside the published data window",
    use: "Prompt contract and price-continuity checks",
    caution: "Counts do not prove adjustment; not a trading signal",
  },
  providerSignalScore: {
    label: "Positioning score",
    definition:
      "A rule-based average of short crowding and put/call imbalances.",
    calculation: [
      "Short DTC + P/C ratios",
      "Cap + average components",
      "0–1 context score",
    ],
    formula: "Mean[min(DTC/10, 1), min(|ln(P/C)|, 1)]",
    use: "Prioritize unusual context for review",
    caution: "Descriptive heuristic, not the LightGBM forecast",
  },
  probabilityDensity: {
    label: "Probability density",
    definition:
      "A smooth log-normal illustration built from spot, IV, and time.",
    calculation: [
      "Spot + ATM IV + DTE",
      "Log-normal mapping",
      "Relative density curve",
    ],
    formula: "Return volatility = ATM IV × √(DTE ÷ 365)",
    use: "Compare where a smooth volatility model places more mass",
    caution: "Earnings jumps can have fatter, asymmetric tails",
  },
  forecastDistribution: {
    label: "ML forecast distribution",
    definition:
      "LightGBM quantiles for the conditional absolute earnings move.",
    calculation: [
      "Point-in-time features",
      "LightGBM quantile heads",
      "P10 · P50 · P90",
    ],
    formula: "Pq = qth conditional absolute-move estimate",
    use: "Size a range of plausible earnings-move magnitudes",
    caution: "Magnitude only; not direction or guaranteed coverage",
  },
  termStructure: {
    label: "Expected-move term structure",
    definition: "Option-implied ranges compared across expiration dates.",
    calculation: ["Each expiry's EM", "Spot × (1 ± EM)", "Price-range fan"],
    formula: "spot × (1 ± expected-move percentage)",
    use: "Find kinks around the earnings expiration",
    caution: "Endpoints are ranges, not path probabilities",
  },
  history: {
    label: "Implied versus realized history",
    definition: "Past priced ranges compared with signed earnings reactions.",
    calculation: [
      "Priced range + closes",
      "Align each event",
      "Hit/miss + signed move",
    ],
    formula: "Realized = post-event close ÷ pre-event close − 1",
    use: "Check whether options historically over- or under-priced moves",
    caution: "Small samples and regime changes limit inference",
  },
  greeks: {
    label: "ATM option Greeks",
    definition:
      "Local option-price sensitivities to spot, volatility, and time.",
    calculation: [
      "Spot + strike + IV + time",
      "Differentiate option value",
      "Δ · Γ · ν · Θ",
    ],
    formula: "Greek = ∂ option value ÷ ∂ risk factor",
    use: "Approximate local hedge and sensitivity exposure",
    caution: "They move with inputs and are not realized P&L",
  },
};
