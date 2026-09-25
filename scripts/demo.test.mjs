// node --test scripts/demo.test.mjs  (npm run test:demo)
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

const before = { ...process.env };
const { DEMO_RUNTIME, demoBackendEnv, envFileKeys } = await import('./demo.mjs');

test('importing the demo script changes nothing in the current environment', () => {
  assert.deepEqual({ ...process.env }, before);
});

test('the demo runtime is the Flash-Lite free-tier routing and holds no secret', () => {
  assert.equal(DEMO_RUNTIME.GEMINI_GENERATION_MODEL, 'gemini-3.5-flash-lite');
  assert.equal(DEMO_RUNTIME.GEMINI_ROUTINE_MODEL, 'gemini-3.5-flash-lite');
  assert.equal(DEMO_RUNTIME.MODEL_QUOTA_RESERVE, '25');
  for (const name of Object.keys(DEMO_RUNTIME)) {
    assert.doesNotMatch(name, /KEY|SECRET|PASSWORD|TOKEN|DATABASE_URL|SUPABASE/, name);
  }
  assert.ok(Object.isFrozen(DEMO_RUNTIME));
});

test('demo overrides reach only the backend child environment and win over stale values', () => {
  const base = { GEMINI_GENERATION_MODEL: 'gemini-3.7-flash', PATH: '/bin' };
  const env = demoBackendEnv(base);
  assert.equal(env.GEMINI_GENERATION_MODEL, 'gemini-3.5-flash-lite');
  assert.equal(env.PATH, '/bin');
  assert.equal(base.GEMINI_GENERATION_MODEL, 'gemini-3.7-flash'); // not mutated
  assert.equal(process.env.GEMINI_ROUTINE_MODEL, before.GEMINI_ROUTINE_MODEL);
});

