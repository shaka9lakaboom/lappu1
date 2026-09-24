// Structural validation of dist/ as an MV3 extension, run after every build.
// Loading in a real browser is covered separately by tests/load.spec.ts.
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, process.env.SKILLMIRROR_OUT_DIR || 'dist');
const errors = [];
const check = (condition, message) => condition || errors.push(message);

const manifestPath = join(dist, 'manifest.json');
if (!existsSync(manifestPath)) {
  console.error('dist/manifest.json not found - run the build first.');
  process.exit(1);
}
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));

check(manifest.manifest_version === 3, 'manifest_version must be 3');
check(/^\d+(\.\d+){0,3}$/.test(manifest.version ?? ''), `invalid version "${manifest.version}"`);
check(manifest.version === pkg.version, `manifest version ${manifest.version} != package.json ${pkg.version}`);
check(typeof manifest.name === 'string' && manifest.name.length <= 75, 'name is required (max 75 chars)');
check(!('background' in manifest && 'scripts' in manifest.background), 'MV3 forbids background.scripts');
check(!('browser_action' in manifest || 'page_action' in manifest), 'use "action" in MV3');

const worker = manifest.background?.service_worker;
check(Boolean(worker), 'background.service_worker is required');
if (worker) check(existsSync(join(dist, worker)), `service worker ${worker} missing`);

const popup = manifest.action?.default_popup;
check(Boolean(popup), 'action.default_popup is required');
if (popup) {
  const popupPath = join(dist, popup);
  check(existsSync(popupPath), `popup ${popup} missing`);
  if (existsSync(popupPath)) {
    const html = readFileSync(popupPath, 'utf8');
    for (const [, src] of html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g)) {
      check(!/^(https?:)?\/\//.test(src), `remote script not allowed in MV3: ${src}`);
      check(existsSync(join(dirname(popupPath), src)), `popup script ${src} missing`);
    }
    check(!/<script(?![^>]*\ssrc=)[^>]*>/.test(html), 'inline <script> blocks are not allowed in MV3');
  }
}

// Every permission must be justified here (architecture section 15.2: minimal permissions).
//   storage - pause flag + "may capture" flag shared with the content script
//   alarms  - periodic LocalQueue flush after the service worker was suspended
const ALLOWED_PERMISSIONS = new Set(['storage', 'alarms']);
for (const permission of manifest.permissions ?? []) {
  check(ALLOWED_PERMISSIONS.has(permission), `unexpected permission "${permission}"`);
}
// The API and Supabase are reached through CORS, so no host permissions are needed.
check((manifest.host_permissions ?? []).length === 0, 'no host_permissions expected');
check(!('externally_connectable' in manifest), 'externally_connectable is not used');
check(!('web_accessible_resources' in manifest), 'no web_accessible_resources expected');

// Content scripts: ChatGPT only (P1), bundled locally.
const SUPPORTED_MATCHES = new Set(['https://chatgpt.com/*']);
const scripts = manifest.content_scripts ?? [];
check(scripts.length === 1, 'exactly one content script (ChatGPT) expected');
for (const script of scripts) {
  for (const match of script.matches ?? []) check(SUPPORTED_MATCHES.has(match), `unsupported content script match "${match}"`);
  for (const js of script.js ?? []) {
    check(!/^(https?:)?\/\//.test(js), `remote content script not allowed: ${js}`);
    check(existsSync(join(dist, js)), `content script ${js} missing`);
  }
}
check(typeof manifest.key === 'string' && manifest.key.length > 200, 'manifest.key (public key for a stable dev extension id) is required');

// No bundled file may contain a Supabase secret or service-role key.
for (const file of [worker, 'content/chatgpt.js', 'popup/popup.js']) {
  const path = file && join(dist, file);
  if (!path || !existsSync(path)) continue;
  const code = readFileSync(path, 'utf8');
  check(!/sb_secret_[A-Za-z0-9_-]{10,}/.test(code), `${file} contains a Supabase secret key`);
  for (const [token] of code.matchAll(/eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g)) {
    const role = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString('utf8')).role;
    check(role !== 'service_role', `${file} contains a service-role key`);
  }
  check(!/(^|[^A-Za-z0-9_$.])eval[(]|new Function[(]/.test(code), `${file} uses eval/new Function`);
}

if (errors.length > 0) {
  console.error(`Manifest validation failed:\n  - ${errors.join('\n  - ')}`);
  process.exit(1);
}
console.log(`Manifest OK: ${manifest.name} ${manifest.version} (MV3)`);
