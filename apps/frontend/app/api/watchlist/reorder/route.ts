import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { sql, ensureSchema } from '@/lib/db';

export const dynamic = 'force-dynamic';

const SYMBOL_RE = /^[A-Z][A-Z.\-]{0,9}$/;

// Full-list replace: client sends the new ordering; we upsert positions in
// one round-trip using a VALUES table. Dropped symbols are deleted so the
// server list always matches what the client just drew.
export async function PUT(req: NextRequest) {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }
  const body = (await req.json().catch(() => null)) as { symbols?: unknown } | null;
  const raw = Array.isArray(body?.symbols) ? (body!.symbols as unknown[]) : null;
  if (!raw) {
    return NextResponse.json({ error: 'symbols[] required' }, { status: 400 });
  }
  const symbols = Array.from(
    new Set(
      raw
        .filter((s): s is string => typeof s === 'string')
        .map((s) => s.trim().toUpperCase())
        .filter((s) => SYMBOL_RE.test(s)),
    ),
  ).slice(0, 500);

  await ensureSchema();

  if (symbols.length === 0) {
    await sql`DELETE FROM watchlist WHERE user_id = ${userId}`;
    return NextResponse.json({ symbols: [] });
  }

  // Update positions for the provided ordering and drop anything not in it.
  // Using unnest arrays keeps this to two statements regardless of list size.
  const positions = symbols.map((_, i) => i);
  await sql`
    INSERT INTO watchlist (user_id, symbol, position)
    SELECT ${userId}, s, p
    FROM unnest(${symbols}::text[], ${positions}::int[]) AS t(s, p)
    ON CONFLICT (user_id, symbol)
    DO UPDATE SET position = EXCLUDED.position
  `;
  await sql`
    DELETE FROM watchlist
    WHERE user_id = ${userId}
      AND symbol <> ALL(${symbols}::text[])
  `;
  return NextResponse.json({ symbols });
}
