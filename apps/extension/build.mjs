// Bundles the extension into dist/ as fully local code (no remote scripts).
// Each entry is a self-contained bundle: MV3 content scripts (added in P1)
// cannot load shared chunks, so code splitting is deliberately off.
import { copyFile, mkdir, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';

const root = dirname(fileURLToPath(import.meta.url));
const dist = join(root, 'dist');

await rm(dist, { recursive: true, force: true });

const common = {
  bundle: true,
  minify: true,
  target: 'chrome116',
  legalComments: 'none',
  define: { 'process.env.NODE_ENV': '"production"' },
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
  entryPoints: [join(root, 'src/popup/main.tsx')],
  outfile: join(dist, 'popup/popup.js'),
  format: 'iife',
  jsx: 'automatic',
});

await mkdir(join(dist, 'popup'), { recursive: true });
await copyFile(join(root, 'manifest.json'), join(dist, 'manifest.json'));
await copyFile(join(root, 'src/popup/popup.html'), join(dist, 'popup/popup.html'));
