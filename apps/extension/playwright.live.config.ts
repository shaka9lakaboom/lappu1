import { defineConfig } from '@playwright/test';

// Live P1 acceptance gate (real ChatGPT + hosted Supabase). Never run in CI.
export default defineConfig({
  testDir: './tests/live',
  testMatch: '*.acceptance.ts',
  reporter: [['list']],
  workers: 1,
  retries: 0,
});
