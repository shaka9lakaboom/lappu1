import 'fake-indexeddb/auto';

import type { EventBatchRequest, RawActivityEnvelope } from '@skillmirror/contracts';
import { IDBFactory } from 'fake-indexeddb';
import { beforeEach, describe, expect, it } from 'vitest';

import { LocalQueue } from '../../src/capture/LocalQueue';
import type { StoredSession } from '../../src/background/auth';
import { SyncEngine, type SyncAuth } from '../../src/background/sync';

const ALICE = '00000000-0000-4000-8000-0000000000a1';
const BOB = '00000000-0000-4000-8000-0000000000b1';
let counter = 0;

function envelope(overrides: Partial<RawActivityEnvelope> = {}): RawActivityEnvelope {
  counter++;
  const id = `msg-${counter}`;
  return {
    event_id: `00000000-0000-4000-8000-${String(counter).padStart(12, '0')}`,
    schema_version: 1,
    learner_id: '',
    source_provider: 'chatgpt',
    source_method: 'browser_extension',
    external_conversation_id: 'conv-1',
    external_message_id: id,
    external_parent_message_id: null,
    message_index: counter,
    role: 'user',
    content_text: `message ${counter}`,
    content_format: 'text',
    occurred_at: null,
    captured_at: '2026-09-25T10:00:00.000Z',
    provider_model: null,
    revision_index: 0,
    attachment_metadata: [],
    context_incomplete: false,
    active_course_id: null,
    content_hash: 'a'.repeat(64),
    client_event_id: `chatgpt:${id}:r0`,
    ...overrides,
  };
}

let factory: IDBFactory;
beforeEach(() => {
  factory = new IDBFactory(); // a fresh "browser profile" per test
});

describe('LocalQueue', () => {
  it('persists across instances (browser / service worker restart)', async () => {
    const first = new LocalQueue(factory);
    await first.enqueue(ALICE, [envelope(), envelope()]);
    first.close();
    const second = new LocalQueue(factory);
    const items = await second.peek(ALICE, 10);
    expect(items).toHaveLength(2);
    expect(items.every((i) => i.envelope.learner_id === ALICE)).toBe(true);
  });

  it('keeps capture order and separates learners', async () => {
    const queue = new LocalQueue(factory);
    const [a, b, c] = [envelope(), envelope(), envelope()];
    await queue.enqueue(ALICE, [a, b]);
    await queue.enqueue(BOB, [c]);
    expect((await queue.peek(ALICE, 10)).map((i) => i.event_id)).toEqual([a.event_id, b.event_id]);
    expect((await queue.peek(BOB, 10)).map((i) => i.event_id)).toEqual([c.event_id]);
    expect(await queue.count(ALICE)).toBe(2);
    expect(await queue.count()).toBe(3);
  });

  it('does not queue the same dedup key twice, before or after acknowledgement', async () => {
    const queue = new LocalQueue(factory);
    const e = envelope();
    expect(await queue.enqueue(ALICE, [e, { ...e, event_id: '00000000-0000-4000-8000-00000000ffff' }])).toEqual({ added: 1, skipped: 1 });
    expect(await queue.enqueue(ALICE, [{ ...e, event_id: '00000000-0000-4000-8000-00000000fffe' }])).toEqual({ added: 0, skipped: 1 });
    await queue.acknowledge(await queue.peek(ALICE, 10));
    expect(await queue.count()).toBe(0);
    // Re-rendered history after a reload is not queued again.
    expect(await queue.enqueue(ALICE, [{ ...e, event_id: '00000000-0000-4000-8000-00000000fffd' }])).toEqual({ added: 0, skipped: 1 });
    // ...but the same key for another learner is a different event.
    expect(await queue.enqueue(BOB, [e])).toEqual({ added: 1, skipped: 0 });
  });

  it('quarantines rejected items out of the send path', async () => {
    const queue = new LocalQueue(factory);
    await queue.enqueue(ALICE, [envelope()]);
    const [item] = await queue.peek(ALICE, 1);
    await queue.quarantine(item, 'HTTP 422');
    expect(await queue.count()).toBe(0);
    expect(await queue.countQuarantined()).toBe(1);
  });
});

