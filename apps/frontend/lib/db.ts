import { neon } from '@neondatabase/serverless';

const connectionString = process.env.DATABASE_URL;
if (!connectionString) {
  // Fail loudly in prod; in dev surfaces as a clear 500 the first time a
  // watchlist API is hit.
  console.warn('[db] DATABASE_URL is not set — watchlist APIs will 500.');
}

export const sql = neon(connectionString ?? 'postgres://invalid');

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
      // Reset so the next request retries instead of caching a broken state.
      schemaReady = null;
      throw err;
    });
  }
  return schemaReady;
}
