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
  interpretation: string;
  formula?: string;
  caution?: string;
}

export const METRIC_GLOSSARY: Record<MetricKey, MetricDefinition> = {
  atmIv: {
    label: "At-the-money implied volatility",
    definition:
      "The annualized volatility backed out from option prices nearest the current stock price.",
    interpretation:
      "Higher ATM IV means the market is charging more for movement. It describes magnitude, not up or down direction.",
    caution:
      "Compare IV across dates and expiries only after accounting for time to expiration.",
  },
  ivExpectedMove: {
    label: "IV-based expected move",
    definition:
      "A one-standard-deviation move estimate derived from ATM implied volatility for this expiry.",
    interpretation:
      "Read ±6% as an approximate volatility range, not a promise that the stock stays inside it.",
    formula: "ATM IV × √(days to expiry ÷ 365)",
    caution:
      "This simplified log-normal estimate does not predict direction or earnings jump risk perfectly.",
  },
  atmStraddle: {
    label: "At-the-money straddle",
    definition:
      "The midpoint price of the nearest-strike call plus put for the selected expiration.",
    interpretation:
      "It is the option market’s dollar cost for owning movement in either direction. Divide by spot for a move percentage.",
    formula: "ATM call midpoint + ATM put midpoint",
    caution:
      "This is a price-based benchmark before fees, spread, and post-event volatility changes.",
  },
  ivRank: {
    label: "IV rank",
    definition:
      "The current IV’s position between its 52-week low and 52-week high.",
    interpretation:
      "80% means current IV is near the high end of its own one-year range; it does not mean IV was lower on 80% of days.",
    formula: "(current IV − 52w low) ÷ (52w high − 52w low)",
  },
  atmSkew: {
    label: "ATM skew",
    definition:
      "The average put IV minus average call IV near the at-the-money strike.",
    interpretation:
      "Positive skew means comparable puts carry more implied volatility than calls; negative skew means the reverse.",
    caution:
      "Skew is options-pricing context, not a directional forecast by itself.",
  },
  daysToCover: {
    label: "Short interest days to cover",
    definition:
      "Reported shares sold short divided by average daily share volume.",
    interpretation:
      "A larger number suggests short positions would take longer to cover at typical volume, a crowding/liquidity measure.",
    caution:
      "Short-interest reports are periodic and can lag current positioning.",
  },
  putCallVolume: {
    label: "Put/call volume ratio",
    definition:
      "Today’s put contract volume divided by call contract volume in the observed chain.",
    interpretation:
      "Above 1 means more put than call contracts traded; below 1 means more calls. Volume alone does not reveal whether trades opened or closed.",
  },
  putCallOpenInterest: {
    label: "Put/call open-interest ratio",
    definition:
      "Outstanding put contracts divided by outstanding call contracts.",
    interpretation:
      "This describes the chain’s existing positioning balance, not the intent or direction of each position.",
  },
  corporateActions: {
    label: "Corporate actions",
    definition: "Dividend and split events found in the provider data window.",
    interpretation:
      "Use these counts as contract-adjustment and price-history context, not as a forecast signal.",
  },
  providerSignalScore: {
    label: "Provider signal score",
    definition:
      "A heuristic summary of available crowding, options-flow, and corporate-action flags.",
    interpretation:
      "Use it to find unusual context worth inspecting. It is not the LightGBM forecast and not a trading recommendation.",
    caution:
      "The score is descriptive and has not been presented here as a standalone predictive model.",
  },
  probabilityDensity: {
    label: "Probability density",
    definition:
      "A log-normal illustration centered on spot using the selected ATM IV and time to expiry.",
    interpretation:
      "The curve shows where the simplified model places more or less density. The marked bands compare straddle-priced and IV-derived ranges.",
    caution:
      "Earnings returns can be discontinuous and have fatter tails than this smooth distribution.",
  },
  forecastDistribution: {
    label: "ML forecast distribution",
    definition:
      "LightGBM quantile estimates for the absolute earnings move. P10 through P90 describe increasing move magnitudes.",
    interpretation:
      "P50 is the model median. P10 means 10% of comparable outcomes are modeled at or below that magnitude; P90 means 90% are at or below it.",
    caution:
      "These are calibrated magnitude estimates, not direction probabilities or guarantees for this event.",
  },
  termStructure: {
    label: "Expected-move term structure",
    definition:
      "The option-implied move translated into an upper and lower price range for each expiry.",
    interpretation:
      "Compare how the priced range widens with time and whether the earnings expiry stands out from nearby contracts.",
    formula: "spot × (1 ± expected-move percentage)",
  },
  history: {
    label: "Implied versus realized history",
    definition:
      "Past pre-event implied moves compared with each signed close-to-close earnings reaction.",
    interpretation:
      "A realized bar outside the implied band means that quarter moved more than options priced; direction comes from the bar’s sign.",
    caution:
      "A small number of quarters is context, not a stable law for the next report.",
  },
  greeks: {
    label: "ATM option Greeks",
    definition:
      "Local sensitivities of the selected ATM call: delta to stock price, gamma to delta, vega to IV, and theta to time.",
    interpretation:
      "Delta is hedge ratio; gamma is delta curvature; vega is value sensitivity to volatility; theta is estimated daily time decay.",
    caution:
      "Greeks change as spot, volatility, and time change and are not forecasts of realized P&L.",
  },
};
