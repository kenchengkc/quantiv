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

export async function writeQuoteInterest(
  redis: Redis,
  symbols: string[],
  context: InterestContext,
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<void> {
  const score = nowSeconds + interestIncrement(context) * 60;
  const members = symbols
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
}
