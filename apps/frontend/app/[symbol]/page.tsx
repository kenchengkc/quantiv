'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { AlertCircle, ArrowLeft, Loader2, TrendingDown, TrendingUp } from 'lucide-react';

interface Straddle {
  expiration: string;
  dte: number;
  atm_strike: number;
  atm_iv: number | null;
  atm_call_iv: number | null;
  atm_put_iv: number | null;
  straddle_mid: number | null;
  em_straddle: number | null;
  em_straddle_pct: number | null;
  em_iv: number | null;
  em_iv_pct: number | null;
  call_delta: number | null;
  call_gamma: number | null;
  call_vega: number | null;
  call_theta: number | null;
}

interface ExpectedMove {
  earnings_date?: string;
  expiration: string;
  dte: number;
  lead_time_days?: number;
  atm_strike: number;
  atm_iv: number | null;
  straddle_abs: number | null;
  straddle_pct: number | null;
  iv_pct: number | null;
  skew_atm?: number | null;
  term_slope?: number | null;
  total_vega?: number | null;
}

interface SymbolDetail {
  symbol: string;
  as_of_date: string;
  spot_price: number | null;
  expected_move?: ExpectedMove;
  straddle_features: Straddle[];
  earnings_history?: Array<{ date: string; timing: string }>;
  next_earnings?: string | null;
}

function logoUrl(ticker: string): string {
  return `https://assets.parqet.com/logos/symbol/${ticker}?format=png`;
}

function formatPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `${(v * 100).toFixed(2)}%`;
}

function formatDollar(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `$${v.toFixed(2)}`;
}

