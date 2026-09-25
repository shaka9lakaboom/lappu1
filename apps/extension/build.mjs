// Bundles the extension into dist/ as fully local code (no remote scripts).
// Each entry is a self-contained bundle: MV3 content scripts cannot load
// shared chunks, so code splitting is deliberately off.
//
// Public configuration is injected at build time from, in order:
//   1. process env:            SKILLMIRROR_SUPABASE_URL, SKILLMIRROR_SUPABASE_ANON_KEY,
//                              SKILLMIRROR_API_URL, SKILLMIRROR_WEB_URL
//   2. apps/extension/.env.local (same names)
//   3. apps/web/.env.local:    NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, NEXT_PUBLIC_API_URL
// Without them the build still succeeds and the popup reports "Not configured"
// (CI builds this way). Secret or service-role keys are refused.
import { copyFile, mkdir, rm, writeFile } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';

const root = dirname(fileURLToPath(import.meta.url));
// SKILLMIRROR_OUT_DIR lets tests build a separately configured copy (e.g. dist-e2e).
const dist = join(root, process.env.SKILLMIRROR_OUT_DIR || 'dist');

function readEnvFile(path) {
  if (!existsSync(path)) return {};
  const values = {};
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const match = /^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
    if (match) values[match[1]] = match[2].replace(/^(['"])(.*)\1$/, '$2');
  }
  return values;
}

function jwtRole(key) {
  const parts = key.split('.');
  if (parts.length !== 3) return undefined;
  try {
    return JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8')).role;
  } catch {
    return undefined;
  }
}

function resolveConfig() {
  const own = readEnvFile(join(root, '.env.local'));
  const web = readEnvFile(join(root, '..', 'web', '.env.local'));
  const pick = (name, webName) => process.env[name] || own[name] || (webName ? web[webName] : '') || null;
  const config = {
    supabaseUrl: pick('SKILLMIRROR_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_URL'),
    supabaseAnonKey: pick('SKILLMIRROR_SUPABASE_ANON_KEY', 'NEXT_PUBLIC_SUPABASE_ANON_KEY'),
    apiUrl: pick('SKILLMIRROR_API_URL', 'NEXT_PUBLIC_API_URL'),
    webUrl: pick('SKILLMIRROR_WEB_URL') ?? 'http://localhost:3000',
  };
  const key = config.supabaseAnonKey;
  if (key && (key.startsWith('sb_secret_') || jwtRole(key) === 'service_role')) {
    throw new Error('Refusing to bundle a secret/service-role Supabase key into the extension. Use the anon/publishable key.');
  }
  for (const field of ['supabaseUrl', 'apiUrl', 'webUrl']) {
    if (!config[field]) continue;
    const url = new URL(config[field]);
    const local = url.hostname === 'localhost' || url.hostname === '127.0.0.1';
    if (url.protocol !== 'https:' && !(local && url.protocol === 'http:')) {
      throw new Error(`${field} must use https (http only for localhost): ${config[field]}`);
    }
    config[field] = url.origin;
  }
  return config;
}

const config = resolveConfig();
console.log(
  `Companion config: supabase=${config.supabaseUrl ? 'set' : 'unset'} anonKey=${config.supabaseAnonKey ? 'set' : 'unset'} ` +
    `api=${config.apiUrl ?? 'unset'} web=${config.webUrl}`,
);

await rm(dist, { recursive: true, force: true });

const common = {
  bundle: true,
  minify: true,
  target: 'chrome116',
  legalComments: 'none',
  define: {
    'process.env.NODE_ENV': '"production"',
    __SKILLMIRROR_CONFIG__: JSON.stringify(config),
  },
  logLevel: 'info',
};

await build({
  ...common,
  entryPoints: [join(root, 'src/background/service-worker.ts')],
  outfile: join(dist, 'background/service-worker.js'),
  format: 'esm',
});

await build({
  ...common,
  entryPoints: [join(root, 'src/content/index.ts')],
  outfile: join(dist, 'content/chatgpt.js'),
  format: 'iife',
});

await build({
  ...common,
  entryPoints: [join(root, 'src/popup/main.tsx')],
  outfile: join(dist, 'popup/popup.js'),
  format: 'iife',
  jsx: 'automatic',
});

await mkdir(join(dist, 'popup'), { recursive: true });
await copyFile(join(root, 'manifest.json'), join(dist, 'manifest.json'));
await copyFile(join(root, 'src/popup/popup.html'), join(dist, 'popup/popup.html'));

// Public build facts for `npm run demo:check` / `demo:verify` (P9): which API and web the bundle
// talks to. Origins only - never a key (the anon key is only reported as set / unset).
const manifest = JSON.parse(readFileSync(join(root, 'manifest.json'), 'utf8'));
await writeFile(
  join(dist, 'build-info.json'),
  `${JSON.stringify(
    {
      extensionVersion: manifest.version,
      apiUrl: config.apiUrl,
      webUrl: config.webUrl,
      supabaseUrl: config.supabaseUrl,
      anonKey: config.supabaseAnonKey ? 'set' : 'unset',
    },
    null,
    2,
  )}
`,
);
