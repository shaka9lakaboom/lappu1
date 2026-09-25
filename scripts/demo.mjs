#!/usr/bin/env node
// Local hackathon demo runtime (ADR 0003 §1, ADR 0004): FastAPI :8000 with its in-process
// worker on the Flash-Lite free-tier runtime, plus Next.js :3000.
//
//   npm run demo           backend + worker + web in one terminal (Ctrl+C stops both)
//   npm run demo:backend   backend + worker only
//   npm run demo:web       web only
//   npm run demo:check     preflight only: env files, ports, interpreter, model routing (validated
//                          by the backend's own Settings), extension build + its localhost config
//   npm run demo:verify    with the demo running: /health, web, hosted DB + migrations (read only),
//                          worker, Flash-Lite route, extension build - changes nothing
//
// Secrets stay where they are: the backend reads services/backend/.env, Next.js reads
// apps/web/.env.local, and this script never prints or copies their values. It only adds the
// non-secret DEMO_RUNTIME overrides below to the backend's process environment (a process
// variable wins over .env in pydantic-settings). Without this command the committed
// architecture default (gemini-3.7-flash on every task) is unchanged.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import net from 'node:net';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const BACKEND_DIR = join(ROOT, 'services', 'backend');
export const BACKEND_ENV_FILE = join(BACKEND_DIR, '.env');
export const WEB_ENV_FILE = join(ROOT, 'apps', 'web', '.env.local');
export const EXTENSION_DIR = join(ROOT, 'apps', 'extension');
export const EXTENSION_DIST = join(EXTENSION_DIR, 'dist');
export const MIGRATIONS_DIR = join(ROOT, 'supabase', 'migrations');
export const BACKEND_PORT = 8000;
export const WEB_PORT = 3000;

/** The hackathon free-tier runtime. Non-secret by construction (checked by scripts/demo.test.mjs). */
export const DEMO_RUNTIME = Object.freeze({
  GEMINI_GENERATION_MODEL: 'gemini-3.5-flash-lite',
  GEMINI_ROUTINE_MODEL: 'gemini-3.5-flash-lite',
  GEMINI_GENERATION_RPM: '12',
  MODEL_DAILY_REQUEST_LIMITS: 'gemini-3.5-flash-lite=500,gemini-3.7-flash=20,gemini-3.8-flash=20',
  MODEL_QUOTA_RESERVE: '25',
  WORKER_ENABLED: 'true',
});

/** Flash-Lite's free tier (owner-verified 2026-09-25): the demo must stay inside it. */
const FREE_TIER = Object.freeze({ 'gemini-3.5-flash-lite': { rpm: 15, rpd: 500 } });

const BACKEND_REQUIRED = ['SUPABASE_URL', 'DATABASE_URL', 'GEMINI_API_KEY'];
const WEB_REQUIRED = ['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'NEXT_PUBLIC_API_URL'];
const EXTENSION_FILES = ['manifest.json', 'background/service-worker.js', 'content/chatgpt.js', 'popup/popup.js', 'popup/popup.html'];

/** Environment of the backend process: the caller's environment plus the demo runtime. */
export function demoBackendEnv(base = process.env) {
  return { ...base, ...DEMO_RUNTIME, PYTHONUNBUFFERED: '1' };
}

function readEnvFile(path) {
  if (!existsSync(path)) return null;
  const values = {};
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const match = /^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
    if (match) values[match[1]] = match[2].replace(/^(['"])(.*)\1$/, '$2');
  }
  return values;
}

/** Which variables an env file sets (name -> non-empty), never their values. Null if missing. */
export function envFileKeys(path) {
  const values = readEnvFile(path);
  if (values === null) return null;
  return Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value.length > 0]));
}

export function backendPython(env = process.env) {
  if (env.SKILLMIRROR_PYTHON) return env.SKILLMIRROR_PYTHON;
  const venv =
    process.platform === 'win32'
      ? join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe')
      : join(BACKEND_DIR, '.venv', 'bin', 'python');
  return existsSync(venv) ? venv : 'python';
}

