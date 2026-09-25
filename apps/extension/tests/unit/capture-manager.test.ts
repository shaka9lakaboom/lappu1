import { createHash } from 'node:crypto';

import { normalizeContent } from '@skillmirror/contracts';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CaptureManager, type UnboundEnvelope } from '../../src/capture/CaptureManager';
import { sha256Hex } from '../../src/capture/EventFingerprint';
import { ChatGPTAdapter } from '../../src/content/adapters/ChatGPTAdapter';
import type { CapturedMessage, ProviderAdapter } from '../../src/content/adapters/ProviderAdapter';
import { domFrom, fixtureMain } from './helpers';

const CONV = '6ab58b53-14f8-83ea-853a-57c0ab951696';
/** Resolves in a microtask, so fake timers can drive the whole pipeline. */
const digest = async (value: string) => createHash('sha256').update(value, 'utf8').digest('hex');

class FakeAdapter implements ProviderAdapter {
  readonly provider = 'chatgpt' as const;
  readonly version = 'fake-1';
  conversationId: string | null = CONV;
  streaming = false;
  private listener: ((c: CapturedMessage) => void) | null = null;
  isSupportedPage = () => true;
  getConversationExternalId = () => this.conversationId;
  parseMessage = () => null;
  isAssistantStreaming = () => this.streaming;
  observe(onChange: (c: CapturedMessage) => void) {
    this.listener = onChange;
    return () => {
      this.listener = null;
    };
  }
  emit(partial: Partial<CapturedMessage>) {
    this.listener?.({
      externalConversationId: this.conversationId,
      externalMessageId: 'msg-1',
      externalParentMessageId: null,
      messageIndex: 0,
      role: 'user',
      contentText: 'Explain binary search in one sentence.',
      contentFormat: 'text',
      occurredAt: null,
      providerModel: null,
      revisionIndex: 0,
      attachmentMetadata: [],
      contextIncomplete: false,
      final: true,
      ...partial,
    });
  }
}

function setup(options: { sinkOk?: () => boolean; stableMs?: number } = {}) {
  const adapter = new FakeAdapter();
  const sent: UnboundEnvelope[][] = [];
  let n = 0;
  const manager = new CaptureManager({
    adapter,
    stableMs: options.stableMs ?? 1000,
    conversationWaitMs: 5000,
    randomUUID: () => `00000000-0000-4000-8000-${String(++n).padStart(12, '0')}`,
    digest,
    sink: {
      enqueue: async (events) => {
        if (options.sinkOk && !options.sinkOk()) return { ok: false, reason: 'offline' };
        sent.push(events);
        return { ok: true };
      },
    },
  });
  const all = () => sent.flat();
  return { adapter, manager, sent, all };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-25T10:00:00Z'));
});
afterEach(() => vi.useRealTimers());

