'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { GripVertical, X, Plus } from 'lucide-react';
import { COMPANY_NAMES } from '@/lib/companyNames';

const WATCH_KEY = 'quantiv-watchlist';

type SymbolSummary = {
  symbol: string;
  spot_price: number | null;
  expected_move?: {
    earnings_date?: string;
    straddle_pct: number | null;
    iv_pct: number | null;
    dte: number;
    timing?: string;
    lead_time_days?: number;
  };
  next_earnings?: string | null;
  next_earnings_timing?: string;
};

type Tick = { change: number | null; changePct: number | null };

function companyName(t: string) {
  return COMPANY_NAMES[t] || t;
}

function parseLocalDate(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}
function shortDate(iso?: string | null) {
  if (!iso) return null;
  return parseLocalDate(iso).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}
function timingText(t?: string | null) {
  const k = (t || '').toLowerCase();
  if (k === 'bmo' || k === 'before_market_open' || k === 'before_open') return 'Before open';
  if (k === 'amc' || k === 'after_market_close' || k === 'after_close') return 'After close';
  return null;
}

function Logo({ ticker, size = 36 }: { ticker: string; size?: number }) {
  const [err, setErr] = useState(false);
  if (err) {
    return (
      <div
        className="serif"
        style={{
          width: size,
          height: size,
          borderRadius: 8,
          background: 'var(--bg-3)',
          border: '1px solid var(--line)',
          display: 'grid',
          placeItems: 'center',
          color: 'var(--ink-2)',
          fontSize: Math.max(10, size * 0.32),
          fontWeight: 700,
        }}
      >
        {ticker.slice(0, 3)}
      </div>
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return (
    <img
      src={`https://assets.parqet.com/logos/symbol/${ticker}?format=png`}
      alt={ticker}
      onError={() => setErr(true)}
      style={{
        width: size,
        height: size,
        borderRadius: 8,
        objectFit: 'cover',
        background: 'var(--paper)',
        border: '1px solid var(--line)',
      }}
    />
  );
}

function readWatchlist(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(WATCH_KEY) || '[]');
    if (!Array.isArray(raw)) return [];
    return raw.filter((x): x is string => typeof x === 'string');
  } catch {
    return [];
  }
}

function writeWatchlist(list: string[]) {
  localStorage.setItem(WATCH_KEY, JSON.stringify(list));
}

