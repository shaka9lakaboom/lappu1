/**
 * P9 (ADR 0008 H10): the recorded signed-in ChatGPT session.
 *
 * tests/fixtures/chatgpt/signed-in/recording.json is a session recorded frame by frame through the
 * structure recorder (scripts/record-chatgpt-structure.js, the DevTools snippet the owner runs on
 * the live page). These tests prove the recorder keeps structure and drops content, the committed
 * recording is reproducible, and the unchanged chatgpt-2 adapter + CaptureManager capture the
 * session correctly: user turn, streaming, completion, regeneration, an edit, an attachment, math,
 * and a degraded layout. A live recording (recording-live.json), when present, replays too.
 */
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { JSDOM } from 'jsdom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// @ts-expect-error - plain ESM build script without type declarations
import { buildRecording, PAGE_URL, recorderWindow, serialize } from '../../scripts/build-signed-in-recording.mjs';
import { CaptureManager, type UnboundEnvelope } from '../../src/capture/CaptureManager';
import { ChatGPTAdapter } from '../../src/content/adapters/ChatGPTAdapter';
import { captureStatus } from '../../src/content/captureStatus';

const here = dirname(fileURLToPath(import.meta.url));
const dir = join(here, '..', 'fixtures', 'chatgpt', 'signed-in');
const recorderSource = readFileSync(join(here, '..', '..', 'scripts', 'record-chatgpt-structure.js'), 'utf8');
const committed = readFileSync(join(dir, 'recording.json'), 'utf8').replace(/\r\n/g, '\n');
const recording = JSON.parse(committed) as { recorder: string; frames: { name: string; html: string }[] };
const PSEUDO = /^00000000-0000-4000-8000-\d{12}$/;
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
const digest = async (value: string) => createHash('sha256').update(value, 'utf8').digest('hex');

