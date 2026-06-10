import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  resetQuoteInterestThrottleForTests,
  writeQuoteInterest,
} from "./quoteInterest";

function fakeRedis() {
  const zadd = vi.fn();
  const expire = vi.fn();
  const exec = vi.fn().mockResolvedValue([]);
  return {
    zadd,
    expire,
    exec,
    redis: { pipeline: () => ({ zadd, expire, exec }) },
  };
}

beforeEach(() => {
  resetQuoteInterestThrottleForTests();
});

describe("quote interest writes", () => {
  it("uses one multi-member ZADD plus one expiry", async () => {
    const { redis, zadd, expire, exec } = fakeRedis();

    await writeQuoteInterest(
      redis as never,
      ["AAPL", "MSFT", "NVDA"],
      "earnings",
      1_000,
    );

    expect(zadd).toHaveBeenCalledTimes(1);
    expect(zadd).toHaveBeenCalledWith(
      "quote:interest",
      { gt: true },
      { score: 1_600, member: "AAPL" },
      { score: 1_600, member: "MSFT" },
      { score: 1_600, member: "NVDA" },
    );
    expect(expire).toHaveBeenCalledWith("quote:interest", 7_200);
    expect(exec).toHaveBeenCalledTimes(1);
  });

  it("caps a request at one hundred symbols", async () => {
    const { redis, zadd } = fakeRedis();
    const symbols = Array.from({ length: 125 }, (_, index) => `S${index}`);

    await writeQuoteInterest(redis as never, symbols, "batch", 1_000);

    expect(zadd.mock.calls[0]).toHaveLength(102);
  });

  it("skips symbols re-recorded within the throttle window", async () => {
    const { redis, zadd, exec } = fakeRedis();

    await writeQuoteInterest(redis as never, ["AAPL", "MSFT"], "batch", 1_000);
    await writeQuoteInterest(redis as never, ["AAPL", "MSFT"], "batch", 1_010);

    expect(zadd).toHaveBeenCalledTimes(1);
    expect(exec).toHaveBeenCalledTimes(1);
  });

  it("writes only the symbols outside the throttle window", async () => {
    const { redis, zadd } = fakeRedis();

    await writeQuoteInterest(redis as never, ["AAPL"], "batch", 1_000);
    await writeQuoteInterest(redis as never, ["AAPL", "TSLA"], "batch", 1_010);

    expect(zadd).toHaveBeenCalledTimes(2);
    expect(zadd.mock.calls[1]).toEqual([
      "quote:interest",
      { gt: true },
      { score: 1_010 + 3 * 60, member: "TSLA" },
    ]);
  });

  it("records the same symbol again once the window elapses", async () => {
    const { redis, zadd } = fakeRedis();

    await writeQuoteInterest(redis as never, ["AAPL"], "batch", 1_000);
    await writeQuoteInterest(redis as never, ["AAPL"], "batch", 1_030);

    expect(zadd).toHaveBeenCalledTimes(2);
  });

  it("tracks contexts independently so boosts are not suppressed", async () => {
    const { redis, zadd } = fakeRedis();

    await writeQuoteInterest(redis as never, ["AAPL"], "batch", 1_000);
    await writeQuoteInterest(redis as never, ["AAPL"], "symbol", 1_010);

    expect(zadd).toHaveBeenCalledTimes(2);
    expect(zadd.mock.calls[1]).toEqual([
      "quote:interest",
      { gt: true },
      { score: 1_010 + 30 * 60, member: "AAPL" },
    ]);
  });

  it("does not mark symbols recorded when the pipeline fails", async () => {
    const zadd = vi.fn();
    const expire = vi.fn();
    const exec = vi.fn().mockRejectedValue(new Error("redis down"));
    const redis = { pipeline: () => ({ zadd, expire, exec }) };

    await expect(
      writeQuoteInterest(redis as never, ["AAPL"], "batch", 1_000),
    ).rejects.toThrow("redis down");

    exec.mockResolvedValue([]);
    await writeQuoteInterest(redis as never, ["AAPL"], "batch", 1_001);
    expect(zadd).toHaveBeenCalledTimes(2);
  });
});
