import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import dotenv from 'dotenv';

dotenv.config({ path: path.resolve(__dirname, '.env.test.local'), override: false, quiet: true });
dotenv.config({ path: path.resolve(__dirname, '.env.local'), override: false, quiet: true });
dotenv.config({
  path: path.resolve(__dirname, '..', '..', 'config', '.env.local'),
  override: false,
  quiet: true,
});

const PORT = Number(process.env.E2E_PORT ?? 3000);
const BASE_URL = process.env.E2E_BASE_URL ?? `http://localhost:${PORT}`;

/** Public earnings-calendar checks — no Clerk globalSetup required. */
export default defineConfig({
  testDir: './e2e',
  testMatch: 'earnings-reaction.spec.ts',
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'on',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  reporter: 'list',
  workers: 1,
  retries: 0,
  outputDir: 'test-results/earnings-reaction',
  webServer: {
    command: 'npm run dev',
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY:
        process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? '',
      CLERK_SECRET_KEY: process.env.CLERK_SECRET_KEY ?? '',
    },
  },
});
