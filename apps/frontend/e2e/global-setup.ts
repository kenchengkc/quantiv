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
    throw new Error(
      'E2E setup is missing CLERK_SECRET_KEY and/or NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY. ' +
        'Copy .env.test.local.example to .env.test.local and fill in the dev-instance keys.',
    );
  }
  await clerkSetup();
}

export default globalSetup;
