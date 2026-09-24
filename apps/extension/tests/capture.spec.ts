/**
 * Real-browser capture pipeline, deterministic: the unpacked extension runs in
 * Chromium; chatgpt.com is served from the real DOM fixtures via request
 * routing; Supabase Auth and the SkillMirror API are a local mock that checks
 * the Bearer token and acknowledges like POST /v1/events/batch.
 *
 * Covers: popup sign-in, content-script capture, service-worker queueing,
 * backend offline -> queue kept -> recovery, browser restart with a queued
 * event, no resend after reload, Pause/Resume, supported-page detection.
 *
 * The live ChatGPT + hosted Supabase gate is separate (docs/project-state.md).
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium, expect, test, type BrowserContext, type Page } from '@playwright/test';

import { fixture } from './unit/helpers';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const distE2E = join(root, 'dist-e2e');
const EXTENSION_ID = 'cohpimnabjigooghbigblennedbplojm';
const ORIGIN = `chrome-extension://${EXTENSION_ID}`;
const PORT = 8791;
const MOCK = `http://127.0.0.1:${PORT}`;
const ACCESS_TOKEN = 'e2e-access-token';
const LEARNER = '00000000-0000-4000-8000-00000000e2e1';
const CONV = '6ab58b53-14f8-83ea-853a-57c0ab951696';

interface StoredEvent {
  event_id: string;
  client_event_id: string;
  learner_id: string;
  role: string;
  content_text: string;
  external_message_id: string | null;
}

const api = {
  online: true,
  attempts: 0,
  stored: new Map<string, StoredEvent>(),
  authHeaders: new Set<string>(),
};

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (chunk) => (data += chunk));
    req.on('end', () => resolve(data));
  });
}

function json(res: ServerResponse, status: number, body: unknown) {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': ORIGIN });
  res.end(JSON.stringify(body));
}

async function handle(req: IncomingMessage, res: ServerResponse) {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': ORIGIN,
      'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
      'Access-Control-Allow-Headers': 'authorization, content-type, idempotency-key, apikey',
    });
    return res.end();
  }
  const url = new URL(req.url ?? '/', MOCK);
  const body = await readBody(req);
  if (url.pathname === '/auth/v1/token') {
    const { password } = JSON.parse(body || '{}') as { password?: string };
    if (req.headers.apikey !== 'e2e-anon-key' || password !== 'correct-password') {
      return json(res, 400, { error_description: 'Invalid login credentials' });
    }
    return json(res, 200, {
      access_token: ACCESS_TOKEN,
      refresh_token: 'e2e-refresh',
      expires_in: 3600,
      user: { id: LEARNER, email: 'learner@e2e.test' },
    });
  }
  if (url.pathname === '/auth/v1/logout') return json(res, 204, {});
  if (url.pathname === '/v1/events/batch') {
    api.attempts++;
    api.authHeaders.add(String(req.headers.authorization));
    if (req.headers.authorization !== `Bearer ${ACCESS_TOKEN}`) return json(res, 401, { detail: 'invalid token' });
    if (!api.online) return json(res, 503, { detail: 'Storage is temporarily unavailable' });
    const { events } = JSON.parse(body) as { events: StoredEvent[] };
    const results = events.map((e) => {
      const duplicate = api.stored.has(e.client_event_id);
      if (!duplicate) api.stored.set(e.client_event_id, e);
      return {
        event_id: e.event_id,
        status: duplicate ? 'duplicate' : 'accepted',
        raw_message_id: '11111111-0000-4000-8000-000000000000',
        conversation_id: '22222222-0000-4000-8000-000000000000',
        revision_index: 0,
      };
    });
    const accepted = results.filter((r) => r.status === 'accepted').length;
    return json(res, 200, { correlation_id: 'e2e', accepted, duplicates: results.length - accepted, results });
  }
  json(res, 404, { detail: 'not found' });
}

let server: Server;
let userDataDir: string;

async function launch(): Promise<BrowserContext> {
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: 'chromium',
    args: [`--disable-extensions-except=${distE2E}`, `--load-extension=${distE2E}`],
  });
  // Serve chatgpt.com from the real DOM fixtures (the page's own scripts are not part of them).
  await context.route('https://chatgpt.com/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: fixture('lightweight-pending.html') }),
  );
  if (!context.serviceWorkers().length) await context.waitForEvent('serviceworker');
  return context;
}

async function popup(context: BrowserContext): Promise<Page> {
  const page = await context.newPage();
  await page.goto(`${ORIGIN}/popup/popup.html`);
  return page;
}

/** Replaces the transcript with another real fixture state, like ChatGPT re-rendering. */
async function renderState(page: Page, name: string, path?: string) {
  const html = fixture(name);
  const main = html.slice(html.indexOf('<main>') + 6, html.indexOf('</main>'));
  await page.evaluate(
    ([markup, nextPath]) => {
      if (nextPath) history.pushState(null, '', nextPath);
      document.querySelector('main')!.innerHTML = markup;
    },
    [main, path ?? null] as const,
  );
}

async function appendUserMessage(page: Page, id: string, text: string) {
  await page.evaluate(
    ([messageId, content]) => {
      const li = document.createElement('li');
      li.setAttribute('data-message-role', 'user');
      li.id = messageId;
      const p = document.createElement('p');
      p.setAttribute('data-user-message-copy', '');
      p.textContent = content;
      li.appendChild(p);
      document.querySelector('ol[data-conversation-transcript]')!.appendChild(li);
    },
    [id, text] as const,
  );
}

test.describe.configure({ mode: 'serial' });
test.setTimeout(300_000);

