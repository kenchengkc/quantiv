/** Persist earnings-calendar week JSON + live quotes across back/forward
 *  navigations and full reloads (module-level Maps alone are wiped on reload). */

const LIVE_KEY = 'quantiv:earnings:live';
const WEEK_PREFIX = 'quantiv:earnings:week:';
const MAX_WEEK_ENTRIES = 3;

export type CachedLiveQuote = {
  change: number | null;
  changePct: number | null;
  realizedMovePct?: number | null;
  realizedDate?: string | null;
};

export type LiveQuoteMap = Record<string, CachedLiveQuote>;

const weekMemory = new Map<string, unknown>();
let liveMemory: LiveQuoteMap = {};

function readSession(key: string): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSession(key: string, value: string) {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // Quota exceeded — best-effort only.
  }
}

function trimWeekEntries(keepIso: string) {
  if (typeof window === 'undefined') return;
  try {
    const keys = Object.keys(window.sessionStorage).filter((k) =>
      k.startsWith(WEEK_PREFIX),
    );
    if (keys.length <= MAX_WEEK_ENTRIES) return;
    const drop = keys
      .filter((k) => k !== WEEK_PREFIX + keepIso)
      .slice(0, keys.length - MAX_WEEK_ENTRIES);
    for (const k of drop) window.sessionStorage.removeItem(k);
  } catch {
    // ignore
  }
}

export function readLiveQuoteCache(): LiveQuoteMap {
  if (Object.keys(liveMemory).length > 0) return { ...liveMemory };
  const raw = readSession(LIVE_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as LiveQuoteMap;
    if (parsed && typeof parsed === 'object') {
      liveMemory = parsed;
      return { ...parsed };
    }
  } catch {
    // ignore corrupt cache
  }
  return {};
}

export function writeLiveQuoteCache(live: LiveQuoteMap) {
  liveMemory = live;
  writeSession(LIVE_KEY, JSON.stringify(live));
}

export function readWeekCache<T>(iso: string): T | null {
  if (weekMemory.has(iso)) return weekMemory.get(iso) as T;
  const raw = readSession(WEEK_PREFIX + iso);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as T;
    weekMemory.set(iso, parsed);
    return parsed;
  } catch {
    return null;
  }
}

export function writeWeekCache<T>(iso: string, data: T) {
  weekMemory.set(iso, data);
  writeSession(WEEK_PREFIX + iso, JSON.stringify(data));
  trimWeekEntries(iso);
}

export function hasWeekCache(iso: string): boolean {
  return weekMemory.has(iso) || readSession(WEEK_PREFIX + iso) != null;
}

/** Seed the in-memory week cache only (no sessionStorage write). Lets the
 *  server-rendered first paint flow through the same warm-path gating as a
 *  cached week — the calendar renders straight from SSR HTML with no skeleton
 *  hold or client round-trip. Safe to call during render: it's an idempotent
 *  Map set, and the mount effect still revalidates from the CDN afterwards. */
export function primeWeekMemory<T>(iso: string, data: T) {
  if (!weekMemory.has(iso)) weekMemory.set(iso, data);
}