describe('CaptureManager', () => {
  it('builds a complete, self-consistent envelope', async () => {
    const { adapter, manager, all } = setup();
    manager.start();
    adapter.emit({});
    await vi.advanceTimersByTimeAsync(1000);
    const [event] = all();
    expect(event).toMatchObject({
      schema_version: 1,
      source_provider: 'chatgpt',
      source_method: 'browser_extension',
      external_conversation_id: CONV,
      external_message_id: 'msg-1',
      role: 'user',
      content_text: 'Explain binary search in one sentence.',
      revision_index: 0,
      captured_at: '2026-09-25T10:00:01.000Z',
      context_incomplete: false,
      client_event_id: 'chatgpt:msg-1:r0',
      active_course_id: null,
    });
    expect('learner_id' in event).toBe(false);
    expect(event.content_hash).toBe(await sha256Hex(normalizeContent(event.content_text)));
  });

  it('waits for the text to be stable before emitting a streamed answer once', async () => {
    const { adapter, manager, all } = setup();
    manager.start();
    const answer = 'Binary search halves a sorted range until the target is found.';
    for (let i = 10; i <= answer.length; i += 10) {
      adapter.emit({ externalMessageId: 'a-1', role: 'assistant', contentText: answer.slice(0, i), messageIndex: 1 });
      await vi.advanceTimersByTimeAsync(300);
    }
    adapter.emit({ externalMessageId: 'a-1', role: 'assistant', contentText: answer, messageIndex: 1 });
    await vi.advanceTimersByTimeAsync(999);
    expect(all()).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1100);
    expect(all().map((e) => e.content_text)).toEqual([answer]);
  });

  it('ignores assistant messages the provider still marks as streaming', async () => {
    const { adapter, manager, all } = setup();
    manager.start();
    adapter.emit({ externalMessageId: null, role: 'assistant', contentText: 'Binary', final: false });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(all()).toHaveLength(0);
  });

  it('does not re-emit on duplicate DOM mutations', async () => {
    const { adapter, manager, all } = setup();
    manager.start();
    for (let i = 0; i < 20; i++) {
      adapter.emit({});
      await vi.advanceTimersByTimeAsync(700);
    }
    await vi.advanceTimersByTimeAsync(5000);
    for (let i = 0; i < 5; i++) adapter.emit({});
    await vi.advanceTimersByTimeAsync(5000);
    expect(all()).toHaveLength(1);
  });

  it('emits a changed message as the next revision', async () => {
    const { adapter, manager, all } = setup();
    manager.start();
    adapter.emit({ contentText: 'First version' });
    await vi.advanceTimersByTimeAsync(1500);
    adapter.emit({ contentText: 'Second version' });
    await vi.advanceTimersByTimeAsync(1500);
    adapter.emit({ contentText: '  Second   version ' }); // whitespace-only change is not a revision
    await vi.advanceTimersByTimeAsync(1500);
    expect(all().map((e) => [e.content_text, e.revision_index, e.client_event_id])).toEqual([
      ['First version', 0, 'chatgpt:msg-1:r0'],
      ['Second version', 1, 'chatgpt:msg-1:r1'],
    ]);
  });

  it('emits in thread order', async () => {
    const { adapter, manager, all } = setup();
    manager.start();
    adapter.emit({ externalMessageId: 'm-2', messageIndex: 2, contentText: 'third' });
    adapter.emit({ externalMessageId: 'm-0', messageIndex: 0, contentText: 'first' });
    adapter.emit({ externalMessageId: 'm-1', messageIndex: 1, role: 'assistant', contentText: 'second' });
    await vi.advanceTimersByTimeAsync(1500);
    expect(all().map((e) => e.message_index)).toEqual([0, 1, 2]);
  });

  it('captures nothing while paused, including messages that finish after resuming', async () => {
    const { adapter, manager, all } = setup();
    manager.start({ paused: true });
    adapter.emit({ externalMessageId: 'u-1', messageIndex: 0, contentText: 'private question' });
    adapter.emit({ externalMessageId: null, messageIndex: 1, role: 'assistant', contentText: 'streaming...', final: false });
    await vi.advanceTimersByTimeAsync(5000);
    manager.resume();
    // The in-flight answer completes after resume: it belongs to the paused turn.
    adapter.emit({ externalMessageId: 'u-1', messageIndex: 0, contentText: 'private question' });
    adapter.emit({ externalMessageId: 'a-1', messageIndex: 1, role: 'assistant', contentText: 'private answer' });
    await vi.advanceTimersByTimeAsync(5000);
    expect(all()).toHaveLength(0);

    adapter.emit({ externalMessageId: 'u-2', messageIndex: 2, contentText: 'new question' });
    await vi.advanceTimersByTimeAsync(1500);
    expect(all().map((e) => e.content_text)).toEqual(['new question']);

    manager.pause();
    adapter.emit({ externalMessageId: 'u-3', messageIndex: 3, contentText: 'during second pause' });
    await vi.advanceTimersByTimeAsync(5000);
    expect(all()).toHaveLength(1);
    expect(manager.state).toBe('paused');
  });

  it('drops a pending capture when paused before it was emitted', async () => {
    const { adapter, manager, all } = setup();
    manager.start();
    adapter.emit({ contentText: 'typed just before pausing' });
    manager.pause();
    await vi.advanceTimersByTimeAsync(5000);
    manager.resume();
    await vi.advanceTimersByTimeAsync(5000);
    expect(all()).toHaveLength(0);
  });

  it('waits briefly for a new chat to get its conversation id', async () => {
    const { adapter, manager, all } = setup();
    adapter.conversationId = null;
    manager.start();
    adapter.emit({});
    await vi.advanceTimersByTimeAsync(2000);
    expect(all()).toHaveLength(0);
    adapter.conversationId = CONV;
    await vi.advanceTimersByTimeAsync(1100);
    expect(all()[0].external_conversation_id).toBe(CONV);
  });

  it('emits without a conversation id when the page never exposes one', async () => {
    const { adapter, manager, all } = setup();
    adapter.conversationId = null;
    manager.start();
    adapter.emit({});
    await vi.advanceTimersByTimeAsync(7000);
    expect(all()).toHaveLength(1);
    expect(all()[0].external_conversation_id).toBeNull();
  });

  it('keeps a capture pending when the worker cannot queue it, then retries', async () => {
    let online = false;
    const { adapter, manager, all } = setup({ sinkOk: () => online });
    manager.start();
    adapter.emit({});
    await vi.advanceTimersByTimeAsync(10_000);
    expect(all()).toHaveLength(0);
    expect(manager.pendingCount).toBe(1);
    online = true;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(all()).toHaveLength(1);
    expect(manager.pendingCount).toBe(0);
  });

  it('marks oversized captures as partial instead of dropping them', async () => {
    const { adapter, manager, all } = setup();
    manager.start();
    adapter.emit({ contentText: 'x'.repeat(100_050) });
    await vi.advanceTimersByTimeAsync(1500);
    expect(all()[0].content_text).toHaveLength(100_000);
    expect(all()[0].context_incomplete).toBe(true);
  });

  it('stop() detaches from the adapter', async () => {
    const { adapter, manager, all } = setup();
    manager.start();
    manager.stop();
    adapter.emit({});
    await vi.advanceTimersByTimeAsync(5000);
    expect(all()).toHaveLength(0);
    expect(manager.state).toBe('stopped');
  });
});

