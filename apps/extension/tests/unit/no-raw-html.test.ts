/**
 * H14 (ADR 0008): product code never injects raw HTML. Captured conversation text and model
 * output are untrusted; React escapes text, and nothing may bypass that. Tests may build DOM
 * fixtures; product source may not.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const SINKS = /dangerouslySetInnerHTML|\.innerHTML\s*=|\.outerHTML\s*=|insertAdjacentHTML|document\.write\(/;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(tsx?|jsx?)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

describe('no raw HTML sinks in product code', () => {
  it('uses none', () => {
    const root = join(dirname(fileURLToPath(import.meta.url)), '../..', 'src');
    const files = sources(root);
    expect(files.length).toBeGreaterThan(5);
    const offenders = files.filter((f) => SINKS.test(readFileSync(f, 'utf8'))).map((f) => relative(root, f));
    expect(offenders).toEqual([]);
  });
});
