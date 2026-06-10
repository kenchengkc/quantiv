/** Shared tick shape for batch-price / cron quote caches. */
export type QuoteTick = {
  symbol: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
};

/** Derive change / changePct from price and previousClose.

 *  Finnhub's `d`/`dp` are anchored to its own `pc`, which lags after weekends
 *  and can disagree once refresh-prices overrides `previousClose` with
 *  `prevclose:{SYM}` from Polygon grouped-daily. Always recompute when both
 *  price and previousClose are present so the calendar matches the session
 *  move (e.g. JBL −0.8% today, not a stale +2.56% dp). */
export function enrichQuoteTick(t: QuoteTick): QuoteTick {
  if (t.price == null || !(t.price > 0)) return t;
  const pc = t.previousClose;
  if (pc == null || !(pc > 0)) return t;
  const change = t.price - pc;
  const changePct = change / pc;
  return { ...t, change, changePct, previousClose: pc };
}
