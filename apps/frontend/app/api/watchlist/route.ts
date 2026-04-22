import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { sql, ensureSchema } from '@/lib/db';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const SYMBOL_RE = /^[A-Z][A-Z.\-]{0,9}$/;

type Row = { symbol: string; position: number };

export async function GET() {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  await ensureSchema();
  const rows = (await sql`
    SELECT symbol, position
    FROM watchlist
    WHERE user_id = ${userId}
    ORDER BY position ASC, added_at ASC
  `) as Row[];
  return NextResponse.json({ symbols: rows.map((r) => r.symbol) });
}

export async function POST(req: NextRequest) {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const body = (await req.json().catch(() => null)) as { symbol?: string } | null;
  const symbol = body?.symbol?.trim().toUpperCase();
  if (!symbol || !SYMBOL_RE.test(symbol)) {
    return NextResponse.json({ error: 'invalid symbol' }, { status: 400 });
  }
  await ensureSchema();
  const [{ next_pos }] = (await sql`
    SELECT COALESCE(MAX(position), -1) + 1 AS next_pos
    FROM watchlist
    WHERE user_id = ${userId}
  `) as { next_pos: number }[];
  await sql`
    INSERT INTO watchlist (user_id, symbol, position)
    VALUES (${userId}, ${symbol}, ${next_pos})
    ON CONFLICT (user_id, symbol) DO NOTHING
  `;
  const rows = (await sql`
    SELECT symbol FROM watchlist
    WHERE user_id = ${userId}
    ORDER BY position ASC, added_at ASC
  `) as { symbol: string }[];
  return NextResponse.json({ symbols: rows.map((r) => r.symbol) });
}