/** Problems of a demo runtime (the model routing and its budget), or [] when it is valid. */
export function validateDemoRuntime(runtime = DEMO_RUNTIME) {
  const problems = [];
  const limits = {};
  for (const part of String(runtime.MODEL_DAILY_REQUEST_LIMITS ?? '').split(',')) {
    const match = /^\s*([A-Za-z0-9._-]+)\s*=\s*(\d+)\s*$/.exec(part);
    if (!match) {
      problems.push(`MODEL_DAILY_REQUEST_LIMITS entry "${part.trim()}" is not model=requests_per_day`);
      continue;
    }
    limits[match[1]] = Number(match[2]);
  }
  const reserve = Number(runtime.MODEL_QUOTA_RESERVE);
  const rpm = Number(runtime.GEMINI_GENERATION_RPM);
  for (const key of ['GEMINI_GENERATION_MODEL', 'GEMINI_ROUTINE_MODEL']) {
    const model = runtime[key];
    if (!model) {
      problems.push(`${key} is not set`);
      continue;
    }
    if (!(model in limits)) problems.push(`${key}=${model} has no daily limit in MODEL_DAILY_REQUEST_LIMITS`);
    const tier = FREE_TIER[model];
    if (!tier) problems.push(`${key}=${model} is not the Flash-Lite hackathon runtime`);
    else if (limits[model] > tier.rpd) problems.push(`${model} daily limit ${limits[model]} exceeds its free tier (${tier.rpd}/day)`);
    if (model in limits && !(reserve >= 0 && reserve < limits[model])) {
      problems.push(`MODEL_QUOTA_RESERVE=${runtime.MODEL_QUOTA_RESERVE} must be >= 0 and below ${model}'s limit ${limits[model]}`);
    }
  }
  const tier = FREE_TIER[runtime.GEMINI_GENERATION_MODEL];
  if (!(Number.isInteger(rpm) && rpm >= 1 && (!tier || rpm <= tier.rpm))) {
    problems.push(`GEMINI_GENERATION_RPM=${runtime.GEMINI_GENERATION_RPM} must be 1..${tier?.rpm ?? 15} requests/min`);
  }
  if (runtime.WORKER_ENABLED !== 'true') problems.push('WORKER_ENABLED must be true for the demo');
  return problems;
}

function isLocalOrigin(value, port) {
  try {
    const url = new URL(value);
    return (url.hostname === 'localhost' || url.hostname === '127.0.0.1') && url.protocol === 'http:' && url.port === String(port);
  } catch {
    return false;
  }
}

function newestMtime(dir) {
  let newest = 0;
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    newest = Math.max(newest, stat.isDirectory() ? newestMtime(path) : stat.mtimeMs);
  }
  return newest;
}

/**
 * The unpacked extension in `dist`: built, and pointed at the local API / web (and the same
 * Supabase project as the web app). Returns { problems, warnings, info } - never a key.
 */
export function extensionStatus({ dist = EXTENSION_DIST, src = join(EXTENSION_DIR, 'src'), webSupabaseUrl = null } = {}) {
  const problems = [];
  const warnings = [];
  const missing = EXTENSION_FILES.filter((f) => !existsSync(join(dist, f)));
  if (missing.length > 0) {
    problems.push(`the extension is not built (${relative(ROOT, dist) || dist} lacks ${missing.join(', ')}): run npm run build:extension`);
    return { problems, warnings, info: null };
  }
  const infoPath = join(dist, 'build-info.json');
  if (!existsSync(infoPath)) {
    problems.push('the extension build predates P9 (no build-info.json): run npm run build:extension');
    return { problems, warnings, info: null };
  }
  const info = JSON.parse(readFileSync(infoPath, 'utf8'));
  if (!info.apiUrl) problems.push('the extension was built without an API URL (apps/web/.env.local NEXT_PUBLIC_API_URL): rebuild it');
  else if (!isLocalOrigin(info.apiUrl, BACKEND_PORT)) problems.push(`the extension talks to ${info.apiUrl}, not the local API http://localhost:${BACKEND_PORT}: rebuild it`);
  if (!isLocalOrigin(info.webUrl, WEB_PORT)) problems.push(`the extension opens ${info.webUrl}, not the local web http://localhost:${WEB_PORT}: rebuild it`);
  if (!info.supabaseUrl || info.anonKey !== 'set') problems.push('the extension was built without Supabase settings: rebuild it after filling apps/web/.env.local');
  else if (webSupabaseUrl && new URL(webSupabaseUrl).origin !== new URL(info.supabaseUrl).origin) {
    problems.push('the extension and the web app use different Supabase projects: rebuild the extension');
  }
  // build-info.json is written by every build (copied files keep their source's mtime on Windows).
  if (existsSync(src) && newestMtime(src) > statSync(infoPath).mtimeMs) {
    warnings.push('extension sources changed after the last build: run npm run build:extension, then reload it in chrome://extensions');
  }
  return { problems, warnings, info };
}

