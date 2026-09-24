/**
 * P1 live acceptance gate (not part of CI): real ChatGPT, the real unpacked
 * extension, the real backend, the hosted Supabase project.
 *
 *   npm run acceptance:live --workspace @skillmirror/extension
 *
 * Preconditions: `npm run build` in apps/extension (configured from
 * apps/web/.env.local), services/backend/.env with SUPABASE_URL, DATABASE_URL
 * and CORS_ORIGINS including chrome-extension://cohpimnabjigooghbigblennedbplojm,
 * nothing else listening on :8000, and a linked Supabase CLI (for the
 * service-level row count). A Chromium window opens on chatgpt.com.
 *
 * Flow: create a learner -> sign in to the Companion -> ask ChatGPT a real
 * question with the backend offline -> both messages queued -> read the exact
 * queued envelopes -> start the backend -> Sync now -> queue drained -> rows
 * in hosted Postgres exactly once with provenance -> Activity page -> resend
 * the exact same envelopes twice -> row count unchanged.
 * Evidence is written to $ACCEPTANCE_OUT (default: test-results/acceptance).
 */
import { execFileSync, execSync, spawn, type ChildProcess } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import nextEnv from '@next/env';
import { chromium, expect, test, type BrowserContext } from '@playwright/test';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..', '..', '..', '..');
const dist = join(repo, 'apps', 'extension', 'dist');
const backendDir = join(repo, 'services', 'backend');
const out = process.env.ACCEPTANCE_OUT ?? join(repo, 'apps', 'extension', 'test-results', 'acceptance');
const EXTENSION_ID = 'cohpimnabjigooghbigblennedbplojm';
const API = 'http://localhost:8000';
const WEB = 'http://localhost:3000';
const PROMPT = 'Explain binary search in one sentence.';

nextEnv.loadEnvConfig(join(repo, 'apps', 'web'));
const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

const evidence: Record<string, unknown> = { started_at: new Date().toISOString() };
const record = (key: string, value: unknown) => {
  evidence[key] = value;
  writeFileSync(join(out, 'evidence.json'), JSON.stringify(evidence, null, 2));
};

async function waitFor(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`${url} did not come up`);
}

async function isUp(url: string): Promise<boolean> {
  try {
    return (await fetch(url)).ok;
  } catch {
    return false;
  }
}

function startBackend(): ChildProcess {
  const python = join(backendDir, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  return spawn(python, ['-m', 'uvicorn', 'app.main:app', '--port', '8000'], { cwd: backendDir, stdio: 'inherit' });
}

function serviceCount(learnerId: string): { raw_messages: number; conversations: number; processing_jobs: number } {
  // Service-level count (bypasses RLS) through the linked Supabase CLI.
  const sql =
    `select (select count(*) from public.raw_messages where learner_id = '${learnerId}') as raw_messages, ` +
    `(select count(*) from public.conversations where learner_id = '${learnerId}') as conversations, ` +
    `(select count(*) from public.processing_jobs where learner_id = '${learnerId}') as processing_jobs`;
  // One quoted argument: the SQL has single quotes only, and a shell is needed for npx on Windows.
  const text = execSync(`npx supabase@2.117.0 db query --linked "${sql}"`, { cwd: repo, encoding: 'utf8' });
  const [row] = JSON.parse(text.slice(text.indexOf('{'))).rows as Array<Record<string, number>>;
  return { raw_messages: Number(row.raw_messages), conversations: Number(row.conversations), processing_jobs: Number(row.processing_jobs) };
}

async function rest<T>(path: string, token: string): Promise<T> {
  const response = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, { headers: { apikey: ANON_KEY, Authorization: `Bearer ${token}` } });
  expect(response.ok, `GET ${path}`).toBe(true);
  return (await response.json()) as T;
}

test.setTimeout(15 * 60_000);

