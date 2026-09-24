/**
 * Cross-language contract checks.
 *
 * 1. The TypeScript normalization/fingerprint matches the Python backend
 *    (shared vectors in packages/contracts/fixtures/fingerprint-vectors.json).
 * 2. Envelopes produced by the real capture pipeline from the real ChatGPT
 *    fixture are written to packages/contracts/fixtures/captured-envelopes.json;
 *    the backend integration tests post exactly these to /v1/events/batch.
 *    Set UPDATE_GOLDEN=1 to regenerate after an intentional change.
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { fallbackFingerprintInput, normalizeContent } from '@skillmirror/contracts';
import { describe, expect, it } from 'vitest';

import { CaptureManager, type UnboundEnvelope } from '../../src/capture/CaptureManager';
import { contentHash, sha256Hex } from '../../src/capture/EventFingerprint';
import { ChatGPTAdapter } from '../../src/content/adapters/ChatGPTAdapter';
import type { ProviderAdapter } from '../../src/content/adapters/ProviderAdapter';
import { domFrom, fixtureUrl } from './helpers';

const contracts = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..', 'packages', 'contracts');
const vectors = JSON.parse(readFileSync(join(contracts, 'fixtures', 'fingerprint-vectors.json'), 'utf8')) as {
  content: Array<{ name: string; text: string; normalized: string; content_hash: string }>;
  fingerprints: Array<Parameters<typeof fallbackFingerprintInput>[0] & { fingerprint: string }>;
};

describe('fingerprint vectors shared with the backend', () => {
  it.each(vectors.content.map((c) => [c.name, c] as const))('%s', async (_name, c) => {
    expect(normalizeContent(c.text)).toBe(c.normalized);
    expect(await contentHash(c.text)).toBe(c.content_hash);
  });

  it.each(vectors.fingerprints.map((f) => [f.role, f] as const))('fallback fingerprint (%s)', async (_role, f) => {
    expect(await sha256Hex(fallbackFingerprintInput(f))).toBe(f.fingerprint);
  });
});

describe('golden captured envelopes', () => {
  it('capture of the real ChatGPT fixture matches packages/contracts/fixtures/captured-envelopes.json', async () => {
    const name = 'lightweight-complete.html';
    const url = fixtureUrl(name);
    const adapter = new ChatGPTAdapter({ document: domFrom(name, url).window.document, location: () => url });
    const sent: UnboundEnvelope[] = [];
    let n = 0;
    // Same adapter, but observe() performs one synchronous scan so the test needs no timers.
    const oneShot: ProviderAdapter = {
      provider: adapter.provider,
      version: adapter.version,
      isSupportedPage: (u) => adapter.isSupportedPage(u),
      getConversationExternalId: () => adapter.getConversationExternalId(),
      parseMessage: (el) => adapter.parseMessage(el),
      isAssistantStreaming: () => adapter.isAssistantStreaming(),
      observe: (onChange) => {
        adapter.scan().forEach(onChange);
        return () => {};
      },
    };
    const manager = new CaptureManager({
      adapter: oneShot,
      stableMs: 0,
      now: () => Date.parse('2026-01-15T10:00:00.000Z'),
      randomUUID: () => `7e57da7a-0000-4000-8000-${String(++n).padStart(12, '0')}`,
      sink: { enqueue: async (events) => (sent.push(...events), { ok: true }) },
    });
    manager.start();
    await manager.flushReady();
    manager.stop();

    expect(sent).toHaveLength(4);
    const golden = join(contracts, 'fixtures', 'captured-envelopes.json');
    const serialized = `${JSON.stringify(sent, null, 2)}\n`;
    if (process.env.UPDATE_GOLDEN === '1' || !existsSync(golden)) writeFileSync(golden, serialized);
    expect(serialized).toBe(readFileSync(golden, 'utf8'));
  });
});