/** Runs a read-only backend probe (services/backend/scripts/demo_probe.py) with the demo env. */
export function runProbe(command, { python = backendPython(), env = demoBackendEnv() } = {}) {
  const result = spawnSync(python, [join('scripts', 'demo_probe.py'), command], {
    cwd: BACKEND_DIR,
    env,
    encoding: 'utf8',
    timeout: 60000,
  });
  if (result.error) return { ok: false, error: `could not run ${python}: ${result.error.message}` };
  try {
    return JSON.parse(result.stdout.trim().split(/\r?\n/).pop());
  } catch {
    return { ok: false, error: `probe ${command} failed (exit ${result.status})` };
  }
}

function isListening(port, host) {
  return new Promise((resolve) => {
    const socket = net.connect({ port, host });
    socket.setTimeout(800);
    socket.once('connect', () => {
      socket.destroy();
      resolve(true);
    });
    socket.once('timeout', () => {
      socket.destroy();
      resolve(false);
    });
    socket.once('error', () => resolve(false));
  });
}

export async function portInUse(port) {
  return (await isListening(port, '127.0.0.1')) || (await isListening(port, '::1'));
}

const flag = (set) => (set ? 'set' : 'MISSING');

/**
 * Prints the preflight report; returns the problems that must stop the demo. Every input can be
 * replaced (tests): env files, ports, the extension build and the backend config probe.
 */
