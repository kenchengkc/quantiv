import type { Redis } from "@upstash/redis";

export type InterestContext =
  | "symbol"
  | "watchlist"
  | "earnings"
  | "screener"
  | "batch";

export function interestContext(raw: string | null): InterestContext {
  if (
    raw === "symbol" ||
    raw === "watchlist" ||
    raw === "earnings" ||
    raw === "screener"
  ) {
    return raw;
  }
  return "batch";
}

function interestIncrement(context: InterestContext): number {
  switch (context) {
    case "symbol":
      return 30;
    case "watchlist":
      return 18;
    case "earnings":
      return 10;
    case "screener":
      return 6;
    default:
      return 3;
  }
}

// Per-instance write throttle. Clients poll batch-price every 30s with the
// same symbol set; re-ZADDing identical members each poll only refreshes a
// decay window the Railway worker samples every ~5 min, so repeats within
// the window add Upstash commands without changing the ranking. Keyed by
// context so a higher-boost context (e.g. symbol page) is never suppressed
// by a recent low-boost one (e.g. batch).
const THROTTLE_WINDOW_S = 30;
const THROTTLE_MAX_ENTRIES = 8192;
const lastRecorded = new Map<string, number>();

function pruneThrottle(nowSeconds: number): void {
  if (lastRecorded.size < THROTTLE_MAX_ENTRIES) return;
  for (const [key, at] of lastRecorded) {
    if (nowSeconds - at >= THROTTLE_WINDOW_S) lastRecorded.delete(key);
  }
  if (lastRecorded.size >= THROTTLE_MAX_ENTRIES) lastRecorded.clear();
}

export function resetQuoteInterestThrottleForTests(): void {
  lastRecorded.clear();
}

export async function writeQuoteInterest(
  redis: Redis,
  symbols: string[],
  context: InterestContext,
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<void> {
  pruneThrottle(nowSeconds);
  const due = symbols.filter((symbol) => {
    const at = lastRecorded.get(`${context}:${symbol}`);
    return at === undefined || nowSeconds - at >= THROTTLE_WINDOW_S;
  });
  const score = nowSeconds + interestIncrement(context) * 60;
  const members = due
    .slice(0, 100)
    .map((symbol) => ({ score, member: symbol }));
  if (members.length === 0) return;

  const pipeline = redis.pipeline();
  pipeline.zadd(
    "quote:interest",
    { gt: true },
    members[0],
    ...members.slice(1),
  );
  pipeline.expire("quote:interest", 2 * 60 * 60);
  await pipeline.exec();
  for (const { member } of members) {
    lastRecorded.set(`${context}:${member}`, nowSeconds);
  }
}
