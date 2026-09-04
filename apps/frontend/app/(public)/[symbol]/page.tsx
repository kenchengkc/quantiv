import type { Metadata } from 'next';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { notFound } from 'next/navigation';
import SymbolResearchExport from '@/components/SymbolResearchExport';
import { companyName, stripLegalSuffix } from '@/lib/companyNames';
import tickerNames from '../../../public/ticker-names.json';
import SymbolPageClient from './SymbolPageClient';

type SymbolPageProps = {
  params: Promise<{ symbol: string }>;
};

type ComparablePayload = {
  expected_move?: {
    straddle_pct?: number | null;
    timing?: string | null;
  } | null;
  next_earnings_timing?: string | null;
};

const EXTENDED_NAMES = tickerNames as Record<string, string>;
const SYMBOL_RE = /^[A-Z][A-Z0-9.-]{0,9}$/;

function normalizeSymbol(raw: string | undefined): string {
  return decodeURIComponent(raw ?? '').trim().toUpperCase();
}

function hasSymbolPayload(symbol: string): boolean {
  return symbolPayloadPath(symbol) !== null;
}

function symbolPayloadPath(symbol: string): string | null {
  const candidates = [
    join(process.cwd(), 'apps', 'frontend', 'public', 'symbols', `${symbol}.json`),
    join(process.cwd(), 'public', 'symbols', `${symbol}.json`),
  ];
  return candidates.find((path) => existsSync(path)) ?? null;
}

function readSymbolPayload(symbol: string): unknown | null {
  const path = symbolPayloadPath(symbol);
  if (!path) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as unknown;
  } catch {
    return null;
  }
}

function readForecastEvidence(): unknown | null {
  const candidates = [
    join(process.cwd(), 'apps', 'frontend', 'public', 'evidence', 'forecast.json'),
    join(process.cwd(), 'public', 'evidence', 'forecast.json'),
  ];
  const path = candidates.find((candidate) => existsSync(candidate));
  if (!path) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as unknown;
  } catch {
    return null;
  }
}

function isKnownSymbol(symbol: string): boolean {
  if (!SYMBOL_RE.test(symbol)) return false;
  return Boolean(EXTENDED_NAMES[symbol]) || companyName(symbol) !== symbol || hasSymbolPayload(symbol);
}

function titleNameFor(symbol: string): string {
  const localName = companyName(symbol);
  const extendedName = EXTENDED_NAMES[symbol];

  if (localName !== symbol) {
    if (extendedName && localName === localName.toUpperCase() && localName.length > 4) {
      return stripLegalSuffix(extendedName);
    }
    return localName;
  }

  if (extendedName) return stripLegalSuffix(extendedName);

  return symbol;
}

function descriptionFor(symbol: string, name: string): string {
  const label = name === symbol ? symbol : `${name} (${symbol})`;

  if (!hasSymbolPayload(symbol)) {
    return `Check quote status and options-data coverage for ${label} on Quantiv.`;
  }

  return `Track ${label} earnings timing, expected move, options-implied range, volatility context, and historical earnings moves.`;
}

function comparableResearchHref(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;
  const research = payload as ComparablePayload;
  const implied = research.expected_move?.straddle_pct;
  if (typeof implied !== 'number' || !Number.isFinite(implied) || implied <= 0) return null;

  const timing = research.expected_move?.timing ?? research.next_earnings_timing ?? '';
  const normalizedTiming = timing.toLowerCase();
  const params = new URLSearchParams({
    minImplied: String(Number((implied * 0.75).toFixed(6))),
    maxImplied: String(Number((implied * 1.25).toFixed(6))),
    sort: 'ratio',
    dir: 'desc',
    limit: '100',
  });
  if (normalizedTiming.includes('before') || normalizedTiming === 'bmo') {
    params.set('timing', 'bmo');
  } else if (normalizedTiming.includes('after') || normalizedTiming === 'amc') {
    params.set('timing', 'amc');
  }
  return `/research?${params.toString()}`;
}

export async function generateMetadata({ params }: SymbolPageProps): Promise<Metadata> {
  const { symbol: rawSymbol } = await params;
  const symbol = normalizeSymbol(rawSymbol);
  if (!isKnownSymbol(symbol)) notFound();

  const name = titleNameFor(symbol);
  const description = descriptionFor(symbol, name);

  return {
    title: name === symbol ? symbol : `${name} (${symbol})`,
    description,
    openGraph: {
      title: name === symbol ? symbol : `${name} (${symbol})`,
      description,
    },
  };
}

export default async function SymbolPage({ params }: SymbolPageProps) {
  const { symbol: rawSymbol } = await params;
  const symbol = normalizeSymbol(rawSymbol);
  if (!isKnownSymbol(symbol)) notFound();
  const initialData = readSymbolPayload(symbol);

  return (
    <>
      <SymbolResearchExport
        symbol={symbol}
        comparableHref={comparableResearchHref(initialData)}
      />
      <SymbolPageClient
        initialSymbol={symbol}
        initialData={initialData}
        initialEvidence={readForecastEvidence()}
      />
    </>
  );
}