export async function preflight(parts, options = {}) {
  const {
    backendEnvFile = BACKEND_ENV_FILE,
    webEnvFile = WEB_ENV_FILE,
    backendPort = BACKEND_PORT,
    webPort = WEB_PORT,
    extension = {},
    probe = (command) => runProbe(command),
    log = console.log,
    error = console.error,
  } = options;
  const problems = [];
  const warnings = [];
  const line = (label, text) => log(`  ${label.padEnd(9)} ${text}`);
  log('SkillMirror demo runtime');

  if (parts.includes('backend')) {
    const keys = envFileKeys(backendEnvFile);
    if (keys === null) {
      problems.push(`${relative(ROOT, backendEnvFile)} not found (copy services/backend/.env.example and fill it in)`);
    } else {
      line('backend', `${relative(ROOT, backendEnvFile)}: ${BACKEND_REQUIRED.map((k) => `${k}=${flag(keys[k])}`).join(' ')}`);
      for (const k of BACKEND_REQUIRED) if (!keys[k]) problems.push(`${k} is not set in ${relative(ROOT, backendEnvFile)}`);
      const appEnv = readEnvFile(backendEnvFile).APP_ENV;
      if (appEnv === 'test') problems.push('APP_ENV=test in services/backend/.env: the worker never starts in test mode');
    }
    const python = backendPython();
    line('python', python === 'python' ? 'python (PATH; no services/backend/.venv found)' : relative(ROOT, python) || python);
    line(
      'models',
      `generation=${DEMO_RUNTIME.GEMINI_GENERATION_MODEL} routine=${DEMO_RUNTIME.GEMINI_ROUTINE_MODEL} ` +
        '(demo override; the architecture default gemini-3.7-flash is untouched)',
    );
    line(
      'budget',
      `${DEMO_RUNTIME.MODEL_DAILY_REQUEST_LIMITS} per day, reserve ${DEMO_RUNTIME.MODEL_QUOTA_RESERVE}, ` +
        `${DEMO_RUNTIME.GEMINI_GENERATION_RPM} generation requests/min`,
    );
    problems.push(...validateDemoRuntime());
    if (keys !== null) {
      // The backend's own Settings with the demo overrides: the exact configuration it will run.
      const config = probe('config');
      if (!config.ok) {
        const details = config.errors?.map((e) => `${e.field}: ${e.message}`).join('; ') ?? config.error;
        problems.push(`the backend rejects the demo configuration: ${details}`);
      } else {
        line('config', `valid · worker ${config.worker_will_start ? 'will start' : 'will NOT start'} · routing ${config.routing}`);
        if (!config.worker_will_start) problems.push('the backend would not start its worker (check WORKER_ENABLED, DATABASE_URL, GEMINI_API_KEY, APP_ENV)');
        if (config.generation_model !== DEMO_RUNTIME.GEMINI_GENERATION_MODEL || config.routine_model !== DEMO_RUNTIME.GEMINI_ROUTINE_MODEL) {
          problems.push(`the backend would run ${config.generation_model} / ${config.routine_model}, not the Flash-Lite demo route`);
        }
        if (!config.cors_allows_extension) warnings.push('CORS_ORIGINS lacks the extension origin: the popup cannot reach the API');
      }
    }
    if (await portInUse(backendPort)) {
      problems.push(
        `port ${backendPort} is already in use: stop the other backend first ` +
          '(two backends run two workers that compete for the same jobs, possibly on different models)',
      );
    }
  }

  let webSupabaseUrl = null;
  if (parts.includes('web')) {
    const keys = envFileKeys(webEnvFile);
    if (keys === null) {
      problems.push(`${relative(ROOT, webEnvFile)} not found (copy apps/web/.env.example and fill it in)`);
    } else {
      const values = readEnvFile(webEnvFile);
      webSupabaseUrl = values.NEXT_PUBLIC_SUPABASE_URL || null;
      const api = values.NEXT_PUBLIC_API_URL || 'MISSING';
      line(
        'web',
        `${relative(ROOT, webEnvFile)}: NEXT_PUBLIC_SUPABASE_URL=${flag(keys.NEXT_PUBLIC_SUPABASE_URL)} ` +
          `NEXT_PUBLIC_SUPABASE_ANON_KEY=${flag(keys.NEXT_PUBLIC_SUPABASE_ANON_KEY)} api=${api}`,
      );
      for (const k of WEB_REQUIRED) if (!keys[k]) problems.push(`${k} is not set in ${relative(ROOT, webEnvFile)}`);
    }
    if (await portInUse(webPort)) problems.push(`port ${webPort} is already in use: stop the other web server first`);
  }

  if (parts.includes('extension')) {
    const status = extensionStatus({ webSupabaseUrl, ...extension });
    if (status.info) line('extension', `built · api=${status.info.apiUrl} web=${status.info.webUrl} (load ${relative(ROOT, EXTENSION_DIST)} unpacked)`);
    problems.push(...status.problems);
    warnings.push(...status.warnings);
  }

  line('urls', [parts.includes('backend') && `http://localhost:${backendPort}/health`, parts.includes('web') && `http://localhost:${webPort}`].filter(Boolean).join('  '));
  for (const warning of warnings) error(`  WARNING   ${warning}`);
  for (const problem of problems) error(`  ERROR     ${problem}`);
  return problems;
}

/** Migration versions of this checkout (supabase/migrations/NNNN_*.sql). */
export function localMigrations(dir = MIGRATIONS_DIR) {
  return readdirSync(dir)
    .map((name) => /^(\d{4})_.*\.sql$/.exec(name)?.[1])
    .filter(Boolean)
    .sort();
}

