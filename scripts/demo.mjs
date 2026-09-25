#!/usr/bin/env node
// Local hackathon demo runtime (ADR 0003 §1, ADR 0004): FastAPI :8000 with its in-process
// worker on the Flash-Lite free-tier runtime, plus Next.js :3000.
//
//   npm run demo           backend + worker + web in one terminal (Ctrl+C stops both)
//   npm run demo:backend   backend + worker only
//   npm run demo:web       web only
//   npm run demo:check     preflight only: env files, ports, interpreter, model routing
//
// Secrets stay where they are: the backend reads services/backend/.env, Next.js reads
// apps/web/.env.local, and this script never prints or copies their values. It only adds the
// non-secret DEMO_RUNTIME overrides below to the backend's process environment (a process
// variable wins over .env in pydantic-settings). Without this command the committed
// architecture default (gemini-3.7-flash on every task) is unchanged.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import net from 'node:net';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const BACKEND_DIR = join(ROOT, 'services', 'backend');
export const BACKEND_ENV_FILE = join(BACKEND_DIR, '.env');
export const WEB_ENV_FILE = join(ROOT, 'apps', 'web', '.env.local');
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

const BACKEND_REQUIRED = ['SUPABASE_URL', 'DATABASE_URL', 'GEMINI_API_KEY'];
const WEB_REQUIRED = ['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'NEXT_PUBLIC_API_URL'];

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

async function portInUse(port) {
  return (await isListening(port, '127.0.0.1')) || (await isListening(port, '::1'));
}

const flag = (set) => (set ? 'set' : 'MISSING');

/** Prints the preflight report; returns the problems that must stop the demo. */
async function preflight(parts) {
  const problems = [];
  const line = (label, text) => console.log(`  ${label.padEnd(9)} ${text}`);
  console.log('SkillMirror demo runtime');

  if (parts.includes('backend')) {
    const keys = envFileKeys(BACKEND_ENV_FILE);
    if (keys === null) {
      problems.push(`${relative(ROOT, BACKEND_ENV_FILE)} not found (copy services/backend/.env.example and fill it in)`);
    } else {
      line('backend', `${relative(ROOT, BACKEND_ENV_FILE)}: ${BACKEND_REQUIRED.map((k) => `${k}=${flag(keys[k])}`).join(' ')}`);
      for (const k of BACKEND_REQUIRED) if (!keys[k]) problems.push(`${k} is not set in ${relative(ROOT, BACKEND_ENV_FILE)}`);
      const appEnv = readEnvFile(BACKEND_ENV_FILE).APP_ENV;
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
    if (await portInUse(BACKEND_PORT)) {
      problems.push(
        `port ${BACKEND_PORT} is already in use: stop the other backend first ` +
          '(two backends run two workers that compete for the same jobs, possibly on different models)',
      );
    }
  }

  if (parts.includes('web')) {
    const keys = envFileKeys(WEB_ENV_FILE);
    if (keys === null) {
      problems.push(`${relative(ROOT, WEB_ENV_FILE)} not found`);
    } else {
      const api = readEnvFile(WEB_ENV_FILE).NEXT_PUBLIC_API_URL || 'MISSING';
      line(
        'web',
        `${relative(ROOT, WEB_ENV_FILE)}: NEXT_PUBLIC_SUPABASE_URL=${flag(keys.NEXT_PUBLIC_SUPABASE_URL)} ` +
          `NEXT_PUBLIC_SUPABASE_ANON_KEY=${flag(keys.NEXT_PUBLIC_SUPABASE_ANON_KEY)} api=${api}`,
      );
      for (const k of WEB_REQUIRED) if (!keys[k]) problems.push(`${k} is not set in ${relative(ROOT, WEB_ENV_FILE)}`);
    }
    if (await portInUse(WEB_PORT)) problems.push(`port ${WEB_PORT} is already in use: stop the other web server first`);
  }

  line('urls', [parts.includes('backend') && `http://localhost:${BACKEND_PORT}/health`, parts.includes('web') && `http://localhost:${WEB_PORT}`].filter(Boolean).join('  '));
  for (const problem of problems) console.error(`  ERROR     ${problem}`);
  return problems;
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
  child.on('error', (error) => {
    console.error(`${prefix} could not start: ${error.message}`);
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

async function waitForHealth(url, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (!stopping && Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return (await response.json()).status ?? 'ok';
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return null;
}

async function main(mode) {
  const parts = { check: ['backend', 'web'], all: ['backend', 'web'], backend: ['backend'], web: ['web'] }[mode];
  if (!parts) {
    console.error(`unknown mode ${JSON.stringify(mode)}: use check, backend, web or no argument`);
    process.exit(2);
  }
  const problems = await preflight(parts);
  if (problems.length > 0) process.exit(1);
  if (mode === 'check') return;

  process.on('SIGINT', () => shutdown(0));
  process.on('SIGTERM', () => shutdown(0));

  if (parts.includes('backend')) {
    // No --reload: a live demo must not restart (and interrupt its worker) on file edits.
    start('backend', backendPython(), ['-m', 'uvicorn', 'app.main:app', '--port', String(BACKEND_PORT)], {
      cwd: BACKEND_DIR,
      env: demoBackendEnv(),
    });
    waitForHealth(`http://127.0.0.1:${BACKEND_PORT}/health`).then((status) => {
      if (status) console.log(`[demo]    backend ready: http://localhost:${BACKEND_PORT}/health (${status})`);
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
  }
  console.log('[demo]    running; press Ctrl+C to stop');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main(process.argv[2] ?? 'all');
}