test('env files are reported by name only, never by value', () => {
  const dir = mkdtempSync(join(tmpdir(), 'skillmirror-demo-'));
  try {
    const path = join(dir, '.env');
    writeFileSync(path, 'GEMINI_API_KEY=AIzaSy-not-a-real-key\nDATABASE_URL=\n# comment\n');
    const keys = envFileKeys(path);
    assert.deepEqual(keys, { GEMINI_API_KEY: true, DATABASE_URL: false });
    assert.doesNotMatch(JSON.stringify(keys), /AIza/);
    assert.equal(envFileKeys(join(dir, 'missing.env')), null);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('the committed backend default stays the architecture model', () => {
  const config = readFileSync(new URL('../services/backend/app/core/config.py', import.meta.url), 'utf8');
  assert.match(config, /gemini_generation_model: str = Field\(default="gemini-3\.7-flash"/);
  assert.match(config, /gemini_routine_model: str \| None = Field\(default=None/);
});

// --- P9: preflight (npm run demo:check) and verify (npm run demo:verify) ----------------------

const { extensionStatus, preflight, validateDemoRuntime, verify } = await import('./demo.mjs');
const { createServer } = await import('node:net');
const { mkdirSync, utimesSync } = await import('node:fs');

const FAKE_KEY = 'AIzaSy' + 'X'.repeat(33); // shaped like a Gemini key; must never be printed
const OK_CONFIG = {
  ok: true,
  app_env: 'development',
  worker_will_start: true,
  generation_model: 'gemini-3.5-flash-lite',
  routine_model: 'gemini-3.5-flash-lite',
  routing: 'free-tier',
  daily_limits: { 'gemini-3.5-flash-lite': 500 },
  quota_reserve: 25,
  cors_allows_extension: true,
};

function tempDir() {
  return mkdtempSync(join(tmpdir(), 'skillmirror-demo-p9-'));
}

function builtExtension(dir, info = {}) {
  for (const f of ['background', 'content', 'popup']) mkdirSync(join(dir, f), { recursive: true });
  for (const f of ['manifest.json', 'background/service-worker.js', 'content/chatgpt.js', 'popup/popup.js', 'popup/popup.html']) {
    writeFileSync(join(dir, f), '{}');
  }
  writeFileSync(
    join(dir, 'build-info.json'),
    JSON.stringify({
      extensionVersion: '0.2.0',
      apiUrl: 'http://localhost:8000',
      webUrl: 'http://localhost:3000',
      supabaseUrl: 'https://project.supabase.co',
      anonKey: 'set',
      ...info,
    }),
  );
  return dir;
}

function envFiles(dir, { backend = true, web = true } = {}) {
  const backendEnv = join(dir, 'backend.env');
  const webEnv = join(dir, 'web.env');
  if (backend) writeFileSync(backendEnv, `SUPABASE_URL=https://project.supabase.co\nDATABASE_URL=postgresql://u:p@h/db\nGEMINI_API_KEY=${FAKE_KEY}\n`);
  if (web) writeFileSync(webEnv, 'NEXT_PUBLIC_SUPABASE_URL=https://project.supabase.co\nNEXT_PUBLIC_SUPABASE_ANON_KEY=anon\nNEXT_PUBLIC_API_URL=http://localhost:8000\n');
  return { backendEnv, webEnv };
}

async function runPreflight(dir, overrides = {}) {
  const out = [];
  const { backendEnv, webEnv } = envFiles(dir, overrides.files);
  const problems = await preflight(['backend', 'web', 'extension'], {
    backendEnvFile: overrides.backendEnvFile ?? backendEnv,
    webEnvFile: overrides.webEnvFile ?? webEnv,
    backendPort: overrides.backendPort ?? 59981,
    webPort: overrides.webPort ?? 59982,
    extension: { dist: overrides.dist ?? builtExtension(join(dir, 'dist')), src: join(dir, 'no-src') },
    probe: overrides.probe ?? (() => OK_CONFIG),
    log: (text) => out.push(text),
    error: (text) => out.push(text),
  });
  return { problems, output: out.join('\n') };
}

test('the committed demo runtime is a valid Flash-Lite route inside its free tier', () => {
  assert.deepEqual(validateDemoRuntime(DEMO_RUNTIME), []);
});

test('an invalid demo runtime model configuration is caught', () => {
  const bad = (overrides) => validateDemoRuntime({ ...DEMO_RUNTIME, ...overrides });
  assert.match(bad({ MODEL_DAILY_REQUEST_LIMITS: 'gemini-3.7-flash=20' }).join(' '), /no daily limit/);
  assert.match(bad({ GEMINI_GENERATION_MODEL: 'gemini-3.7-flash' }).join(' '), /not the Flash-Lite hackathon runtime/);
  assert.match(bad({ GEMINI_GENERATION_RPM: '60' }).join(' '), /GEMINI_GENERATION_RPM=60/);
  assert.match(bad({ MODEL_QUOTA_RESERVE: '600' }).join(' '), /MODEL_QUOTA_RESERVE=600/);
  assert.match(bad({ MODEL_DAILY_REQUEST_LIMITS: 'gemini-3.5-flash-lite=5000' }).join(' '), /exceeds its free tier/);
  assert.match(bad({ WORKER_ENABLED: 'false' }).join(' '), /WORKER_ENABLED/);
  assert.match(bad({ MODEL_DAILY_REQUEST_LIMITS: 'garbage' }).join(' '), /not model=requests_per_day/);
});

test('the extension must be built, and built for the local API and web', () => {
  const dir = tempDir();
  try {
    assert.match(extensionStatus({ dist: join(dir, 'missing'), src: join(dir, 'x') }).problems[0], /not built/);
    const dist = builtExtension(join(dir, 'dist'));
    assert.deepEqual(extensionStatus({ dist, src: join(dir, 'x'), webSupabaseUrl: 'https://project.supabase.co' }).problems, []);
    rmSync(join(dist, 'build-info.json'));
    assert.match(extensionStatus({ dist, src: join(dir, 'x') }).problems[0], /predates P9/);
    builtExtension(dist, { apiUrl: 'https://api.example.com' });
    assert.match(extensionStatus({ dist, src: join(dir, 'x') }).problems[0], /not the local API/);
    builtExtension(dist, { webUrl: 'http://localhost:3001' });
    assert.match(extensionStatus({ dist, src: join(dir, 'x') }).problems.join(' '), /not the local web/);
    builtExtension(dist, { anonKey: 'unset' });
    assert.match(extensionStatus({ dist, src: join(dir, 'x') }).problems.join(' '), /without Supabase settings/);
    builtExtension(dist);
    assert.match(
      extensionStatus({ dist, src: join(dir, 'x'), webSupabaseUrl: 'https://other.supabase.co' }).problems.join(' '),
      /different Supabase projects/,
    );
    // Sources newer than the build: a warning to rebuild and reload.
    const src = join(dir, 'src');
    mkdirSync(src);
    writeFileSync(join(src, 'a.ts'), '');
    const old = new Date(Date.now() - 3600_000);
    utimesSync(join(dist, 'build-info.json'), old, old);
    assert.match(extensionStatus({ dist, src }).warnings[0], /changed after the last build/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('preflight passes on a complete setup and prints no secret', async () => {
  const dir = tempDir();
  try {
    const { problems, output } = await runPreflight(dir);
    assert.deepEqual(problems, []);
    assert.match(output, /GEMINI_API_KEY=set/);
    assert.doesNotMatch(output, /AIza|postgresql:\/\/u:p/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('preflight catches :8000 already occupied', async () => {
  const dir = tempDir();
  const server = createServer().listen(0, '127.0.0.1');
  await new Promise((resolve) => server.once('listening', resolve));
  try {
    const { problems } = await runPreflight(dir, { backendPort: server.address().port });
    assert.match(problems.join(' '), /already in use: stop the other backend/);
  } finally {
    server.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('preflight catches a missing backend env and a missing web env', async () => {
  const dir = tempDir();
  try {
    const { problems } = await runPreflight(dir, { files: { backend: false, web: false } });
    assert.match(problems.join(' '), /backend\.env not found/);
    assert.match(problems.join(' '), /web\.env not found/);
    writeFileSync(join(dir, 'partial.env'), 'SUPABASE_URL=https://x.supabase.co\nGEMINI_API_KEY=\n');
    const partial = await runPreflight(dir, { backendEnvFile: join(dir, 'partial.env') });
    assert.match(partial.problems.join(' '), /DATABASE_URL is not set/);
    assert.match(partial.problems.join(' '), /GEMINI_API_KEY is not set/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('preflight catches an unbuilt extension', async () => {
  const dir = tempDir();
  try {
    const { problems } = await runPreflight(dir, { dist: join(dir, 'empty-dist') });
    assert.match(problems.join(' '), /extension is not built.*npm run build:extension/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('preflight catches a configuration the backend rejects, without echoing values', async () => {
  const dir = tempDir();
  try {
    const probe = () => ({ ok: false, errors: [{ field: 'settings', message: 'MODEL_DAILY_REQUEST_LIMITS has no daily limit for gemini-3.5-flash-lite' }] });
    const { problems, output } = await runPreflight(dir, { probe });
    assert.match(problems.join(' '), /backend rejects the demo configuration: settings: MODEL_DAILY_REQUEST_LIMITS/);
    assert.doesNotMatch(output, /AIza/);
    const wrongRoute = await runPreflight(dir, { probe: () => ({ ...OK_CONFIG, generation_model: 'gemini-3.7-flash' }) });
    assert.match(wrongRoute.problems.join(' '), /not the Flash-Lite demo route/);
    const noWorker = await runPreflight(dir, { probe: () => ({ ...OK_CONFIG, worker_will_start: false }) });
    assert.match(noWorker.problems.join(' '), /would not start its worker/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('verify passes on a healthy demo and names every failed check otherwise', async () => {
  const dir = tempDir();
  try {
    const dist = builtExtension(join(dir, 'dist'));
    const healthy = {
      fetcher: async (url) => (url.endsWith('/health') ? { status: 200, body: { status: 'ok', service: 'SkillMirror API', version: '0.1.0', environment: 'development' } } : { status: 200 }),
      probe: (command) =>
        command === 'config'
          ? OK_CONFIG
          : { ok: true, host: 'pooler', migrations: ['0001', '0002'], open_jobs: {}, stuck_pending: 0, stuck_processing: 0 },
      migrations: ['0001', '0002'],
      extension: { dist, src: join(dir, 'x') },
      log: () => {},
    };
    assert.deepEqual(await verify(healthy), []);
    const lines = [];
    const broken = await verify({
      ...healthy,
      fetcher: async () => ({ status: 0, error: 'ECONNREFUSED' }),
      probe: (command) => (command === 'config' ? { ...OK_CONFIG, routine_model: null, routing: 'architecture-default' } : { ok: true, host: 'pooler', migrations: ['0001'], open_jobs: { PENDING: 3 }, stuck_pending: 3, stuck_processing: 0 }),
      log: (text) => lines.push(text),
    });
    assert.deepEqual(broken, ['backend /health', 'web reachable', 'Flash-Lite demo route', 'migrations 0001-0002', 'worker processing']);
    assert.doesNotMatch(lines.join('\n'), /AIza/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
