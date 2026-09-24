export type AboutStoryKind = "market" | "history" | "model";

export const ABOUT_STATS = [
  {
    value: 1424,
    from: 0,
    suffix: "",
    decimals: 0,
    kicker: "Names",
    label: "tracked",
  },
  {
    value: 12.4,
    from: 0,
    suffix: "K",
    decimals: 1,
    kicker: "Chains",
    label: "snapshots / week",
  },
  {
    value: 8,
    from: 0,
    suffix: " yrs",
    decimals: 0,
    kicker: "History",
    label: "realized data",
  },
  {
    value: 60,
    from: 1440,
    suffix: " min",
    decimals: 0,
    kicker: "Refresh",
    label: "chain → UI",
  },
] as const;

export const ABOUT_STORIES: ReadonlyArray<{
  kind: AboutStoryKind;
  kicker: string;
  title: string;
  caption: string;
}> = [
  {
    kind: "market",
    kicker: "Market",
    title: "Market-implied expectations",
    caption: "The ATM straddle provides a market-implied estimate of movement in either direction.",
  },
  {
    kind: "history",
    kicker: "History",
    title: "Historical earnings reactions",
    caption: "Compare historical earnings reactions with the option-implied ranges available before each event.",
  },
  {
    kind: "model",
    kicker: "Model",
    title: "Model-estimated ranges",
    caption: "P10–P90 describes a conditional range of absolute moves, using the straddle as a market benchmark.",
  },
];

export const METHODOLOGY_SECTIONS = [
  {
    id: "methodology-atm-iv",
    kicker: "ATM IV",
    title: "From option quotes to volatility",
    tex: String.raw`\sigma_{\mathrm{ATM}}=\tfrac{1}{2}\bigl(\sigma_C+\sigma_P\bigr)`,
    note: "Same-strike call and put IVs are averaged after crossed, stale, illiquid, and excessive-spread quotes are filtered.",
  },
  {
    id: "methodology-straddle",
    kicker: "Straddle EM",
    title: "Straddle-implied expected move",
    tex: String.raw`\mathrm{EM}_{\text{straddle}}=\frac{C_{\mathrm{mid}}+P_{\mathrm{mid}}}{S_0}`,
    note: "Call midpoint plus put midpoint, normalized by spot. It is a market price for two-sided movement before spread, fees, and post-event IV change.",
  },
  {
    id: "methodology-iv-move",
    kicker: "IV-based EM",
    title: "Expected move from annualized volatility",
    tex: String.raw`\mathrm{EM}_{\mathrm{IV}}=\sigma_{\mathrm{ATM}}\sqrt{\tfrac{\mathrm{DTE}}{365}}`,
    note: "Annualized ATM IV is scaled to the selected expiry with square-root-of-time so it can be compared with the straddle range.",
  },
  {
    id: "methodology-greeks",
    kicker: "Greeks",
    title: "Local option sensitivities",
    tex: String.raw`\begin{aligned}\Delta_{\text{call}}&=e^{-qT}N(d_1)\\[2pt]\Gamma&=\tfrac{e^{-qT}\varphi(d_1)}{S\sigma\sqrt{T}}\\[2pt]\nu&=Se^{-qT}\varphi(d_1)\sqrt{T}\end{aligned}`,
    note: "Quantiv displays the chain's published ATM delta, gamma, vega, and theta as measures of local option sensitivity. These measures do not forecast realized profit or loss.",
  },
  {
    id: "methodology-history",
    kicker: "Hist edge",
    title: "Implied versus historical realized moves",
    tex: String.raw`\text{hist\_edge}=\frac{\mathrm{EM}_{\text{straddle}}-\mu_{4\mathrm{Q},|\Delta|}}{\mu_{4\mathrm{Q},|\Delta|}}`,
    note: "The current implied move is compared with recent absolute earnings reactions. The limited sample provides historical context and should not be treated as a standalone trading signal.",
  },
  {
    id: "methodology-forecast",
    kicker: "Forecast",
    title: "LightGBM quantile ensemble",
    tex: String.raw`\hat{y}_{\tau}=\arg\min_{\hat{y}}\sum_i\rho_{\tau}(y_i-\hat{y}),\quad\tau\in\{0.10,0.25,0.50,0.75,0.90\}`,
    note: "Five quantile heads estimate absolute earnings moves from point-in-time features using walk-forward training. P10–P90 is a conditional range, not guaranteed coverage.",
  },
  {
    id: "methodology-exceedance",
    kicker: "Market-relative probability",
    title: "Straddle exceedance",
    tex: String.raw`\widehat{P}(|r|>s)=1-\operatorname{lerp}\!\left((q_i,\tau_i),(q_{i+1},\tau_{i+1});s\right)`,
    note: "The straddle threshold is interpolated across the served quantiles. Outside the P10–P90 range, probability bounds reflect the limited precision available for tail estimates.",
  },
] as const;
