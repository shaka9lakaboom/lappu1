import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  // Browser tests only; tests/unit/*.test.ts run under vitest.
  testMatch: '*.spec.ts',
  reporter: [['list']],
  timeout: 30_000,
  workers: 1,
});
