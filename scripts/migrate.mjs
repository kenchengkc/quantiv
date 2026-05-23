#!/usr/bin/env node
/**
 * One-shot schema migrations for the Neon Postgres database that backs the
 * watchlist API. Run from CI before each deploy:
 *
 *   node scripts/migrate.mjs
 *
 * Expects DATABASE_URL in env. Idempotent — re-running is a no-op if every
 * statement already succeeded once. Add new migrations by appending to the
 * MIGRATIONS array; statements should be CREATE … IF NOT EXISTS or
 * ALTER … IF NOT EXISTS so they're safe to re-run.
 *
 * Why this is not inline in route handlers:
 *   - First request on every cold serverless instance no longer pays the
 *     CREATE TABLE round-trip latency.
 *   - The production app role only needs DML (SELECT/INSERT/UPDATE/DELETE),
 *     not DDL — CI uses a higher-privilege role just for this script.
 *   - Migration failures fail the deploy, not user requests.
 */

import { neon } from '@neondatabase/serverless';

const MIGRATIONS = [
  `
  CREATE TABLE IF NOT EXISTS watchlist (
    user_id    TEXT        NOT NULL,
    symbol     TEXT        NOT NULL,
    position   INTEGER     NOT NULL DEFAULT 0,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, symbol)
  )
  `,
  `
  CREATE INDEX IF NOT EXISTS watchlist_user_pos_idx
    ON watchlist (user_id, position)
  `,
];

async function main() {
  const url = process.env.DATABASE_URL;
  if (!url) {
    console.error('DATABASE_URL is not set');
    process.exit(1);
  }
  const sql = neon(url);
  for (let i = 0; i < MIGRATIONS.length; i++) {
    const statement = MIGRATIONS[i].trim();
    process.stdout.write(`migrate[${i + 1}/${MIGRATIONS.length}] … `);
    try {
      // Tagged-template helper expects template literal, but accepts
      // ([sql])-shaped invocation as well for prebuilt strings.
      await sql([statement]);
      console.log('ok');
    } catch (err) {
      console.error('failed');
      console.error(err);
      process.exit(1);
    }
  }
  console.log(`migrate: ${MIGRATIONS.length} statement(s) ok`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
