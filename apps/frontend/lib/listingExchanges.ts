let exchanges: Record<string, string> = {};
let exchangesVersion = 0;
let exchangesPromise: Promise<void> | null = null;
const exchangeSubscribers = new Set<() => void>();

export function loadListingExchanges(): Promise<void> {
  if (exchangesPromise) return exchangesPromise;
  if (typeof window === 'undefined') return Promise.resolve();
  exchangesPromise = fetch('/ticker-exchanges.json', { cache: 'force-cache' })
    .then((r) => (r.ok ? (r.json() as Promise<Record<string, string>>) : {}))
    .catch(() => ({}))
    .then((m) => {
      exchanges = m ?? {};
      exchangesVersion += 1;
      for (const fn of exchangeSubscribers) fn();
    });
  return exchangesPromise;
}

export function subscribeToListingExchanges(cb: () => void): () => void {
  exchangeSubscribers.add(cb);
  return () => {
    exchangeSubscribers.delete(cb);
  };
}

export function getListingExchangesVersion(): number {
  return exchangesVersion;
}

export function listingExchangeLabel(ticker: string): string {
  return exchanges[ticker.toUpperCase()] ?? 'US market';
}