test('real ChatGPT turn is captured, stored once in hosted Supabase and survives a duplicate resend', async () => {
  expect(SUPABASE_URL && ANON_KEY, 'apps/web/.env.local must define the hosted Supabase URL and anon key').toBeTruthy();
  mkdirSync(out, { recursive: true });
  expect(await isUp(`${API}/health`), 'stop anything on :8000 first; the test controls the backend').toBe(false);

  // 1. A fresh SkillMirror learner on the hosted project (real Supabase Auth signup).
  const email = `skillmirror-p1-${Date.now()}@mailinator.com`;
  const password = randomBytes(18).toString('base64url');
  const signup = await fetch(`${SUPABASE_URL}/auth/v1/signup`, {
    method: 'POST',
    headers: { apikey: ANON_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, data: { display_name: 'P1 acceptance' } }),
  });
  const session = (await signup.json()) as { access_token?: string; user?: { id: string } };
  expect(session.access_token, 'signup must return a session (auto-confirm)').toBeTruthy();
  const learnerId = session.user!.id;
  const token = session.access_token!;
  record('learner', { id: learnerId, email });

  let backend: ChildProcess | null = null;
  let web: ChildProcess | null = null;
  const context: BrowserContext = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), 'sm-live-')), {
    headless: false,
    channel: 'chromium',
    viewport: { width: 1280, height: 900 },
    args: [`--disable-extensions-except=${dist}`, `--load-extension=${dist}`],
  });
  try {
    if (!context.serviceWorkers().length) await context.waitForEvent('serviceworker');
    const worker = context.serviceWorkers()[0];
    expect(new URL(worker.url()).host).toBe(EXTENSION_ID);

    // 2. Sign in to the Companion popup.
    const popup = await context.newPage();
    await popup.goto(`chrome-extension://${EXTENSION_ID}/popup/popup.html`);
    await popup.getByPlaceholder('SkillMirror email').fill(email);
    await popup.getByPlaceholder('Password').fill(password);
    await popup.getByRole('button', { name: 'Sign in to SkillMirror' }).click();
    await expect(popup.getByTestId('identity')).toHaveText(email, { timeout: 20_000 });
    await expect(popup.getByTestId('tracking-status')).toHaveText('On');

    // 3. A real ChatGPT turn while the backend is offline.
    const chat = await context.newPage();
    await chat.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });
    const composer = chat.locator('#prompt-textarea, textarea:visible, [contenteditable="true"]:visible').first();
    await composer.waitFor({ timeout: 60_000 });
    await composer.click();
    await chat.keyboard.type(PROMPT);
    await chat.keyboard.press('Enter');
    await expect(popup.getByTestId('queued-count')).toHaveText('2', { timeout: 120_000 });
    await chat.screenshot({ path: join(out, 'chatgpt.png') });
    const conversationId = /\/(?:c|uc)\/([A-Za-z0-9-]+)/.exec(new URL(chat.url()).pathname)?.[1] ?? null;
    record('chatgpt', { url_path: new URL(chat.url()).pathname, conversation_id: conversationId });

    // 4. The exact queued envelopes, read from the extension's IndexedDB.
    const queued = (await worker.evaluate(
      () =>
        new Promise<unknown[]>((resolve, reject) => {
          const open = indexedDB.open('skillmirror-companion');
          open.onerror = () => reject(open.error);
          open.onsuccess = () => {
            const request = open.result.transaction('queue').objectStore('queue').getAll();
            request.onsuccess = () => resolve(request.result.map((item: { envelope: unknown }) => item.envelope));
            request.onerror = () => reject(request.error);
          };
        }),
    )) as unknown as Array<Record<string, unknown>>;
    expect(queued.map((e) => e.role).sort()).toEqual(['assistant', 'user']);
    const user = queued.find((e) => e.role === 'user')!;
    const assistant = queued.find((e) => e.role === 'assistant')!;
    expect(user.content_text).toBe(PROMPT);
    expect(String(assistant.content_text).length).toBeGreaterThan(20);
    expect(queued.every((e) => e.learner_id === learnerId && e.external_conversation_id === conversationId)).toBe(true);
    writeFileSync(join(out, 'queued-envelopes.json'), JSON.stringify(queued, null, 2));
    expect(serviceCount(learnerId).raw_messages).toBe(0);

    // 5. Backend comes up; the queue is delivered and drained.
    backend = startBackend();
    await waitFor(`${API}/health`, 60_000);
    await popup.getByRole('button', { name: 'Sync now' }).click();
    await expect(popup.getByTestId('queued-count')).toHaveText('0', { timeout: 60_000 });
    await expect(popup.getByTestId('last-sync')).not.toHaveText('Never');
    await popup.screenshot({ path: join(out, 'popup.png') });

    // 6. Stored exactly once, with provenance, visible to the learner through RLS.
    const afterSync = serviceCount(learnerId);
    expect(afterSync).toEqual({ raw_messages: 2, conversations: 1, processing_jobs: 2 });
    const rows = await rest<Array<Record<string, unknown>>>(
      `raw_messages?select=id,learner_id,source_provider,source_method,external_message_id,external_parent_message_id,message_index,role,content_text,revision_index,captured_at,received_at,context_incomplete,client_event_uuid,client_event_id,capture_metadata,conversations(external_id)&order=message_index`,
      token,
    );
    const jobs = await rest<Array<Record<string, unknown>>>('processing_jobs?select=job_type,entity_id,state,attempts', token);
    expect(rows.map((r) => r.role)).toEqual(['user', 'assistant']);
    expect(rows[0].content_text).toBe(PROMPT);
    expect(rows.every((r) => r.learner_id === learnerId && r.source_provider === 'chatgpt')).toBe(true);
    expect(new Set(rows.map((r) => r.client_event_uuid))).toEqual(new Set(queued.map((e) => e.event_id)));
    expect(jobs.map((j) => j.state)).toEqual(['PENDING', 'PENDING']);
    record('hosted_after_sync', { counts: afterSync, rows, jobs });

    // 7. Activity page (web app against the hosted project).
    if (!(await isUp(`${WEB}/sign-in`))) {
      web = spawn('npm', ['run', 'dev', '--workspace', '@skillmirror/web'], { cwd: repo, stdio: 'inherit', shell: process.platform === 'win32' });
      await waitFor(`${WEB}/sign-in`, 180_000);
    }
    const site = await context.newPage();
    await site.goto(`${WEB}/sign-in?next=/activity`);
    await site.getByLabel('Email').fill(email);
    await site.getByLabel('Password').fill(password);
    await site.getByRole('button', { name: 'Sign in', exact: true }).click();
    await site.waitForURL(/\/activity$/, { timeout: 60_000 });
    await expect(site.getByTestId('activity-row')).toHaveCount(2);
    await expect(site.getByTestId('activity-count')).toHaveText('2');
    const roles = await site.getByTestId('activity-role').allInnerTexts();
    expect(roles.sort()).toEqual(['assistant', 'user']);
    await expect(site.getByTestId('activity-status').first()).toContainText('Waiting for processing');
    await site.screenshot({ path: join(out, 'activity.png'), fullPage: true });

    // 8. Resend the exact same events (twice): nothing new is stored.
    const resends = [];
    for (let i = 0; i < 2; i++) {
      const response = await fetch(`${API}/v1/events/batch`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', 'Idempotency-Key': randomUUID() },
        body: JSON.stringify({ client: { extension_version: '0.2.0', adapter_version: 'chatgpt-1' }, events: queued }),
      });
      expect(response.status).toBe(200);
      const body = (await response.json()) as { accepted: number; duplicates: number };
      expect(body).toMatchObject({ accepted: 0, duplicates: 2 });
      resends.push(body);
    }
    // And a spoofed learner id is refused.
    const spoof = await fetch(`${API}/v1/events/batch`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ client: { extension_version: '0.2.0', adapter_version: 'chatgpt-1' }, events: [{ ...user, learner_id: randomUUID() }] }),
    });
    expect(spoof.status).toBe(403);
    const afterResend = serviceCount(learnerId);
    expect(afterResend).toEqual(afterSync);
    record('duplicate_resend', { responses: resends, spoof_status: spoof.status, counts_after: afterResend });
    record('finished_at', new Date().toISOString());
  } finally {
    await context.close().catch(() => undefined);
    backend?.kill();
    // `npm run dev` runs through a shell on Windows; stop the whole tree.
    if (web?.pid && process.platform === 'win32') execFileSync('taskkill', ['/pid', String(web.pid), '/T', '/F']);
    else web?.kill();
  }
});