// --- SyncEngine ---------------------------------------------------------------

const API = 'http://api.test';
const session = (token = 'token-1'): StoredSession => ({
  access_token: token,
  refresh_token: 'refresh',
  expires_at: 9_999_999_999,
  user: { id: ALICE, email: 'alice@test.invalid' },
});

type Handler = (body: EventBatchRequest, headers: Headers) => Response | Promise<Response>;

function ackAll(body: EventBatchRequest): Response {
  return Response.json({
    correlation_id: 'c',
    accepted: body.events.length,
    duplicates: 0,
    results: body.events.map((e) => ({
      event_id: e.event_id,
      status: 'accepted',
      raw_message_id: '11111111-0000-4000-8000-000000000000',
      conversation_id: '22222222-0000-4000-8000-000000000000',
      revision_index: e.revision_index,
    })),
  });
}

function harness(handler: Handler, authOverrides: Partial<SyncAuth> = {}) {
  const queue = new LocalQueue(factory);
  const calls: EventBatchRequest[] = [];
  let now = 1_000_000;
  const fetchImpl = (async (url: string, init: RequestInit) => {
    expect(url).toBe(`${API}/v1/events/batch`);
    const body = JSON.parse(String(init.body)) as EventBatchRequest;
    calls.push(body);
    return handler(body, new Headers(init.headers));
  }) as unknown as typeof fetch;
  const auth: SyncAuth = {
    getSession: async () => session(),
    refresh: async () => session('token-2'),
    ...authOverrides,
  };
  const engine = new SyncEngine({
    queue,
    auth,
    apiUrl: API,
    client: { extension_version: '0.2.0', adapter_version: 'chatgpt-1' },
    fetchImpl,
    now: () => now,
    batchSize: 2,
  });
  return { queue, engine, calls, advance: (ms: number) => (now += ms) };
}

