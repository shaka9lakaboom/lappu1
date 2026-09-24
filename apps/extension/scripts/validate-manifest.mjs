// Structural validation of dist/ as an MV3 extension, run after every build.
// Loading in a real browser is covered separately by tests/load.spec.ts.
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
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

// P0 needs no privileges. Every permission added later must be justified here.
const ALLOWED_PERMISSIONS = new Set([]);
for (const permission of manifest.permissions ?? []) {
  check(ALLOWED_PERMISSIONS.has(permission), `unexpected permission "${permission}"`);
}
check((manifest.host_permissions ?? []).length === 0, 'P0 requests no host permissions');

if (errors.length > 0) {
  console.error(`Manifest validation failed:\n  - ${errors.join('\n  - ')}`);
  process.exit(1);
}
console.log(`Manifest OK: ${manifest.name} ${manifest.version} (MV3)`);
