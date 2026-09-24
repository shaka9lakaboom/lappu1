import { existsSync } from 'node:fs';

import { loadEnvConfig } from '@next/env';
import { defineConfig, devices } from '@playwright/test';

// Public values from apps/web/.env.local, like the app itself.
loadEnvConfig(__dirname);
// Test-only secrets (SUPABASE_SERVICE_ROLE_KEY) live in a separate, gitignored
// file so they are never next to browser-visible configuration.
if (existsSync(`${__dirname}/.env.e2e.local`)) process.loadEnvFile(`${__dirname}/.env.e2e.local`);

const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:3000';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  timeout: 90_000,
  use: {
    baseURL,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        // CI sets E2E_WEB_COMMAND="npm run start" to test the production build.
        command: process.env.E2E_WEB_COMMAND ?? 'npm run dev',
        url: baseURL,
        reuseExistingServer: true,
        timeout: 120_000,
      },
});
