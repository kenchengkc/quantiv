export const WORKER_HEARTBEAT_FRESH_MS = 150_000;

export type RailwayWorkerStatus = {
  updated_at?: string;
  lease_protocol?: number;
};

export type RailwayOwnershipState = {
  deferToRailway: boolean;
  leaseProtocolEnabled: boolean;
  reason: "legacy_railway" | "railway_healthy" | "railway_stale";
};

export function parseWorkerStatus(raw: unknown): RailwayWorkerStatus | null {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    return raw as RailwayWorkerStatus;
  }
  if (typeof raw !== "string") return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as RailwayWorkerStatus)
      : null;
  } catch {
    return null;
  }
}

export function isFreshRailwayHeartbeat(
  raw: unknown,
  nowMs = Date.now(),
): boolean {
  const status = parseWorkerStatus(raw);
  if (!status?.updated_at) return false;
  const updatedAt = Date.parse(status.updated_at);
  return (
    Number.isFinite(updatedAt) && nowMs - updatedAt < WORKER_HEARTBEAT_FRESH_MS
  );
}

export function classifyRailwayOwnership(
  statusRaw: unknown,
  leaseOwner: unknown,
  protocolRaw: unknown,
  nowMs = Date.now(),
): RailwayOwnershipState {
  const status = parseWorkerStatus(statusRaw);
  const protocolEnabled =
    protocolRaw === "1" || protocolRaw === 1 || status?.lease_protocol === 1;

  if (!protocolEnabled) {
    return {
      deferToRailway: true,
      leaseProtocolEnabled: false,
      reason: "legacy_railway",
    };
  }

  const railwayOwnsLease =
    typeof leaseOwner === "string" &&
    leaseOwner.length > 0 &&
    !leaseOwner.startsWith("vercel:");
  const healthy =
    railwayOwnsLease && isFreshRailwayHeartbeat(statusRaw, nowMs);
  return {
    deferToRailway: healthy,
    leaseProtocolEnabled: true,
    reason: healthy ? "railway_healthy" : "railway_stale",
  };
}
