import { defineConfig, devices } from '@playwright/test';

// Minimal config used to run the public-page screener spec without
// dragging in Clerk's globalSetup (which requires .env.test.local).
// Re-uses the dev server already running on localhost:3000.
export default defineConfig({
  testDir: './e2e',
  testMatch: 'screener.spec.ts',
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  reporter: 'list',
  workers: 1,
  retries: 0,
});