async function fetchStatus(url, timeoutMs = 5000) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs), redirect: 'manual' });
    return { status: response.status, body: response.headers.get('content-type')?.includes('json') ? await response.json() : null };
  } catch (err) {
    return { status: 0, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * With the demo running: checks what a live presentation needs, read only. Returns the failed
 * check names. Nothing is written anywhere (the database probe runs in a READ ONLY transaction).
 */
export async function verify(options = {}) {
  const {
    fetcher = fetchStatus,
    probe = (command) => runProbe(command),
    migrations = localMigrations(),
    extension = {},
    log = console.log,
  } = options;
  const failed = [];
  const check = (name, ok, detail) => {
    log(`  [${ok ? 'PASS' : 'FAIL'}] ${name.padEnd(28)} ${detail}`);
    if (!ok) failed.push(name);
  };
  log('SkillMirror demo verify (read only)');

  const health = await fetcher(`http://127.0.0.1:${BACKEND_PORT}/health`);
  check('backend /health', health.status === 200 && health.body?.status === 'ok', health.status === 200 ? `${health.body?.service} ${health.body?.version} (${health.body?.environment})` : `not answering (${health.error ?? health.status})`);

  const web = await fetcher(`http://127.0.0.1:${WEB_PORT}/sign-in`, 20000);
  check('web reachable', web.status === 200, web.status === 200 ? `http://localhost:${WEB_PORT}` : `not answering (${web.error ?? web.status})`);

  const config = probe('config');
  check('backend configuration', config.ok === true, config.ok ? `app_env ${config.app_env}` : (config.errors?.map((e) => e.field).join(', ') ?? config.error));
  check(
    'worker configured',
    config.ok === true && config.worker_will_start === true,
    config.ok ? `worker ${config.worker_will_start ? 'enabled (DATABASE_URL + GEMINI_API_KEY set)' : 'would not start'}` : 'unknown',
  );
  const flashLite = DEMO_RUNTIME.GEMINI_GENERATION_MODEL;
  check(
    'Flash-Lite demo route',
    config.ok === true && config.generation_model === flashLite && config.routine_model === flashLite && config.routing === 'free-tier' && config.daily_limits?.[flashLite] > 0,
    config.ok ? `generation=${config.generation_model} routine=${config.routine_model} budget ${config.daily_limits?.[flashLite] ?? 0}/day reserve ${config.quota_reserve}` : 'unknown',
  );

  const db = probe('database');
  check('hosted database reachable', db.ok === true, db.ok ? `${db.host} (read only)` : (db.error ?? 'failed'));
  const expected = migrations.join(',');
  check(
    `migrations ${migrations[0]}-${migrations.at(-1)}`,
    db.ok === true && db.migrations.join(',') === expected,
    db.ok ? `remote ${db.migrations.join(',') || 'none'}` : 'unknown',
  );
  // Liveness without writing anything: nothing waits longer than a few minutes and no lock is
  // stale. (Connection names cannot tell: the Supabase pooler reports every client as Supavisor.)
  check(
    'worker processing',
    db.ok === true && db.stuck_pending === 0 && db.stuck_processing === 0,
    db.ok
      ? `open jobs ${JSON.stringify(db.open_jobs)}; waiting > 3 min ${db.stuck_pending}, stale locks ${db.stuck_processing}`
      : 'unknown',
  );

  const ext = extensionStatus(extension);
  check('extension build + localhost', ext.problems.length === 0, ext.problems[0] ?? `api=${ext.info.apiUrl} web=${ext.info.webUrl}`);
  for (const warning of ext.warnings) log(`  WARNING ${warning}`);
  log(failed.length === 0 ? 'demo verify: PASS' : `demo verify: FAIL (${failed.join(', ')})`);
  return failed;
}

const children = [];
let stopping = false;

function pipeWithPrefix(stream, prefix, out) {
  let buffered = '';
  stream.setEncoding('utf8');
  stream.on('data', (chunk) => {
    buffered += chunk;
    const lines = buffered.split(/\r?\n/);
    buffered = lines.pop();
    for (const text of lines) out.write(`${prefix} ${text}\n`);
  });
  stream.on('end', () => buffered && out.write(`${prefix} ${buffered}\n`));
}

function start(name, command, args, options) {
  const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'], ...options });
  const prefix = `[${name}]`.padEnd(9);
  pipeWithPrefix(child.stdout, prefix, process.stdout);
  pipeWithPrefix(child.stderr, prefix, process.stderr);
  child.on('error', (err) => {
    console.error(`${prefix} could not start: ${err.message}`);
    shutdown(1);
  });
  child.on('exit', (code, signal) => {
    if (stopping) return;
    console.error(`${prefix} exited (${signal ?? `code ${code}`}); stopping the demo`);
    shutdown(code || 1);
  });
  children.push(child);
  return child;
}

function shutdown(code) {
  if (stopping) return;
  stopping = true;
  // Ctrl+C reaches every process of the console, so the backend normally stops its worker
  // cleanly by itself (no job is left PROCESSING). Force only what is still running later.
  const alive = () => children.filter((c) => c.exitCode === null && c.signalCode === null);
  if (process.platform !== 'win32') for (const c of alive()) c.kill('SIGINT');
  const deadline = Date.now() + 15000;
  const timer = setInterval(() => {
    if (alive().length > 0 && Date.now() < deadline) return;
    clearInterval(timer);
    for (const c of alive()) {
      if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(c.pid), '/T', '/F'], { stdio: 'ignore' });
      else c.kill('SIGKILL');
    }
    process.exit(code);
  }, 250);
}

