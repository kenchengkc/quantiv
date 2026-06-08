import { describe, expect, it } from "vitest";
import {
  isFreshMemoryQuote,
  isUsableSharedQuote,
} from "../../../../lib/quoteCachePolicy";

const cached = {
  at: 0,
  tick: {
    symbol: "AAPL",
    price: 100,
    previousClose: 99,
    change: 1,
    changePct: 1 / 99,
  },
};

describe("batch quote cache policy", () => {
  it("expires process-memory entries after five seconds", () => {
    expect(isFreshMemoryQuote(1_000, 5_999)).toBe(true);
    expect(isFreshMemoryQuote(1_000, 6_000)).toBe(false);
  });

  it("rejects shared entries older than seven days", () => {
    const sevenDaysMs = 7 * 24 * 60 * 60 * 1_000;
    expect(
      isUsableSharedQuote({ ...cached, at: 1_000 }, 1_000 + sevenDaysMs),
    ).toBe(true);
    expect(
      isUsableSharedQuote({ ...cached, at: 1_000 }, 1_001 + sevenDaysMs),
    ).toBe(false);
    expect(isUsableSharedQuote(null, 1_000)).toBe(false);
  });
});
