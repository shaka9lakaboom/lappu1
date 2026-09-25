/**
 * P8 fixture matrix on the chatgpt-2 thread layout (ADR 0008 H11). Every variant is derived from
 * thread-layout.html - the structure of the live signed-in page (synthetic text and ids) - the
 * way ChatGPT changes that page: a regenerated answer, an edited question, an attachment, rendered
 * math, a very long message and an unknown layout. The adapter and the CaptureManager run
 * unchanged on each.
 */
import { createHash } from 'node:crypto';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CaptureManager, type UnboundEnvelope } from '../../src/capture/CaptureManager';
import { CHATGPT_ADAPTER_VERSION, ChatGPTAdapter } from '../../src/content/adapters/ChatGPTAdapter';
import { captureStatus } from '../../src/content/captureStatus';
import { domFrom } from './helpers';

const URL = 'https://chatgpt.com/c/68d5a7f0-0000-4000-8000-00000000c0de';
const USER1 = '1f0c0a11-0000-4000-8000-000000000001';
const ANSWER1 = '2a0c0a22-0000-4000-8000-000000000002';
const REGENERATED = '2a0c0a22-0000-4000-8000-0000000000ff';
const digest = async (value: string) => createHash('sha256').update(value, 'utf8').digest('hex');

function thread() {
  const doc = domFrom('thread-layout.html', URL).window.document;
  const adapter = new ChatGPTAdapter({ document: doc, location: () => URL, scanDelayMs: 100 });
  return { doc, adapter };
}

function firstAnswer(doc: Document): Element {
  return doc.querySelector(`[data-chatgpt-selection-message-id="${ANSWER1}"]`)!;
}

function firstQuestionText(doc: Document): Element {
  return doc.querySelector('[data-turn-key] [data-user-message-bubble] .whitespace-pre-wrap')!;
}

/** A CaptureManager over the adapter; returns what reached the queue. */
function capture(adapter: ChatGPTAdapter) {
  const sent: UnboundEnvelope[] = [];
  const manager = new CaptureManager({
    adapter,
    stableMs: 1000,
    digest,
    sink: { enqueue: async (events) => (sent.push(...events), { ok: true }) },
  });
  manager.start();
  return { sent, manager };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-25T16:43:00Z'));
});
afterEach(() => vi.useRealTimers());

describe('chatgpt-2 on the signed-in thread layout: fixture matrix', () => {
  it('is the adapter version the backend records for these captures', () => {
    expect(CHATGPT_ADAPTER_VERSION).toBe('chatgpt-2');
  });

  it('captures a regenerated answer as a new message of the same question', async () => {
    const { doc, adapter } = thread();
    const { sent, manager } = capture(adapter);
    await vi.advanceTimersByTimeAsync(3000);
    expect(sent).toHaveLength(4);

    // "Regenerate": ChatGPT replaces the answer with a new message id under the same question.
    const unit = firstAnswer(doc).closest('[data-chatgpt-search-message-ids]')!;
    unit.setAttribute('data-chatgpt-search-message-ids', `${REGENERATED} ${REGENERATED}`);
    firstAnswer(doc).setAttribute('data-chatgpt-selection-message-id', REGENERATED);
    doc.querySelector(`[data-chatgpt-selection-message-id="${REGENERATED}"] p`)!.textContent =
      'It counts the items: len([1, 2, 3]) is 3.';
    await vi.advanceTimersByTimeAsync(3000);

    const regenerated = sent.filter((e) => e.external_message_id === REGENERATED);
    expect(regenerated).toHaveLength(1);
    expect(regenerated[0]).toMatchObject({
      role: 'assistant',
      external_parent_message_id: USER1,
      revision_index: 0,
      message_index: 1,
    });
    // The earlier answer is not re-sent or rewritten; the backend keeps both (latest pairs).
    expect(sent.filter((e) => e.external_message_id === ANSWER1)).toHaveLength(1);
    manager.stop();
  });

  it('captures an edited question as the next revision of that message', async () => {
    const { doc, adapter } = thread();
    const { sent, manager } = capture(adapter);
    await vi.advanceTimersByTimeAsync(3000);
    firstQuestionText(doc).textContent = 'What does len() return for a list of lists?';
    await vi.advanceTimersByTimeAsync(3000);
    const edits = sent.filter((e) => e.external_message_id === USER1);
    expect(edits.map((e) => [e.revision_index, e.content_text])).toEqual([
      [0, 'What does len() return for a list?'],
      [1, 'What does len() return for a list of lists?'],
    ]);
    manager.stop();
  });

  it('records an attachment as metadata only and flags the context as incomplete', () => {
    const { doc, adapter } = thread();
    const bubble = doc.querySelector('[data-turn-key] [data-user-message-bubble]')!;
    const file = doc.createElement('div');
    file.setAttribute('data-testid', 'file-thumbnail');
    file.setAttribute('data-file-name', 'screenshot.png');
    bubble.parentElement!.insertBefore(file, bubble);
    const [question] = adapter.scan();
    expect(question.contentText).toBe('What does len() return for a list?');
    expect(question.attachmentMetadata).toEqual([
      expect.objectContaining({ filename: 'screenshot.png', content_available: false }),
    ]);
    expect(question.contextIncomplete).toBe(true);
  });

  it('reads rendered math as its TeX source, once', () => {
    const { doc, adapter } = thread();
    firstAnswer(doc).querySelector('p')!.innerHTML =
      'The area is <span class="katex"><span class="katex-mathml"><math xmlns="http://www.w3.org/1998/Math/MathML">' +
      '<semantics><mrow><mi>π</mi><msup><mi>r</mi><mn>2</mn></msup></mrow>' +
      '<annotation encoding="application/x-tex">\\pi r^2</annotation></semantics></math></span>' +
      '<span class="katex-html" aria-hidden="true"><span class="mord">πr</span><span class="msupsub">2</span></span></span> for radius r.';
    const answer = adapter.scan()[1];
    expect(answer.contentText.split('\n')[0]).toBe('The area is \\pi r^2 for radius r.');
    expect(answer.contentText).not.toMatch(/πr2|π r 2/);
  });

  it('keeps a message over 100k characters as a partial capture, never drops it', async () => {
    const { doc, adapter } = thread();
    firstQuestionText(doc).textContent = 'loop '.repeat(21_000); // 105k characters
    const { sent, manager } = capture(adapter);
    await vi.advanceTimersByTimeAsync(3000);
    const question = sent.find((e) => e.external_message_id === USER1)!;
    expect(question.content_text).toHaveLength(100_000);
    expect(question.context_incomplete).toBe(true);
    manager.stop();
  });

  it('shows a layout it cannot read as degraded instead of capturing nothing silently', () => {
    const { doc, adapter } = thread();
    // A future ChatGPT release renames the unit markers: the turns stay, the markers go.
    for (const unit of Array.from(doc.querySelectorAll('[data-chatgpt-search-unit-key]'))) {
      unit.removeAttribute('data-chatgpt-search-unit-key');
      unit.removeAttribute('data-content-search-unit-key');
    }
    const parsed = adapter.scan().length;
    expect(parsed).toBe(0);
    expect(captureStatus({ conversationOpen: true, parsedMessages: parsed, pageAgeMs: 20_000 })).toBe(
      'layout_unrecognized',
    );
  });
});