describe('SyncEngine', () => {
  it('sends authenticated batches and deletes only acknowledged items', async () => {
    const { queue, engine, calls } = harness((body, headers) => {
      expect(headers.get('Authorization')).toBe('Bearer token-1');
      return ackAll(body);
    });
    await queue.enqueue(ALICE, [envelope(), envelope(), envelope()]);
    expect(await engine.flush()).toBe('synced');
    expect(calls.map((c) => c.events.length)).toEqual([2, 1]);
    expect(calls[0].events.every((e) => e.learner_id === ALICE)).toBe(true);
    expect(await queue.count()).toBe(0);
    const state = await engine.state();
    expect(state.lastSync?.ok).toBe(true);
    expect(state.lastSuccessAt).not.toBeNull();
  });

  it('keeps everything when the backend is offline and backs off', async () => {
    let offline = true;
    const { queue, engine, calls, advance } = harness((body) => {
      if (offline) throw new TypeError('Failed to fetch');
      return ackAll(body);
    });
    await queue.enqueue(ALICE, [envelope(), envelope()]);
    expect(await engine.flush()).toBe('retry_later');
    expect(await queue.count()).toBe(2);
    expect(await engine.flush()).toBe('backoff'); // not retried immediately
    expect(calls).toHaveLength(1);

    advance(5_001);
    expect(await engine.flush()).toBe('retry_later'); // still offline, longer backoff
    expect((await engine.state()).consecutiveFailures).toBe(2);
    offline = false;
    advance(10_001);
    expect(await engine.flush()).toBe('synced');
    expect(await queue.count()).toBe(0);
    expect((await engine.state()).consecutiveFailures).toBe(0);
  });

  it.each([500, 503, 429, 403])('keeps items on HTTP %i', async (status) => {
    const { queue, engine } = harness(() => new Response('{"detail":"nope"}', { status }));
    await queue.enqueue(ALICE, [envelope()]);
    expect(await engine.flush()).toBe('retry_later');
    expect(await queue.count()).toBe(1);
    expect((await engine.state()).lastSync?.ok).toBe(false);
  });

  it('manual sync ignores backoff', async () => {
    let fail = true;
    const { queue, engine } = harness((body) => (fail ? new Response('', { status: 503 }) : ackAll(body)));
    await queue.enqueue(ALICE, [envelope()]);
    await engine.flush();
    fail = false;
    expect(await engine.flush()).toBe('backoff');
    expect(await engine.flush({ force: true })).toBe('synced');
  });

  it('refreshes the token once on 401', async () => {
    const { queue, engine, calls } = harness((body, headers) =>
      headers.get('Authorization') === 'Bearer token-2' ? ackAll(body) : new Response('', { status: 401 }),
    );
    await queue.enqueue(ALICE, [envelope()]);
    expect(await engine.flush()).toBe('synced');
    expect(calls).toHaveLength(2);
  });

  it('stops and keeps items when the session is no longer valid', async () => {
    const { queue, engine } = harness(() => new Response('', { status: 401 }), { refresh: async () => null });
    await queue.enqueue(ALICE, [envelope()]);
    expect(await engine.flush()).toBe('auth_required');
    expect(await queue.count()).toBe(1);
  });

  it('does nothing without a session', async () => {
    const { queue, engine, calls } = harness(ackAll, { getSession: async () => null });
    await queue.enqueue(ALICE, [envelope()]);
    expect(await engine.flush()).toBe('signed_out');
    expect(calls).toHaveLength(0);
    expect(await queue.count()).toBe(1);
  });

  it('isolates and quarantines a malformed event, delivering the rest', async () => {
    const bad = envelope({ content_text: 'bad' });
    const { queue, engine } = harness((body) =>
      body.events.some((e) => e.event_id === bad.event_id) ? new Response('{"detail":[]}', { status: 422 }) : ackAll(body),
    );
    await queue.enqueue(ALICE, [envelope(), bad, envelope()]);
    expect(await engine.flush()).toBe('synced');
    expect(await queue.count()).toBe(0);
    expect(await queue.countQuarantined()).toBe(1);
  });

  it('never deletes an event the API did not acknowledge', async () => {
    const { queue, engine } = harness((body) => {
      const response = ackAll({ ...body, events: body.events.slice(0, 1) });
      return response;
    });
    await queue.enqueue(ALICE, [envelope(), envelope()]);
    expect(await engine.flush()).toBe('retry_later');
    expect(await queue.count()).toBe(1);
  });

  it('treats a duplicate acknowledgement as stored', async () => {
    const { queue, engine } = harness((body) =>
      Response.json({
        correlation_id: 'c',
        accepted: 0,
        duplicates: body.events.length,
        results: body.events.map((e) => ({ event_id: e.event_id, status: 'duplicate', raw_message_id: 'x', conversation_id: 'y', revision_index: 0 })),
      }),
    );
    await queue.enqueue(ALICE, [envelope()]);
    expect(await engine.flush()).toBe('synced');
    expect(await queue.count()).toBe(0);
    expect((await engine.state()).lastSync?.duplicates).toBe(1);
  });

  it('runs one flush at a time', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const { queue, engine, calls } = harness(async (body) => {
      await gate;
      return ackAll(body);
    });
    await queue.enqueue(ALICE, [envelope()]);
    const first = engine.flush();
    const second = engine.flush();
    release();
    expect(await first).toBe('synced');
    expect(await second).toBe('synced');
    expect(calls).toHaveLength(1);
  });

  it('only sends the signed-in learner\'s queue', async () => {
    const { queue, engine, calls } = harness(ackAll);
    await queue.enqueue(BOB, [envelope()]);
    expect(await engine.flush()).toBe('idle');
    expect(calls).toHaveLength(0);
    expect(await queue.count(BOB)).toBe(1);
  });
});