async function waitFor(url, timeoutMs, accept = (r) => r.ok) {
  const deadline = Date.now() + timeoutMs;
  while (!stopping && Date.now() < deadline) {
    try {
      const response = await fetch(url, { redirect: 'manual' });
      if (accept(response)) return response;
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return null;
}

const seconds = (since) => `${((Date.now() - since) / 1000).toFixed(1)}s`;

async function main(mode) {
  const parts = {
    check: ['backend', 'web', 'extension'],
    all: ['backend', 'web', 'extension'],
    backend: ['backend'],
    web: ['web'],
  }[mode];
  if (mode === 'verify') {
    const failed = await verify();
    process.exit(failed.length === 0 ? 0 : 1);
  }
  if (!parts) {
    console.error(`unknown mode ${JSON.stringify(mode)}: use check, verify, backend, web or no argument`);
    process.exit(2);
  }
  const problems = await preflight(parts);
  if (problems.length > 0) process.exit(1);
  if (mode === 'check') {
    console.log('demo check: PASS');
    return;
  }

  process.on('SIGINT', () => shutdown(0));
  process.on('SIGTERM', () => shutdown(0));
  const started = Date.now();

  if (parts.includes('backend')) {
    // No --reload: a live demo must not restart (and interrupt its worker) on file edits.
    start('backend', backendPython(), ['-m', 'uvicorn', 'app.main:app', '--port', String(BACKEND_PORT)], {
      cwd: BACKEND_DIR,
      env: demoBackendEnv(),
    });
    waitFor(`http://127.0.0.1:${BACKEND_PORT}/health`, 90000).then(async (response) => {
      if (response) console.log(`[demo]    backend ready in ${seconds(started)}: http://localhost:${BACKEND_PORT}/health (${(await response.json()).status})`);
      else if (!stopping) console.error('[demo]    backend did not answer /health within 90s');
    });
  }
  if (parts.includes('web')) {
    const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
    start('web', npm, ['run', 'dev', '--workspace', '@skillmirror/web'], {
      cwd: ROOT,
      env: process.env,
      shell: process.platform === 'win32',
    });
    // The first request compiles the page (Next.js dev): report when the sign-in page answers.
    waitFor(`http://127.0.0.1:${WEB_PORT}/sign-in`, 180000, (r) => r.status === 200).then((response) => {
      if (response) console.log(`[demo]    web ready in ${seconds(started)}: http://localhost:${WEB_PORT} (then: npm run demo:verify)`);
      else if (!stopping) console.error('[demo]    the web app did not answer within 180s');
    });
  }
  console.log('[demo]    running; press Ctrl+C to stop');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main(process.argv[2] ?? 'all');
}