describe('CaptureManager + ChatGPTAdapter on the thread layout', () => {
  it('emits a streaming answer once, when final, and never duplicates re-rendered messages', async () => {
    const url = 'https://chatgpt.com/c/68d5a7f0-0000-4000-8000-00000000c0de';
    const doc = domFrom('thread-layout-streaming.html', url).window.document;
    const adapter = new ChatGPTAdapter({ document: doc, location: () => url, scanDelayMs: 100 });
    const sent: UnboundEnvelope[] = [];
    const manager = new CaptureManager({
      adapter,
      stableMs: 1000,
      digest,
      sink: { enqueue: async (events) => (sent.push(...events), { ok: true }) },
    });
    manager.start();
    await vi.advanceTimersByTimeAsync(3000);
    // While the second answer streams: both user messages and the finished first answer only.
    expect(sent.map((e) => [e.role, e.message_index])).toEqual([
      ['user', 0],
      ['assistant', 1],
      ['user', 2],
    ]);

    doc.querySelector('main')!.innerHTML = fixtureMain('thread-layout.html');
    await vi.advanceTimersByTimeAsync(3000);
    doc.querySelector('main')!.innerHTML = fixtureMain('thread-layout.html');
    await vi.advanceTimersByTimeAsync(3000);

    expect(sent.map((e) => [e.role, e.external_message_id, e.revision_index])).toEqual([
      ['user', '1f0c0a11-0000-4000-8000-000000000001', 0],
      ['assistant', '2a0c0a22-0000-4000-8000-000000000002', 0],
      ['user', '3b0c0a33-0000-4000-8000-000000000003', 0],
      ['assistant', '4c0c0a44-0000-4000-8000-000000000004', 0],
    ]);
    expect(sent[3].content_text).toBe('For a dict, len() counts the keys.');
    expect(sent[3].external_parent_message_id).toBe('3b0c0a33-0000-4000-8000-000000000003');
    expect(sent.every((e) => e.external_conversation_id === '68d5a7f0-0000-4000-8000-00000000c0de')).toBe(true);
    manager.stop();
  });
});

describe('CaptureManager + ChatGPTAdapter on real DOM transitions', () => {
  it('turns pending -> streaming -> complete into exactly four final records', async () => {
    const dom = domFrom('lightweight-pending.html', 'https://chatgpt.com/');
    let url = 'https://chatgpt.com/';
    const doc = dom.window.document;
    const adapter = new ChatGPTAdapter({ document: doc, location: () => url, scanDelayMs: 100 });
    const sent: UnboundEnvelope[] = [];
    const manager = new CaptureManager({
      adapter,
      stableMs: 1000,
      digest,
      sink: { enqueue: async (events) => (sent.push(...events), { ok: true }) },
    });
    manager.start();
    await vi.advanceTimersByTimeAsync(3000);
    // Only the user message is final so far, but the new chat has no id yet.
    expect(sent).toHaveLength(0);

    url = `https://chatgpt.com/uc/${CONV}`;
    doc.querySelector('main')!.innerHTML = fixtureMain('lightweight-streaming.html');
    await vi.advanceTimersByTimeAsync(3000);
    expect(sent.map((e) => e.role)).toEqual(['user', 'assistant', 'user']);

    doc.querySelector('main')!.innerHTML = fixtureMain('lightweight-complete.html');
    await vi.advanceTimersByTimeAsync(3000);
    // Re-rendering the same final DOM again must not duplicate anything.
    doc.querySelector('main')!.innerHTML = fixtureMain('lightweight-complete.html');
    await vi.advanceTimersByTimeAsync(3000);

    expect(sent.map((e) => [e.role, e.external_message_id, e.message_index, e.revision_index])).toEqual([
      ['user', '5f574d0d-14b5-4e87-a867-800b044bc4b3', 0, 0],
      ['assistant', 'da2ec96d-c994-4975-b05f-0b515cf917c1', 1, 0],
      ['user', '6cf347d5-a408-49da-a913-e0c4f2746f72', 2, 0],
      ['assistant', '9ff93fc4-5c8f-4247-a67c-03a2e529d06c', 3, 0],
    ]);
    expect(sent.every((e) => e.external_conversation_id === CONV)).toBe(true);
    expect(sent[3].content_text).toContain('print(i)');
    manager.stop();
  });
});
