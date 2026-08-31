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
  | "forecastDistribution"
  | "termStructure"
  | "history"
  | "greeks";

export interface MetricDefinition {
  label: string;
  definition: string;
  calculation: readonly [string, string, string];
  formula: string;
  formulaTex: string;
  calculationDetails?: readonly [string, string, string];
  methodologyHref: string;
  use: string;
  caution: string;
}

export const METRIC_GLOSSARY: Record<MetricKey, MetricDefinition> = {
  atmIv: {
    label: "At-the-money implied volatility",
    definition:
      "The average IV back-solved from a same-strike ATM call and put quote.",
    calculation: [
      "ATM call + put mids",
      "Solve each IV; average",
      "Annualized ATM IV %",
    ],
    formula: "Find each σ where model price = market price; then average",
    formulaTex: String.raw`\begin{gathered} C_{\mathrm{mid}}\xrightarrow{\mathrm{BS}^{-1}}\sigma_C \qquad P_{\mathrm{mid}}\xrightarrow{\mathrm{BS}^{-1}}\sigma_P \\[3pt] \sigma_{\mathrm{ATM}}=\tfrac{1}{2}(\sigma_C+\sigma_P) \end{gathered}`,
    calculationDetails: [
      "Same symbol, expiry, and strike",
      "Match each model price to its quote",
      "Call/put average, annualized",
    ],
    methodologyHref: "/about#methodology-atm-iv",
    use: "Compare priced volatility across expiries and dates",
    caution: "Not direction; quote, rate, and dividend inputs matter",
  },
  ivExpectedMove: {
    label: "IV-based expected move",
    definition:
      "A volatility-scaled move range for the selected time to expiry.",
    calculation: ["ATM IV + DTE", "Scale by √time", "± move %"],
    formula: "ATM IV × √(days to expiry ÷ 365)",
    formulaTex: String.raw`\mathrm{EM}_{\mathrm{IV}}=\sigma_{\mathrm{ATM}}\sqrt{\tfrac{\mathrm{DTE}}{365}}`,
    calculationDetails: [
      "Annualized volatility and horizon",
      "Convert annual risk to this expiry",
      "One-standard-deviation move",
    ],
    methodologyHref: "/about#methodology-iv-move",
    use: "Benchmark the range priced through this expiry",
    caution: "A one-sigma estimate, not a hard boundary",
  },
  atmStraddle: {
    label: "At-the-money straddle",
    definition: "The nearest-strike call and put midpoint premiums combined.",
    calculation: ["Call + put mids", "Add; divide by spot", "$ move + move %"],
    formula: "ATM call midpoint + ATM put midpoint",
    formulaTex: String.raw`\mathrm{EM}_{\$}=C_{\mathrm{mid}}+P_{\mathrm{mid}},\quad \mathrm{EM}_{\%}=\tfrac{\mathrm{EM}_{\$}}{S_0}`,
    calculationDetails: [
      "Matched call and put markets",
      "Add mids; normalize by spot",
      "Dollar and percentage range",
    ],
    methodologyHref: "/about#methodology-straddle",
    use: "See the market cost of two-sided movement",
    caution: "Before spread, fees, and the post-event IV drop",
  },
  ivRank: {
    label: "IV rank",
    definition: "Current IV positioned between its 52-week low and high.",
    calculation: ["Current + 52w range", "Normalize low to high", "0–100 rank"],
    formula: "(current IV − 52w low) ÷ (52w high − 52w low)",
    formulaTex: String.raw`\mathrm{IV\ Rank}=\tfrac{\sigma_0-\sigma_{52\mathrm{w,low}}}{\sigma_{52\mathrm{w,high}}-\sigma_{52\mathrm{w,low}}}`,
    methodologyHref: "/about#models-and-math",
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
    formulaTex: String.raw`\mathrm{Skew}_{\mathrm{ATM}}=\overline{\sigma}_{P}-\overline{\sigma}_{C}`,
    methodologyHref: "/about#models-and-math",
    use: "Compare downside-option richness with upside",
    caution: "Positioning context, not a price forecast",
  },
  daysToCover: {
    label: "Short interest days to cover",
    definition: "Reported short shares scaled by typical daily trading volume.",
    calculation: ["Short shares + ADV", "Short shares ÷ ADV", "Trading days"],
    formula: "Reported shares short ÷ average daily volume",
    formulaTex: String.raw`\mathrm{DTC}=\tfrac{\mathrm{shares\ short}}{\mathrm{average\ daily\ volume}}`,
    methodologyHref: "/about#models-and-math",
    use: "Flag short crowding and potential unwind friction",
    caution: "Short-interest reports lag current positions",
  },
  putCallVolume: {
    label: "Put/call volume ratio",
    definition: "Observed put contract volume divided by call contract volume.",
    calculation: ["Put + call volume", "Put volume ÷ call", "Session ratio"],
    formula: "Observed put volume ÷ observed call volume",
    formulaTex: String.raw`\mathrm{P/C}_{\mathrm{volume}}=\tfrac{V_P}{V_C}`,
    methodologyHref: "/about#models-and-math",
    use: "Spot unusually put- or call-heavy trading",
    caution: "Does not reveal buy/sell or open/close intent",
  },
  putCallOpenInterest: {
    label: "Put/call open-interest ratio",
    definition:
      "Outstanding put contracts divided by outstanding call contracts.",
    calculation: ["Put + call OI", "Put OI ÷ call OI", "Position ratio"],
    formula: "Outstanding put contracts ÷ outstanding call contracts",
    formulaTex: String.raw`\mathrm{P/C}_{\mathrm{OI}}=\tfrac{\mathrm{OI}_P}{\mathrm{OI}_C}`,
    methodologyHref: "/about#models-and-math",
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
    formulaTex: String.raw`N_{\mathrm{actions}}=N_{\mathrm{dividends}}+N_{\mathrm{splits}}`,
    methodologyHref: "/about#models-and-math",
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
    formulaTex: String.raw`\mathrm{Score}=\operatorname{mean}\!\left[\min\!\left(\tfrac{\mathrm{DTC}}{10},1\right),\min\!\left(|\ln(\mathrm{P/C})|,1\right)\right]`,
    methodologyHref: "/about#models-and-math",
    use: "Prioritize unusual context for review",
    caution: "Descriptive heuristic, not the LightGBM forecast",
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
    formulaTex: String.raw`P_q=Q_q\!\left(|r_{\mathrm{earnings}}|\mid X_t\right)`,
    methodologyHref: "/about#methodology-forecast",
    use: "Size a range of plausible earnings-move magnitudes",
    caution: "Magnitude only; not direction or guaranteed coverage",
  },
  termStructure: {
    label: "Expected-move term structure",
    definition: "Option-implied ranges compared across expiration dates.",
    calculation: ["Each expiry's EM", "Spot × (1 ± EM)", "Price-range fan"],
    formula: "spot × (1 ± expected-move percentage)",
    formulaTex: String.raw`S_{\mathrm{range}}=S_0\!\left(1\pm\mathrm{EM}_{\%}\right)`,
    methodologyHref: "/about#methodology-iv-move",
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
    formulaTex: String.raw`r_{\mathrm{realized}}=\tfrac{S_{\mathrm{post}}}{S_{\mathrm{pre}}}-1`,
    methodologyHref: "/about#methodology-history",
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
    formulaTex: String.raw`\mathrm{Greek}_x=\tfrac{\partial V}{\partial x}`,
    methodologyHref: "/about#methodology-greeks",
    use: "Approximate local hedge and sensitivity exposure",
    caution: "They move with inputs and are not realized P&L",
  },
};
