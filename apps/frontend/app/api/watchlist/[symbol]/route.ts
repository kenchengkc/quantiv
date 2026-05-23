import { NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { sql, ensureSchema } from '@/lib/db';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const SYMBOL_RE = /^[A-Z][A-Z.\-]{0,9}$/;
const PRIVATE_NO_STORE = {
  'Cache-Control': 'private, no-store, max-age=0, must-revalidate',
};

function json(body: unknown, init?: ResponseInit) {
  return NextResponse.json(body, {
    ...init,
    headers: {
      ...PRIVATE_NO_STORE,
      ...(init?.headers ?? {}),
    },
  });
}

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { userId } = await auth();
  if (!userId) {
    return json({ error: 'unauthorized' }, { status: 401 });
  }
  const { symbol: rawSymbol } = await params;
  const symbol = rawSymbol.trim().toUpperCase();
  if (!SYMBOL_RE.test(symbol)) {
    return json({ error: 'invalid symbol' }, { status: 400 });
  }
  await ensureSchema();
  await sql`
    DELETE FROM watchlist
    WHERE user_id = ${userId} AND symbol = ${symbol}
  `;
  const rows = (await sql`
    SELECT symbol FROM watchlist
    WHERE user_id = ${userId}
    ORDER BY position ASC, added_at ASC
  `) as { symbol: string }[];
  return json({ symbols: rows.map((r) => r.symbol) });
}
