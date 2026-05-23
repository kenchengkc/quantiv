import { clerkSetup } from '@clerk/testing/playwright';

// Runs once before any spec. Reads NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and
// CLERK_SECRET_KEY from the environment (loaded by playwright.config.ts
// from .env.test.local) and exchanges them for a Clerk Testing Token,
// which is then attached to every signIn() call automatically.
//
// CRITICAL: point these env vars at a Clerk *development* instance, not
// production. Test sign-ins create real Clerk session records.
async function globalSetup() {
  if (!process.env.CLERK_SECRET_KEY || !process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) {
    console.warn(
      '[e2e] CLERK_SECRET_KEY and/or NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY not set — ' +
        'skipping clerkSetup(). Public specs (screener, fonts) still run; authenticated ' +
        'specs (watchlist) skip individually. For local auth specs, copy ' +
        '.env.test.local.example → .env.test.local and fill in dev-instance keys.',
    );
    return;
  }
  await clerkSetup();
}

export default globalSetup;
