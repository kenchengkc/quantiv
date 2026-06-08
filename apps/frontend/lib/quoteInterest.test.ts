import { describe, expect, it, vi } from "vitest";
import { writeQuoteInterest } from "./quoteInterest";

describe("quote interest writes", () => {
  it("uses one multi-member ZADD plus one expiry", async () => {
    const zadd = vi.fn();
    const expire = vi.fn();
    const exec = vi.fn().mockResolvedValue([]);
    const redis = {
      pipeline: () => ({ zadd, expire, exec }),
    };

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
    const zadd = vi.fn();
    const pipeline = {
      zadd,
      expire: vi.fn(),
      exec: vi.fn().mockResolvedValue([]),
    };
    const symbols = Array.from({ length: 125 }, (_, index) => `S${index}`);

    await writeQuoteInterest(
      { pipeline: () => pipeline } as never,
      symbols,
      "batch",
      1_000,
    );

    expect(zadd.mock.calls[0]).toHaveLength(102);
  });
});
