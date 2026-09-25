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
