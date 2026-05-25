import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// Pull E2E env from `.env.test.local` first (gitignored — holds the Clerk
// dev-instance Secret Key + a test user email), then fall back to
// `.env.local` for any other Next.js runtime vars the dev server needs.
import dotenv from 'dotenv';
dotenv.config({ path: path.resolve(__dirname, '.env.test.local'), override: false });
dotenv.config({ path: path.resolve(__dirname, '.env.local'), override: false });
dotenv.config({ path: path.resolve(__dirname, '..', '..', 'config', '.env.local'), override: false });

const CLERK_PUBLISHABLE_KEY =
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ??
  process.env.E2E_CLERK_PUBLISHABLE_KEY ??
  '';
const CLERK_SECRET =
  process.env.CLERK_SECRET_KEY ??
  process.env.E2E_CLERK_SECRET_KEY ??
  '';

process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = CLERK_PUBLISHABLE_KEY;
process.env.CLERK_SECRET_KEY = CLERK_SECRET;

const PORT = Number(process.env.E2E_PORT ?? 3000);
const BASE_URL = process.env.E2E_BASE_URL ?? `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  // font-compare.spec.ts and font-loaded.spec.ts are developer-only
  // visual diffs against a static design mockup hosted on
  // localhost:8088. They are NOT runnable in CI — the design server
  // doesn't exist there. Use `playwright.font.config.ts` to run them
  // explicitly when you have the mockup served locally.
  testIgnore: ['font-compare.spec.ts', 'font-loaded.spec.ts'],
  // clerkSetup() lives here — runs once before any test, exchanges the dev
  // publishable + secret keys for a Clerk Testing Token that bypasses bot
  // detection so signIn() can mint sessions.
  globalSetup: require.resolve('./e2e/global-setup'),
  fullyParallel: false,            // sign-in serializes against Clerk's rate limits
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // Always serial. Tests sign in through Clerk and share sessionStorage
  // keys (e.g. quantiv:prevRoute, quantiv:splash:played) that collide
  // when multiple workers race the same browser context. Local default
  // of `undefined` lets Playwright auto-scale to N workers and produced
  // 8/9 spec failures under parallel runs.
  workers: 1,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  // Boot the Next dev server unless one is already running.
  webServer: {
    command: 'npm run dev',
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    // Pass through the Clerk publishable key the app needs at runtime.
    env: {
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: CLERK_PUBLISHABLE_KEY,
      CLERK_SECRET_KEY: CLERK_SECRET,
    },
  },
});
