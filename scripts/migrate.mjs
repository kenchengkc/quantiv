#!/usr/bin/env node
/**
 * Apply watchlist schema changes to Neon.
 *
 *   node scripts/migrate.mjs
 *
 * Needs DATABASE_URL. Safe to re-run. Append to MIGRATIONS using
 * CREATE/ALTER … IF NOT EXISTS. Use a role that can change schema;
 * do not run this from request handlers or CI.
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