test.beforeAll(async () => {
  execFileSync(process.execPath, ['build.mjs'], {
    cwd: root,
    stdio: 'inherit',
    env: {
      ...process.env,
      SKILLMIRROR_OUT_DIR: 'dist-e2e',
      SKILLMIRROR_SUPABASE_URL: MOCK,
      SKILLMIRROR_SUPABASE_ANON_KEY: 'e2e-anon-key',
      SKILLMIRROR_API_URL: MOCK,
      SKILLMIRROR_WEB_URL: 'http://localhost:3000',
    },
  });
  execFileSync(process.execPath, ['scripts/validate-manifest.mjs'], { cwd: root, stdio: 'inherit', env: { ...process.env, SKILLMIRROR_OUT_DIR: 'dist-e2e' } });
  server = createServer((req, res) => void handle(req, res));
  await new Promise<void>((resolve) => server.listen(PORT, '127.0.0.1', resolve));
  userDataDir = mkdtempSync(join(tmpdir(), 'skillmirror-e2e-'));
});

test.afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
  rmSync(userDataDir, { recursive: true, force: true });
});

test('capture -> queue -> authenticated sync, surviving outage and restart', async () => {
  let context = await launch();
  let ui = await popup(context);

  // Signed out: nothing is captured.
  await expect(ui.getByTestId('tracking-status')).toHaveText('Off (signed out)');
  await ui.getByPlaceholder('SkillMirror email').fill('learner@e2e.test');
  await ui.getByPlaceholder('Password').fill('wrong-password');
  await ui.getByRole('button', { name: 'Sign in to SkillMirror' }).click();
  await expect(ui.getByRole('alert')).toHaveText('Invalid login credentials');
  await ui.getByPlaceholder('Password').fill('correct-password');
  await ui.getByRole('button', { name: 'Sign in to SkillMirror' }).click();
  await expect(ui.getByTestId('identity')).toHaveText('learner@e2e.test');
  await expect(ui.getByTestId('tracking-status')).toHaveText('On');

  // Backend down while the learner chats.
  api.online = false;
  const chat = await context.newPage();
  await chat.goto('https://chatgpt.com/');
  // The extension has no "tabs" permission, so probe every tab the way the popup
  // probes the active one: only the ChatGPT tab's content script answers.
  const pings = await ui.evaluate(async () => {
    const tabs = await chrome.tabs.query({});
    return Promise.all(tabs.map((t) => chrome.tabs.sendMessage(t.id!, { type: 'CONTENT_PING' }).catch(() => null)));
  });
  expect(pings.filter(Boolean)).toEqual([
    { type: 'CONTENT_STATUS', provider: 'chatgpt', state: 'tracking', conversationDetected: false },
  ]);

  await renderState(chat, 'lightweight-streaming.html', `/uc/${CONV}`);
  await chat.waitForTimeout(2500);
  await renderState(chat, 'lightweight-complete.html');
  await expect(ui.getByTestId('queued-count')).toHaveText('4', { timeout: 20_000 });
  expect(api.attempts).toBeGreaterThan(0);
  expect(api.stored.size).toBe(0);
  await expect(ui.getByTestId('sync-error')).toContainText('503');

  // Browser restart with the queue still full and the backend still down.
  await context.close();
  context = await launch();
  ui = await popup(context);
  await expect(ui.getByTestId('identity')).toHaveText('learner@e2e.test');
  await expect(ui.getByTestId('queued-count')).toHaveText('4');

  // Recovery: everything is delivered once, then removed from the queue.
  api.online = true;
  await ui.getByRole('button', { name: 'Sync now' }).click();
  await expect(ui.getByTestId('queued-count')).toHaveText('0', { timeout: 20_000 });
  await expect(ui.getByTestId('last-sync')).not.toHaveText('Never');
  expect([...api.stored.values()].map((e) => [e.role, e.external_message_id])).toEqual([
    ['user', '5f574d0d-14b5-4e87-a867-800b044bc4b3'],
    ['assistant', 'da2ec96d-c994-4975-b05f-0b515cf917c1'],
    ['user', '6cf347d5-a408-49da-a913-e0c4f2746f72'],
    ['assistant', '9ff93fc4-5c8f-4247-a67c-03a2e529d06c'],
  ]);
  expect([...api.stored.values()].every((e) => e.learner_id === LEARNER)).toBe(true);
  expect([...api.authHeaders]).toEqual([`Bearer ${ACCESS_TOKEN}`]);

  // Reopening the conversation re-renders history: nothing new is sent.
  const attemptsBefore = api.attempts;
  const chat2 = await context.newPage();
  await chat2.goto(`https://chatgpt.com/uc/${CONV}`);
  await renderState(chat2, 'lightweight-complete.html');
  await chat2.waitForTimeout(4000);
  await expect(ui.getByTestId('queued-count')).toHaveText('0');
  expect(api.attempts).toBe(attemptsBefore);

  // Pause: a new message is not captured, even after resuming.
  await ui.getByTestId('pause-toggle').click();
  await expect(ui.getByTestId('tracking-status')).toHaveText('Paused');
  await appendUserMessage(chat2, 'aaaaaaaa-1111-4000-8000-000000000001', 'Typed while paused');
  await chat2.waitForTimeout(4000);
  await ui.getByTestId('pause-toggle').click();
  await expect(ui.getByTestId('tracking-status')).toHaveText('On');
  await chat2.waitForTimeout(3000);
  expect([...api.stored.values()].some((e) => e.content_text === 'Typed while paused')).toBe(false);

  // Resumed: the next message is captured and delivered.
  await appendUserMessage(chat2, 'aaaaaaaa-1111-4000-8000-000000000002', 'Typed after resuming');
  await expect.poll(() => [...api.stored.values()].map((e) => e.content_text), { timeout: 20_000 }).toContain('Typed after resuming');
  expect(api.stored.size).toBe(5);

  await context.close();
});
