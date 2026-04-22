import { neon, type NeonQueryFunction } from '@neondatabase/serverless';

// Lazy-init so `next build`'s page-data collection doesn't blow up on a
// missing DATABASE_URL. The handlers that use this are all dynamic, so this
// only runs at request time on a real deploy.
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

let schemaReady: Promise<void> | null = null;

export function ensureSchema(): Promise<void> {
  if (!schemaReady) {
    schemaReady = (async () => {
      await sql`
        CREATE TABLE IF NOT EXISTS watchlist (
          user_id    TEXT        NOT NULL,
          symbol     TEXT        NOT NULL,
          position   INTEGER     NOT NULL DEFAULT 0,
          added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, symbol)
        )
      `;
      await sql`
        CREATE INDEX IF NOT EXISTS watchlist_user_pos_idx
        ON watchlist (user_id, position)
      `;
    })().catch((err) => {
      schemaReady = null;
      throw err;
    });
  }
  return schemaReady;
}
