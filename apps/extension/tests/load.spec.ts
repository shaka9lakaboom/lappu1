/**
 * Loads dist/ as an unpacked MV3 extension in Chromium (the same mechanism as
 * chrome://extensions > Load unpacked) and checks the service worker and popup.
 * Run `npm run build` first.
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium, expect, test as base, type BrowserContext } from '@playwright/test';

const dist = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist');
const manifest = existsSync(join(dist, 'manifest.json'))
  ? (JSON.parse(readFileSync(join(dist, 'manifest.json'), 'utf8')) as { version: string })
  : undefined;

const test = base.extend<{ context: BrowserContext; extensionId: string }>({
  // eslint-disable-next-line no-empty-pattern
  context: async ({}, use) => {
    const context = await chromium.launchPersistentContext('', {
      channel: 'chromium',
      args: [`--disable-extensions-except=${dist}`, `--load-extension=${dist}`],
    });
    await use(context);
    await context.close();
  },
  extensionId: async ({ context }, use) => {
    const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
    await use(new URL(worker.url()).host);
  },
});

test.beforeAll(() => {
  if (!manifest) throw new Error('dist/manifest.json missing: run `npm run build` in apps/extension first');
});

// Derived from manifest.key; this is the origin to allow in the backend's CORS_ORIGINS.
const DEV_EXTENSION_ID = 'cohpimnabjigooghbigblennedbplojm';

test('service worker starts from the unpacked build with the stable dev id', async ({ context, extensionId }) => {
  expect(extensionId).toBe(DEV_EXTENSION_ID);
  const worker = context.serviceWorkers().find((w) => w.url().includes(extensionId));
  expect(worker?.url()).toBe(`chrome-extension://${extensionId}/background/service-worker.js`);
});

test('popup renders and reaches the service worker', async ({ context, extensionId }) => {
  const errors: string[] = [];
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => message.type() === 'error' && errors.push(message.text()));

  await page.goto(`chrome-extension://${extensionId}/popup/popup.html`);

  await expect(page.getByRole('heading', { name: 'SkillMirror Companion' })).toBeVisible();
  await expect(page.getByTestId('version')).toHaveText(manifest!.version);
  await expect(page.getByTestId('worker-status')).toHaveText('Running');
  // CI builds without configuration; a local build may be configured but signed out.
  await expect(page.getByTestId('tracking-status')).toHaveText(/^(Not configured|Off \(signed out\))$/);
  // The popup opened as a tab is not a ChatGPT page.
  await expect(page.getByTestId('provider-status')).toHaveText('Unsupported page');
  await expect(page.getByTestId('unsupported-note')).toBeVisible();
  expect(errors).toEqual([]);
});
