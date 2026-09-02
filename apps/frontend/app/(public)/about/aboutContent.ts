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
    title: "What is priced?",
    caption: "The ATM straddle frames the two-sided move the market is charging for.",
  },
  {
    kind: "history",
    kicker: "History",
    title: "What actually happened?",
    caption: "Past earnings reactions replay against the ranges options priced before each event.",
  },
  {
    kind: "model",
    kicker: "Model",
    title: "What does the model expect?",
    caption: "P10–P90 shows a conditional move range; the straddle becomes the comparison threshold.",
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
    title: "What the market charges for movement",
    tex: String.raw`\mathrm{EM}_{\text{straddle}}=\frac{C_{\mathrm{mid}}+P_{\mathrm{mid}}}{S_0}`,
    note: "Call midpoint plus put midpoint, normalized by spot. It is a market price for two-sided movement before spread, fees, and post-event IV change.",
  },
  {
    id: "methodology-iv-move",
    kicker: "IV-based EM",
    title: "Scale annualized IV to the expiry",
    tex: String.raw`\mathrm{EM}_{\mathrm{IV}}=\sigma_{\mathrm{ATM}}\sqrt{\tfrac{\mathrm{DTE}}{365}}`,
    note: "Annualized ATM IV is scaled to the selected expiry with square-root-of-time so it can be compared with the straddle range.",
  },
  {
    id: "methodology-greeks",
    kicker: "Greeks",
    title: "Local option sensitivities",
    tex: String.raw`\begin{aligned}\Delta_{\text{call}}&=e^{-qT}N(d_1)\\[2pt]\Gamma&=\tfrac{e^{-qT}\varphi(d_1)}{S\sigma\sqrt{T}}\\[2pt]\nu&=Se^{-qT}\varphi(d_1)\sqrt{T}\end{aligned}`,
    note: "Quantiv surfaces the chain's published ATM delta, gamma, vega, and theta as local sensitivity context rather than realized P&L forecasts.",
  },
  {
    id: "methodology-history",
    kicker: "Hist edge",
    title: "Rich versus recent realized moves",
    tex: String.raw`\text{hist\_edge}=\frac{\mathrm{EM}_{\text{straddle}}-\mu_{4\mathrm{Q},|\Delta|}}{\mu_{4\mathrm{Q},|\Delta|}}`,
    note: "Today's implied move is compared with recent absolute earnings reactions. The sample is intentionally small, so this is context rather than a standalone signal.",
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
    note: "The straddle threshold is mapped into the served P10/P25/P50/P75/P90 quantiles by interpolation. Outside that range Quantiv reports bounds instead of inventing tail precision.",
  },
] as const;