function formatDate(iso: string | undefined | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export default function SymbolPage() {
  const params = useParams();
  const router = useRouter();
  const symbol = (params.symbol as string)?.toUpperCase();

  const [data, setData] = useState<SymbolDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [logoError, setLogoError] = useState(false);

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`/symbols/${symbol}.json`, { cache: 'no-store' });
        if (!res.ok) throw new Error(`No local data for ${symbol}`);
        const json = (await res.json()) as SymbolDetail;
        if (!cancelled) setData(json);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (loading) {
    return (
      <div className="container mx-auto p-6 flex items-center justify-center min-h-[60vh]">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-emerald-400" />
          <p className="text-gray-400">Loading {symbol}…</p>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="container mx-auto p-6 max-w-2xl">
        <button
          onClick={() => router.push('/')}
          className="inline-flex items-center gap-2 text-sm text-gray-400 hover:text-white mb-4"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to earnings
        </button>
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-5 flex items-start gap-3">
          <AlertCircle className="h-5 w-5 text-red-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-red-300 font-medium">No data for {symbol}</p>
            <p className="text-sm text-red-400/80 mt-1">
              {error ?? ''} — this ticker doesn&apos;t have pre-computed options data locally.
              Run <code className="px-1.5 py-0.5 rounded bg-black/30 text-xs">python tools/build_frontend_data.py</code>
              {' '}with fresh parquet data to add it.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const em = data.expected_move;
  const spot = data.spot_price ?? 0;

  const lower = em?.straddle_pct && spot ? spot * (1 - em.straddle_pct) : null;
  const upper = em?.straddle_pct && spot ? spot * (1 + em.straddle_pct) : null;

  return (
    <div className="container mx-auto px-4 sm:px-6 lg:px-8 py-6 max-w-5xl">
      <button
        onClick={() => router.push('/')}
        className="inline-flex items-center gap-2 text-sm text-gray-400 hover:text-white mb-6"
      >
        <ArrowLeft className="w-4 h-4" />
        Earnings calendar
      </button>

      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <div className="w-16 h-16 rounded-xl overflow-hidden bg-gray-800 ring-1 ring-white/10 flex-shrink-0">
          {!logoError ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={logoUrl(symbol)}
              alt={symbol}
              className="w-full h-full object-cover"
              onError={() => setLogoError(true)}
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-blue-900 to-gray-800 text-white font-bold">
              {symbol.slice(0, 4)}
            </div>
          )}
        </div>
        <div>
          <h1 className="text-3xl font-bold text-white">{symbol}</h1>
          <div className="flex items-center gap-3 mt-1 text-sm">
            <span className="text-2xl font-semibold text-gray-100">
              {formatDollar(spot)}
            </span>
            <span className="text-gray-500">
              As of {data.as_of_date}
            </span>
          </div>
        </div>
      </div>

      {/* Headline expected move */}
      {em && (
        <div className="rounded-2xl bg-gray-900/60 border border-white/10 p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="text-[11px] uppercase tracking-wider text-gray-500">
                {em.earnings_date ? 'Next-earnings expected move' : 'Nearest-expiry expected move'}
              </div>
              <div className="text-xs text-gray-400 mt-1">
                {em.earnings_date && (
                  <>Earnings {formatDate(em.earnings_date)} · </>
                )}
                Expiry {formatDate(em.expiration)} · {em.dte}d out
              </div>
            </div>
            <div className="text-right">
              <div className="text-3xl font-bold text-emerald-400">
                ±{em.straddle_pct ? (em.straddle_pct * 100).toFixed(2) : '—'}%
              </div>
              <div className="text-xs text-gray-500 mt-1">ATM straddle</div>
            </div>
          </div>

          {/* Price range */}
          {lower && upper && (
            <div>
              <div className="flex items-center justify-between text-xs mb-2">
                <span className="inline-flex items-center gap-1 text-red-400">
                  <TrendingDown className="w-3 h-3" />
                  {formatDollar(lower)}
                </span>
                <span className="text-gray-500">Implied range · 68% band</span>
                <span className="inline-flex items-center gap-1 text-emerald-400">
                  {formatDollar(upper)}
                  <TrendingUp className="w-3 h-3" />
                </span>
              </div>
              <div className="h-2 bg-black/40 rounded-full overflow-hidden">
                <div className="h-full w-full bg-gradient-to-r from-red-500/80 via-yellow-400/70 to-emerald-500/80" />
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-6">
            <Stat label="ATM IV" value={formatPct(em.atm_iv)} />
            <Stat label="IV-based EM" value={formatPct(em.iv_pct)} />
            <Stat label="Straddle $" value={formatDollar(em.straddle_abs)} />
            <Stat label="ATM Strike" value={formatDollar(em.atm_strike)} />
          </div>
        </div>
      )}

      {/* Term structure */}
      {data.straddle_features.length > 0 && (
        <div className="rounded-2xl bg-gray-900/60 border border-white/10 p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-gray-300">
              Expected move by expiry
            </h2>
            <span className="text-xs text-gray-500">
              {data.straddle_features.length} expiries
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] uppercase tracking-wider text-gray-500 border-b border-white/5">
                  <th className="text-left py-2 font-normal">Expiry</th>
                  <th className="text-right py-2 font-normal">DTE</th>
                  <th className="text-right py-2 font-normal">ATM IV</th>
                  <th className="text-right py-2 font-normal">Straddle</th>
                  <th className="text-right py-2 font-normal">EM %</th>
                  <th className="text-right py-2 font-normal">IV EM %</th>
                </tr>
              </thead>
              <tbody>
                {data.straddle_features.map((s) => (
                  <tr key={s.expiration} className="border-b border-white/5 last:border-0">
                    <td className="py-2.5 text-gray-200">{formatDate(s.expiration)}</td>
                    <td className="py-2.5 text-right text-gray-400">{s.dte}d</td>
                    <td className="py-2.5 text-right text-gray-300">{formatPct(s.atm_iv)}</td>
                    <td className="py-2.5 text-right text-gray-300">{formatDollar(s.straddle_mid)}</td>
                    <td className="py-2.5 text-right font-semibold text-emerald-400">
                      {formatPct(s.em_straddle_pct)}
                    </td>
                    <td className="py-2.5 text-right text-blue-400">{formatPct(s.em_iv_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Greeks + microstructure */}
      {em && (em.skew_atm !== undefined || em.total_vega !== undefined) && (
        <div className="rounded-2xl bg-gray-900/60 border border-white/10 p-6 mb-6">
          <h2 className="text-sm font-semibold text-gray-300 mb-4">Greeks &amp; microstructure</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Stat
              label="ATM Skew"
              value={em.skew_atm !== null && em.skew_atm !== undefined ? `${(em.skew_atm * 100).toFixed(2)}vol` : '—'}
            />
            <Stat
              label="Term slope"
              value={
                em.term_slope !== null && em.term_slope !== undefined
                  ? `${(em.term_slope * 100).toFixed(2)}vol/30d`
                  : '—'
              }
            />
            <Stat
              label="Total vega"
              value={em.total_vega ? `$${(em.total_vega * 1000).toFixed(0)}` : '—'}
            />
            <Stat label="DTE" value={`${em.dte}d`} />
          </div>
        </div>
      )}

      {/* Earnings history */}
      {data.earnings_history && data.earnings_history.length > 0 && (
        <div className="rounded-2xl bg-gray-900/60 border border-white/10 p-6 mb-6">
          <h2 className="text-sm font-semibold text-gray-300 mb-4">Recent earnings</h2>
          <div className="flex flex-wrap gap-2">
            {data.earnings_history.slice(0, 8).map((h) => (
              <span
                key={h.date}
                className="text-xs px-2.5 py-1 rounded-md bg-white/5 border border-white/10 text-gray-300"
              >
                {formatDate(h.date)}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-black/30 border border-white/5 p-3">
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className="text-base font-semibold text-gray-100 mt-0.5">{value}</div>
    </div>
  );
}
