import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { JSDOM } from 'jsdom';

const fixtures = join(dirname(fileURLToPath(import.meta.url)), '..', 'fixtures', 'chatgpt');

export function fixture(name: string): string {
  return readFileSync(join(fixtures, name), 'utf8');
}

/** The <main> content of a fixture, for swapping DOM states in place. */
export function fixtureMain(name: string): string {
  const html = fixture(name);
  return html.slice(html.indexOf('<main>') + 6, html.indexOf('</main>'));
}

export function domFrom(name: string, url = 'https://chatgpt.com/'): JSDOM {
  return new JSDOM(fixture(name), { url });
}

/** Path of a real fixture's captured URL, taken from its header comment. */
export function fixtureUrl(name: string): string {
  const match = /captured \S+ from (https:\/\/chatgpt\.com\S*)/.exec(fixture(name));
  return match ? match[1] : 'https://chatgpt.com/';
}
