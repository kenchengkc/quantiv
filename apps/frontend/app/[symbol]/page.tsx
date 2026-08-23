import type { Metadata } from 'next';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { notFound } from 'next/navigation';
import { companyName, stripLegalSuffix } from '@/lib/companyNames';
import tickerNames from '../../public/ticker-names.json';
import SymbolPageClient from './SymbolPageClient';

type SymbolPageProps = {
  params: Promise<{ symbol: string }>;
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

  return (
    <SymbolPageClient
      initialSymbol={symbol}
      initialData={readSymbolPayload(symbol)}
    />
  );
}
