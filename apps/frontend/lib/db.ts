import { neon, type NeonQueryFunction } from '@neondatabase/serverless';

// Connect on first query so `next build` can run without DATABASE_URL.
// Schema changes: node scripts/maintenance/migrate.mjs (not here, not in CI).
let _sql: NeonQueryFunction<false, false> | null = null;

function getSql(): NeonQueryFunction<false, false> {
  if (_sql) return _sql;
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error('DATABASE_URL is not set');
  }
  _sql = neon(connectionString);
  return _sql;
}

// Call sites write sql`SELECT ...` as usual.
export const sql: NeonQueryFunction<false, false> = ((
  strings: TemplateStringsArray,
  ...values: unknown[]
) => {
  return getSql()(strings, ...values);
}) as NeonQueryFunction<false, false>;
