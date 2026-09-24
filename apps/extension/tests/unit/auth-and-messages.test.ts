import { describe, expect, it } from 'vitest';

import { SupabaseAuthClient, type SessionStore } from '../../src/background/auth';
import { isContentRequest, isPopupRequest } from '../../src/shared/messages';

function memoryStore(): SessionStore & { data: Map<string, unknown> } {
  const data = new Map<string, unknown>();
  return {
    data,
    getValue: async <T,>(key: string) => data.get(key) as T | undefined,
    setValue: async (key, value) => void (value === undefined ? data.delete(key) : data.set(key, value)),
  };
}

const token = (overrides: Record<string, unknown> = {}) =>
  Response.json({
    access_token: 'access-1',
    refresh_token: 'refresh-1',
    expires_in: 3600,
    user: { id: '00000000-0000-4000-8000-0000000000a1', email: 'a@test.invalid' },
    ...overrides,
  });

describe('SupabaseAuthClient', () => {
  it('signs in with the anon key and stores the session', async () => {
    const store = memoryStore();
    const requests: Array<{ url: string; init: RequestInit }> = [];
    const client = new SupabaseAuthClient('https://p.supabase.co', 'anon-key', store, (async (url: string, init: RequestInit) => {
      requests.push({ url, init });
      return token();
    }) as unknown as typeof fetch, () => 1000);
    const session = await client.signInWithPassword('a@test.invalid', 'pw');
    expect(requests[0].url).toBe('https://p.supabase.co/auth/v1/token?grant_type=password');
    expect(new Headers(requests[0].init.headers).get('apikey')).toBe('anon-key');
    expect(session).toMatchObject({ access_token: 'access-1', expires_at: 4600, user: { email: 'a@test.invalid' } });
    expect(await client.getSession()).toEqual(session);
  });

  it('surfaces a rejected sign-in without storing anything', async () => {
    const store = memoryStore();
    const client = new SupabaseAuthClient('https://p.supabase.co', 'k', store, (async () =>
      Response.json({ error_description: 'Invalid login credentials' }, { status: 400 })) as unknown as typeof fetch);
    await expect(client.signInWithPassword('a@test.invalid', 'bad')).rejects.toThrow('Invalid login credentials');
    expect(store.data.size).toBe(0);
  });

  it('refreshes near expiry, once for concurrent callers', async () => {
    const store = memoryStore();
    let now = 1000;
    let refreshes = 0;
    const client = new SupabaseAuthClient('https://p.supabase.co', 'k', store, (async (url: string) => {
      if (url.includes('refresh_token')) {
        refreshes++;
        return token({ access_token: `access-${refreshes + 1}`, refresh_token: `refresh-${refreshes + 1}` });
      }
      return token();
    }) as unknown as typeof fetch, () => now);
    await client.signInWithPassword('a@test.invalid', 'pw');
    now = 1000 + 3600 - 30; // inside the refresh margin
    const [a, b] = await Promise.all([client.getSession(), client.getSession()]);
    expect(refreshes).toBe(1);
    expect(a?.access_token).toBe('access-2');
    expect(b?.access_token).toBe('access-2');
  });

  it('ends the session when the refresh token is rejected, but not on a network error', async () => {
    const store = memoryStore();
    let mode: 'ok' | 'reject' | 'offline' = 'ok';
    const client = new SupabaseAuthClient('https://p.supabase.co', 'k', store, (async () => {
      if (mode === 'offline') throw new TypeError('Failed to fetch');
      if (mode === 'reject') return Response.json({ error: 'invalid_grant' }, { status: 400 });
      return token();
    }) as unknown as typeof fetch, () => 1000);
    await client.signInWithPassword('a@test.invalid', 'pw');
    mode = 'offline';
    await expect(client.refresh()).rejects.toThrow();
    expect(await client.current()).toBeDefined();
    mode = 'reject';
    expect(await client.refresh()).toBeNull();
    expect(await client.current()).toBeUndefined();
  });
});

describe('runtime message guards', () => {
  const event = {
    event_id: '00000000-0000-4000-8000-000000000001',
    schema_version: 1,
    source_provider: 'chatgpt',
    source_method: 'browser_extension',
    external_conversation_id: null,
    external_message_id: 'm',
    external_parent_message_id: null,
    message_index: 0,
    role: 'user',
    content_text: 'hi',
    content_format: 'text',
    occurred_at: null,
    captured_at: '2026-09-25T10:00:00.000Z',
    provider_model: null,
    revision_index: 0,
    attachment_metadata: [],
    context_incomplete: false,
    active_course_id: null,
    content_hash: 'a'.repeat(64),
    client_event_id: 'chatgpt:m:r0',
  };

  it('accepts well-formed captured events', () => {
    expect(isContentRequest({ type: 'CAPTURE_EVENTS', events: [event] })).toBe(true);
  });

  it.each([
    ['a content script naming a learner', { ...event, learner_id: '00000000-0000-4000-8000-0000000000b1' }],
    ['another provider', { ...event, source_provider: 'claude' }],
    ['a system role', { ...event, role: 'system' }],
    ['a string revision', { ...event, revision_index: '0' }],
  ])('rejects %s', (_label, bad) => {
    expect(isContentRequest({ type: 'CAPTURE_EVENTS', events: [bad] })).toBe(false);
  });

  it('rejects empty or unknown requests', () => {
    expect(isContentRequest({ type: 'CAPTURE_EVENTS', events: [] })).toBe(false);
    expect(isPopupRequest({ type: 'GET_TOKEN' })).toBe(false);
    expect(isPopupRequest({ type: 'SET_PAUSED', paused: 'yes' })).toBe(false);
    expect(isPopupRequest({ type: 'SET_PAUSED', paused: true })).toBe(true);
  });
});