export default function WatchlistPage() {
  const [tickers, setTickers] = useState<string[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const [summaries, setSummaries] = useState<Record<string, SymbolSummary>>({});
  const [live, setLive] = useState<Record<string, Tick>>({});
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [overIdx, setOverIdx] = useState<number | null>(null);

  useEffect(() => {
    setTickers(readWatchlist());
    setHydrated(true);
    const onStorage = (e: StorageEvent) => {
      if (e.key === WATCH_KEY) setTickers(readWatchlist());
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  // Load per-symbol summary from pre-generated /symbols/*.json
  useEffect(() => {
    if (!hydrated || tickers.length === 0) return;
    let cancelled = false;
    (async () => {
      const missing = tickers.filter((t) => !(t in summaries));
      await Promise.all(
        missing.map(async (t) => {
          try {
            const res = await fetch(`/symbols/${t}.json`, { cache: 'no-store' });
            if (!res.ok) return;
            const json = (await res.json()) as SymbolSummary;
            if (!cancelled) setSummaries((s) => ({ ...s, [t]: json }));
          } catch {
            /* ignore */
          }
        }),
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [tickers, hydrated, summaries]);

  // Batch-fetch live prices for the whole watchlist.
  useEffect(() => {
    if (!hydrated || tickers.length === 0) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/stocks/batch-price?symbols=${tickers.join(',')}`,
          { cache: 'no-store' },
        );
        if (!res.ok) return;
        const json = (await res.json()) as {
          data: { symbol: string; change: number | null; changePct: number | null }[];
        };
        if (cancelled) return;
        const next: Record<string, Tick> = {};
        for (const t of json.data) {
          next[t.symbol] = { change: t.change, changePct: t.changePct };
        }
        setLive(next);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tickers, hydrated]);

  const remove = useCallback((t: string) => {
    setTickers((prev) => {
      const next = prev.filter((x) => x !== t);
      writeWatchlist(next);
      return next;
    });
  }, []);

  const reorder = useCallback((from: number, to: number) => {
    if (from === to) return;
    setTickers((prev) => {
      const next = [...prev];
      const [item] = next.splice(from, 1);
      next.splice(to, 0, item);
      writeWatchlist(next);
      return next;
    });
  }, []);

  const total = tickers.length;

  if (!hydrated) {
    return (
      <div style={{ maxWidth: 960, margin: '0 auto', padding: '40px 28px' }}>
        <div style={{ color: 'var(--ink-3)', fontSize: 13 }}>Loading watchlist…</div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '0 28px 60px' }}>
      {/* Header */}
      <div
        style={{
          padding: '24px 0 20px',
          borderBottom: '1px solid var(--line)',
          display: 'flex',
          alignItems: 'flex-end',
          justifyContent: 'space-between',
          gap: 24,
          flexWrap: 'wrap',
        }}
      >
        <div>
          <div
            style={{
              fontSize: 10,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              color: 'var(--ink-3)',
              marginBottom: 6,
            }}
          >
            Your list
          </div>
          <h1
            className="serif"
            style={{
              margin: 0,
              fontSize: 40,
              fontWeight: 800,
              letterSpacing: '-0.025em',
              lineHeight: 1,
              textTransform: 'uppercase',
            }}
          >
            Watchlist
          </h1>
        </div>
        <div
          className="mono tnum"
          style={{ fontSize: 11, color: 'var(--ink-4)', letterSpacing: '0.08em' }}
        >
          {total} {total === 1 ? 'ticker' : 'tickers'}
        </div>
      </div>

      {total === 0 ? <EmptyState /> : (
        <ul style={{ listStyle: 'none', padding: 0, margin: '12px 0 0' }}>
          {tickers.map((t, i) => {
            const sum = summaries[t];
            const em = sum?.expected_move;
            const movePct = em?.straddle_pct ?? em?.iv_pct ?? null;
            const earningsIso = em?.earnings_date ?? sum?.next_earnings ?? null;
            const earningsLabel = shortDate(earningsIso);
            const timing = timingText(em?.timing ?? sum?.next_earnings_timing);
            const dte = em?.lead_time_days ?? em?.dte ?? null;
            const tick = live[t];
            const spot = sum?.spot_price ?? null;
            const up = (tick?.changePct ?? 0) >= 0;
            const isDragging = dragIdx === i;
            const isOver = overIdx === i && dragIdx !== null && dragIdx !== i;

            return (
              <li
                key={t}
                draggable
                onDragStart={(e) => {
                  setDragIdx(i);
                  e.dataTransfer.effectAllowed = 'move';
                  e.dataTransfer.setData('text/plain', String(i));
                }}
                onDragEnter={() => setOverIdx(i)}
                onDragOver={(e) => {
                  e.preventDefault();
                  e.dataTransfer.dropEffect = 'move';
                }}
                onDragLeave={(e) => {
                  if (e.currentTarget === e.target) setOverIdx((x) => (x === i ? null : x));
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  const from = Number(e.dataTransfer.getData('text/plain'));
                  if (!Number.isNaN(from)) reorder(from, i);
                  setDragIdx(null);
                  setOverIdx(null);
                }}
                onDragEnd={() => {
                  setDragIdx(null);
                  setOverIdx(null);
                }}
                style={{
                  display: 'grid',
                  gridTemplateColumns:
                    '18px 36px minmax(0, 1.6fr) minmax(0, 1fr) minmax(0, 1.2fr) auto 28px',
                  alignItems: 'center',
                  gap: 14,
                  padding: '14px 12px',
                  borderBottom: '1px solid var(--line)',
                  background: isOver ? 'var(--bg-3)' : 'transparent',
                  opacity: isDragging ? 0.35 : 1,
                  transition: 'background 120ms ease, opacity 120ms ease',
                  cursor: 'grab',
                }}
              >
                <span
                  aria-hidden
                  style={{
                    display: 'inline-flex',
                    color: 'var(--ink-4)',
                  }}
                >
                  <GripVertical size={16} />
                </span>

                <Link
                  href={`/${t}`}
                  aria-label={`Open ${t}`}
                  style={{ display: 'inline-flex' }}
                  onDragStart={(e) => e.preventDefault()}
                >
                  <Logo ticker={t} size={36} />
                </Link>

                <Link
                  href={`/${t}`}
                  style={{ textDecoration: 'none', minWidth: 0, display: 'block' }}
                  onDragStart={(e) => e.preventDefault()}
                >
                  <div
                    className="serif"
                    style={{
                      fontSize: 18,
                      fontWeight: 700,
                      color: 'var(--ink)',
                      letterSpacing: '-0.01em',
                      lineHeight: 1.1,
                    }}
                  >
                    {t}
                  </div>
                  <div
                    style={{
                      fontSize: 11,
                      color: 'var(--ink-4)',
                      marginTop: 2,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {companyName(t)}
                  </div>
                </Link>

                <div style={{ minWidth: 0 }}>
                  <div
                    className="serif tnum"
                    style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}
                  >
                    {spot !== null ? `$${spot.toFixed(2)}` : '—'}
                  </div>
                  {tick?.changePct !== null && tick?.changePct !== undefined && (
                    <div
                      className="mono tnum"
                      style={{
                        fontSize: 11,
                        color: up ? 'var(--up)' : 'var(--down)',
                        marginTop: 2,
                      }}
                    >
                      {up ? '▲' : '▼'} {Math.abs(tick.changePct * 100).toFixed(2)}%
                    </div>
                  )}
                </div>

                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 10,
                      letterSpacing: '0.14em',
                      textTransform: 'uppercase',
                      color: 'var(--ink-3)',
                      marginBottom: 2,
                    }}
                  >
                    Reports
                  </div>
                  <div
                    className="mono tnum"
                    style={{ fontSize: 12, color: 'var(--ink-2)' }}
                  >
                    {earningsLabel
                      ? `${earningsLabel}${dte !== null ? ` · ${dte}d` : ''}`
                      : '—'}
                  </div>
                  {timing && (
                    <div
                      style={{ fontSize: 10.5, color: 'var(--ink-4)', marginTop: 2 }}
                    >
                      {timing}
                    </div>
                  )}
                </div>

                <div
                  className="serif tnum"
                  style={{
                    fontSize: 20,
                    fontWeight: 700,
                    color: 'var(--ink)',
                    textAlign: 'right',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {movePct !== null ? (
                    <>
                      ±{(movePct * 100).toFixed(1)}
                      <span style={{ fontSize: 11, color: 'var(--ink-3)', marginLeft: 1 }}>
                        %
                      </span>
                    </>
                  ) : (
                    <span style={{ fontSize: 12, color: 'var(--ink-4)' }}>—</span>
                  )}
                </div>

                <button
                  onClick={() => remove(t)}
                  onDragStart={(e) => e.preventDefault()}
                  title={`Remove ${t}`}
                  aria-label={`Remove ${t}`}
                  style={{
                    width: 28,
                    height: 28,
                    display: 'grid',
                    placeItems: 'center',
                    borderRadius: 999,
                    border: '1px solid var(--line)',
                    background: 'transparent',
                    color: 'var(--ink-3)',
                    cursor: 'pointer',
                    transition: 'color 140ms ease, border-color 140ms ease, background 140ms ease',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.color = 'var(--down)';
                    e.currentTarget.style.borderColor = 'var(--down)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.color = 'var(--ink-3)';
                    e.currentTarget.style.borderColor = 'var(--line)';
                  }}
                >
                  <X size={14} />
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {total > 0 && (
        <div
          style={{
            marginTop: 24,
            fontSize: 11,
            color: 'var(--ink-4)',
            display: 'flex',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 8,
          }}
        >
          <span style={{ fontStyle: 'italic' }}>
            Drag to reorder · click × to remove
          </span>
          <span>
            Add from any ticker page with{' '}
            <Plus
              size={11}
              style={{ display: 'inline', verticalAlign: '-1px', marginRight: 2 }}
            />
          </span>
        </div>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div
      style={{
        marginTop: 40,
        padding: '60px 24px',
        border: '1px dashed var(--line)',
        borderRadius: 12,
        textAlign: 'center',
      }}
    >
      <div
        className="serif"
        style={{
          fontSize: 22,
          fontWeight: 700,
          letterSpacing: '-0.01em',
          marginBottom: 8,
        }}
      >
        Nothing on your watchlist yet
      </div>
      <div style={{ color: 'var(--ink-3)', fontSize: 13, marginBottom: 20 }}>
        Open any ticker and tap the{' '}
        <Plus size={12} style={{ display: 'inline', verticalAlign: '-1px' }} /> icon next
        to its name to add it here.
      </div>
      <Link
        href="/"
        className="chip"
        style={{ textDecoration: 'none', display: 'inline-flex' }}
      >
        Browse earnings
      </Link>
    </div>
  );
}
