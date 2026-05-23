import { neon, type NeonQueryFunction } from '@neondatabase/serverless';

// Lazy-init so `next build`'s page-data collection doesn't blow up on a
// missing DATABASE_URL. The handlers that use this are all dynamic, so this
// only runs at request time on a real deploy.
//
// Schema migrations live in scripts/migrate.mjs and run as a one-time CI
// step before the deploy lands — NOT inside route handlers. Putting DDL
// in the request path adds first-cold-instance latency and requires
// production credentials to carry CREATE privileges.
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

// Tagged-template proxy: call sites write `sql\`SELECT ...\`` as usual.
export const sql: NeonQueryFunction<false, false> = ((
  strings: TemplateStringsArray,
  ...values: unknown[]
) => {
  return getSql()(strings, ...values);
}) as NeonQueryFunction<false, false>;