describe('the structure recorder keeps structure and drops content', () => {
  it('never reads cookies, storage or the network', () => {
    expect(recorderSource).not.toMatch(/document\.cookie|localStorage|sessionStorage|indexedDB|fetch\(|XMLHttpRequest|sendBeacon|WebSocket/);
  });

  it('replaces every word, id, file name, link, image and script', () => {
    const personal = `<!doctype html><html><body><main>
      <div data-turn-key="7d2c9a10-1111-4222-8333-944455556666" data-owner="jane.doe@example.com">
        <div data-chatgpt-search-unit-key="t:0:user" data-chatgpt-search-message-ids="7d2c9a10-1111-4222-8333-944455556666">
          <div data-testid="file-thumbnail" data-file-name="Jane_Doe_passport.pdf"></div>
          <div data-user-message-bubble="true"><div class="whitespace-pre-wrap">My name is Jane Doe
and my card is 4111 1111 1111 1111</div></div>
          <img src="https://files.oaiusercontent.com/file-abc?sig=SECRET_SIGNATURE" alt="Jane passport">
          <a href="https://chatgpt.com/share/secret-share-token?cookie=abc">link text</a>
          <button aria-label="Rename Jane Doe's tax return">x</button>
          <button aria-label="Copy message"></button><button aria-label="Stop streaming"></button>
          <script>window.sessionToken = "eyJhbGciOiJIUzI1NiJ9.secret";</script>
          <style>.x{background:url(https://tracker.example/pixel)}</style>
          <!-- a comment with Jane's address -->
        </div>
      </div></main></body></html>`;
    const window = recorderWindow(personal, 'https://chatgpt.com/c/7d2c9a10-1111-4222-8333-944455556666');
    const recorder = window.skillmirrorRecorder;
    const out: string = recorder.sanitize(window.document.querySelector('main'), recorder.createState({ salt: 'test' }));
    for (const secret of ['Jane', 'Doe', 'jane.doe', '4111', 'passport', 'SECRET_SIGNATURE', 'secret-share-token', 'sessionToken', 'eyJ', 'tracker.example', 'address', 'tax return', '7d2c9a10']) {
      expect(out).not.toContain(secret);
    }
    expect(out).not.toMatch(/\s(src|href|alt|srcset)=/);
    expect(out).not.toMatch(/<script|<style|<!--/);
    expect(out).toContain('data-file-name="file-1.pdf"');
    expect(out).toContain('aria-label="Copy message"');
    expect(out).toContain('aria-label="Stop streaming"');
    expect(out).toContain('data-owner="x"'); // an email-like data value is dropped
    for (const id of out.match(UUID) ?? []) expect(id).toMatch(PSEUDO);
    // Multi-line user text keeps its line break (pre-wrap): the adapter reads it as written.
    expect(out).toMatch(/whitespace-pre-wrap">\S+ \S+ \S+ \S+ \S+\n/);
  });

  it('keeps the same text the same across frames, and a different salt gives different words', () => {
    const window = recorderWindow();
    const recorder = window.skillmirrorRecorder;
    const main = window.document.querySelector('main');
    const state = recorder.createState({ salt: 'one' });
    expect(recorder.sanitize(main, state)).toBe(recorder.sanitize(main, state));
    expect(recorder.sanitize(main, recorder.createState({ salt: 'two' }))).not.toBe(recorder.sanitize(main, state));
  });
});

describe('the committed signed-in recording', () => {
  it('is reproducible from the recorder and the structure-from-live fixture', () => {
    expect(serialize(buildRecording())).toBe(committed);
    expect(recording.recorder).toBe('structure-recorder/1');
    expect(recording.frames.map((f) => f.name)).toEqual([
      'user-turn',
      'assistant-streaming',
      'assistant-complete',
      'regenerated',
      'edited-user',
      'attachment-user-turn',
      'math-streaming',
      'math-complete',
      'degraded',
    ]);
  });

  it('holds no text or id of the source page', () => {
    const source = readFileSync(join(here, '..', 'fixtures', 'chatgpt', 'thread-layout.html'), 'utf8');
    for (const phrase of ['What does len() return', 'len([1, 2, 3])', 'counts the keys', 'screenshot-of-my-notes', 'area of the circle', '\\pi r^2']) {
      expect(committed).not.toContain(phrase);
    }
    for (const id of new Set(source.match(UUID))) expect(committed).not.toContain(id);
    for (const id of committed.match(UUID) ?? []) expect(id).toMatch(PSEUDO);
  });
});

function replayer(firstFrame: string) {
  const dom = new JSDOM('<!doctype html><html><body><main></main></body></html>', { url: PAGE_URL });
  const doc = dom.window.document;
  doc.querySelector('main')!.outerHTML = firstFrame;
  const adapter = new ChatGPTAdapter({ document: doc, location: () => PAGE_URL, scanDelayMs: 100 });
  const sent: UnboundEnvelope[] = [];
  const manager = new CaptureManager({
    adapter,
    stableMs: 1000,
    digest,
    sink: { enqueue: async (events) => (sent.push(...events), { ok: true }) },
  });
  return { doc, adapter, sent, manager };
}

async function replay(frames: { name: string; html: string }[]) {
  const { doc, adapter, sent, manager } = replayer(frames[0].html);
  manager.start();
  const after: Record<string, UnboundEnvelope[]> = {};
  for (const [i, frame] of frames.entries()) {
    if (i > 0) doc.querySelector('main')!.outerHTML = frame.html;
    await vi.advanceTimersByTimeAsync(3000);
    after[frame.name] = [...sent];
  }
  manager.stop();
  return { after, adapter };
}

describe('the chatgpt-2 adapter replays the signed-in session', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-26T09:00:00Z'));
  });
  afterEach(() => vi.useRealTimers());

  it('captures each message once, when final, with revisions, attachments, math and degradation', async () => {
    const { after, adapter } = await replay(recording.frames);
    const [question] = after['user-turn'];
    expect(after['user-turn']).toHaveLength(1);
    expect(question).toMatchObject({ role: 'user', revision_index: 0, message_index: 0, external_parent_message_id: null });

    expect(after['assistant-streaming']).toHaveLength(1); // never while the answer streams
    const answer = after['assistant-complete'][1];
    expect(answer).toMatchObject({ role: 'assistant', external_parent_message_id: question.external_message_id, message_index: 1 });

    const regenerated = after['regenerated'][2];
    expect(regenerated.role).toBe('assistant');
    expect(regenerated.external_message_id).not.toBe(answer.external_message_id);
    expect(regenerated.external_parent_message_id).toBe(question.external_message_id);

    const edited = after['edited-user'][3];
    expect(edited).toMatchObject({ role: 'user', external_message_id: question.external_message_id, revision_index: 1 });
    expect(edited.content_text).not.toBe(question.content_text);

    const withFile = after['attachment-user-turn'][4];
    expect(withFile.role).toBe('user');
    expect(withFile.attachment_metadata).toEqual([expect.objectContaining({ filename: 'file-1.png', content_available: false })]);
    expect(withFile.context_incomplete).toBe(true);

    expect(after['math-streaming']).toHaveLength(5);
    const math = after['math-complete'][5];
    expect(math.role).toBe('assistant');
    expect(math.content_text.split('x^{2}').length - 1).toBe(1); // the TeX source, once
    expect(math.content_text).not.toMatch(/π|πr2/);

    // Degraded: nothing parses, nothing new is sent, and the popup shows the layout as unrecognised.
    expect(after['degraded']).toHaveLength(6);
    const parsed = adapter.scan().length;
    expect(parsed).toBe(0);
    expect(captureStatus({ conversationOpen: true, parsedMessages: parsed, pageAgeMs: 20_000 })).toBe('layout_unrecognized');
  });

  const live = join(dir, 'recording-live.json');
  it.skipIf(!existsSync(live))('replays the owner\'s live recording (recording-live.json)', async () => {
    const text = readFileSync(live, 'utf8');
    for (const id of text.match(UUID) ?? []) expect(id).toMatch(PSEUDO);
    expect(text).not.toMatch(/\s(src|href)=/);
    const liveRecording = JSON.parse(text) as { recorder: string; frames: { name?: string; html: string }[] };
    expect(liveRecording.recorder).toBe('structure-recorder/1');
    const frames = liveRecording.frames.map((f, i) => ({ name: f.name ?? `frame-${i}`, html: f.html }));
    const { after } = await replay(frames);
    const sent = after[frames.at(-1)!.name];
    expect(sent.some((e) => e.role === 'user')).toBe(true);
    expect(sent.some((e) => e.role === 'assistant')).toBe(true);
    // Every assistant message is paired with the question before it.
    for (const e of sent.filter((x) => x.role === 'assistant')) expect(e.external_parent_message_id).not.toBeNull();
  });
});
