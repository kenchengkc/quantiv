import { describe, expect, it } from "vitest";
import {
  classifyRailwayOwnership,
  isFreshRailwayHeartbeat,
} from "../../../../lib/quoteWorkerLease";

describe("Railway quote-worker heartbeat", () => {
  const now = Date.parse("2026-06-08T14:00:00Z");

  it("accepts fresh serialized and decoded statuses", () => {
    expect(
      isFreshRailwayHeartbeat(
        JSON.stringify({ updated_at: "2026-06-08T13:59:00Z" }),
        now,
      ),
    ).toBe(true);
    expect(
      isFreshRailwayHeartbeat({ updated_at: "2026-06-08T13:58:00Z" }, now),
    ).toBe(true);
  });

  it("rejects stale or malformed statuses", () => {
    expect(
      isFreshRailwayHeartbeat({ updated_at: "2026-06-08T13:57:29Z" }, now),
    ).toBe(false);
    expect(isFreshRailwayHeartbeat("not-json", now)).toBe(false);
    expect(isFreshRailwayHeartbeat(null, now)).toBe(false);
  });

  it("preserves legacy manual failback until the lease protocol is present", () => {
    expect(
      classifyRailwayOwnership(
        { updated_at: "2026-06-08T13:59:00Z" },
        null,
        null,
        now,
      ),
    ).toEqual({
      deferToRailway: true,
      leaseProtocolEnabled: false,
      reason: "legacy_railway",
    });
  });

  it("defers only when both the lease and heartbeat are healthy", () => {
    expect(
      classifyRailwayOwnership(
        {
          updated_at: "2026-06-08T13:59:00Z",
          lease_protocol: 1,
        },
        "railway-owner",
        "1",
        now,
      ),
    ).toEqual({
      deferToRailway: true,
      leaseProtocolEnabled: true,
      reason: "railway_healthy",
    });
    expect(
      classifyRailwayOwnership(
        {
          updated_at: "2026-06-08T13:55:00Z",
          lease_protocol: 1,
        },
        null,
        "1",
        now,
      ),
    ).toEqual({
      deferToRailway: false,
      leaseProtocolEnabled: true,
      reason: "railway_stale",
    });
  });

  it("does not mistake a Vercel fallback lease for Railway ownership", () => {
    expect(
      classifyRailwayOwnership(
        {
          updated_at: "2026-06-08T13:59:00Z",
          lease_protocol: 1,
        },
        "vercel:fallback-owner",
        "1",
        now,
      ),
    ).toEqual({
      deferToRailway: false,
      leaseProtocolEnabled: true,
      reason: "railway_stale",
    });
  });
});
