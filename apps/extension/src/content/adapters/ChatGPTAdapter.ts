/**
 * ChatGPT adapter. Reads only the visible, rendered conversation on
 * chatgpt.com. It never intercepts network traffic, calls ChatGPT APIs, reads
 * tokens or cookies, or touches the page's JavaScript state.
 *
 * Two DOM shapes are recognised (fixtures in tests/fixtures/chatgpt/):
 *  - "lightweight" shell: `li[data-message-role]` whose `id` is the message
 *    id. While generating, the assistant item has `data-message-streaming` and
 *    a temporary `pending-...` id; `data-message-complete` marks it final.
 *  - "app" layout: `[data-message-author-role][data-message-id]`, streaming
 *    marked by `.result-streaming` or a visible stop button.
 */
import type { AttachmentMetadata } from '@skillmirror/contracts';

import type { CapturedMessage, ProviderAdapter } from './ProviderAdapter';
import { renderedText } from './renderedText';

export const CHATGPT_ADAPTER_VERSION = 'chatgpt-1';

const SUPPORTED_HOSTS = new Set(['chatgpt.com']);
// /c/<id> (signed in), /uc/<id> (signed out), optionally under /g/<gpt>/ or /project paths.
const CONVERSATION_PATH = /(?:^|\/)(?:c|uc)\/([A-Za-z0-9-]{8,128})(?:\/|$)/;
const MESSAGE_ID = /^[A-Za-z0-9._:-]{1,256}$/;
const MODEL_SLUG = /^[A-Za-z0-9._:/-]{1,100}$/;

const LIGHTWEIGHT_MESSAGE = 'li[data-message-role]';
const APP_MESSAGE = '[data-message-author-role]';
const STREAMING_MARKERS = [
  'li[data-message-role="assistant"][data-message-streaming]',
  '.result-streaming',
  '[data-testid="stop-button"]',
  'button[aria-label="Stop streaming"]',
].join(',');

const ATTACHMENT_MARKERS = [
  '[data-attachment]',
  '[data-file-name]',
  '[data-testid*="attachment" i]',
  '[data-testid*="file" i]',
  'a[download]',
].join(',');

const MIME = /^[a-z0-9.+-]{1,63}\/[a-z0-9.+-]{1,63}$/;
const EXTENSION_MIME: Record<string, string> = {
  pdf: 'application/pdf',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  txt: 'text/plain',
  csv: 'text/csv',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  py: 'text/x-python',
  ipynb: 'application/x-ipynb+json',
};

export interface ChatGPTAdapterOptions {
  document?: Document;
  location?: () => string;
  /** Debounce between a DOM mutation and the rescan it triggers. */
  scanDelayMs?: number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

function clean(value: string | null | undefined, pattern: RegExp): string | null {
  const trimmed = value?.trim();
  return trimmed && pattern.test(trimmed) ? trimmed : null;
}

function guessMime(filename: string | null): string | null {
  const ext = filename?.split('.').pop()?.toLowerCase();
  return (ext && EXTENSION_MIME[ext]) || null;
}

export class ChatGPTAdapter implements ProviderAdapter {
  readonly provider = 'chatgpt' as const;
  readonly version = CHATGPT_ADAPTER_VERSION;

  private readonly doc: Document;
  private readonly location: () => string;
  private readonly scanDelayMs: number;
  private readonly setTimer: (fn: () => void, ms: number) => unknown;
  private readonly clearTimer: (handle: unknown) => void;

  constructor(options: ChatGPTAdapterOptions = {}) {
    this.doc = options.document ?? document;
    this.location = options.location ?? (() => window.location.href);
    this.scanDelayMs = options.scanDelayMs ?? 300;
    this.setTimer = options.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = options.clearTimer ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));
  }

  isSupportedPage(url: string): boolean {
    try {
      const parsed = new URL(url);
      return parsed.protocol === 'https:' && SUPPORTED_HOSTS.has(parsed.hostname);
    } catch {
      return false;
    }
  }

  getConversationExternalId(): string | null {
    try {
      const match = CONVERSATION_PATH.exec(new URL(this.location()).pathname);
      return match ? match[1] : null;
    } catch {
      return null;
    }
  }

  isAssistantStreaming(): boolean {
    return this.doc.querySelector(STREAMING_MARKERS) !== null;
  }

  /** Message elements in rendered order, excluding templates and nested duplicates. */
  messageElements(): Element[] {
    const lightweight = Array.from(this.doc.querySelectorAll(LIGHTWEIGHT_MESSAGE)).filter(
      (el) => !el.hasAttribute('data-conversation-recovery') && !el.closest('[hidden], template'),
    );
    if (lightweight.length > 0) return lightweight;
    return Array.from(this.doc.querySelectorAll(APP_MESSAGE)).filter(
      (el) => !el.parentElement?.closest(APP_MESSAGE) && !el.closest('[hidden], template'),
    );
  }

  parseMessage(element: Element): CapturedMessage | null {
    const all = this.messageElements();
    const index = all.indexOf(element);
    if (index < 0) return null;
    return this.parseAt(all, index, this.getConversationExternalId(), this.isAssistantStreaming());
  }

  /** Parses every rendered message. Unparseable items are skipped, not guessed. */
  scan(): CapturedMessage[] {
    const all = this.messageElements();
    const conversationId = this.getConversationExternalId();
    const streaming = this.isAssistantStreaming();
    const messages: CapturedMessage[] = [];
    for (let index = 0; index < all.length; index++) {
      const parsed = this.parseAt(all, index, conversationId, streaming);
      if (parsed) messages.push(parsed);
    }
    // Parent = previous rendered message with a real id (the visible branch).
    for (let i = 0; i < messages.length; i++) {
      messages[i].externalParentMessageId = i > 0 ? messages[i - 1].externalMessageId : null;
    }
    return messages;
  }

  observe(onChange: (candidate: CapturedMessage) => void): () => void {
    let timer: unknown = null;
    const run = () => {
      timer = null;
      for (const message of this.scan()) onChange(message);
    };
    const schedule = () => {
      if (timer !== null) this.clearTimer(timer);
      timer = this.setTimer(run, this.scanDelayMs);
    };
    const view = this.doc.defaultView;
    const observer = new (view?.MutationObserver ?? MutationObserver)(schedule);
    observer.observe(this.doc.body ?? this.doc.documentElement, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['id', 'data-message-streaming', 'data-message-complete', 'class', 'data-message-id'],
    });
    schedule();
    return () => {
      observer.disconnect();
      if (timer !== null) this.clearTimer(timer);
      timer = null;
    };
  }

  private parseAt(
    all: Element[],
    index: number,
    conversationId: string | null,
    streaming: boolean,
  ): CapturedMessage | null {
    const element = all[index];
    const lightweight = element.hasAttribute('data-message-role');
    const role = element.getAttribute(lightweight ? 'data-message-role' : 'data-message-author-role');
    if (role !== 'user' && role !== 'assistant') return null;

    const rawId = lightweight ? element.id : element.getAttribute('data-message-id');
    const temporary = !rawId || rawId.startsWith('pending-');
    const externalMessageId = temporary ? null : clean(rawId, MESSAGE_ID);

    const content = this.contentRoot(element, role, lightweight);
    if (!content) return null;
    // User text is rendered pre-wrap by ChatGPT, so its line breaks are meaningful.
    const contentText = renderedText(content, { preformatted: role === 'user' });
    if (!contentText) return null;

    const final =
      role === 'user' ||
      (lightweight
        ? element.hasAttribute('data-message-complete') && !element.hasAttribute('data-message-streaming')
        : !content.classList.contains('result-streaming') &&
          !content.querySelector('.result-streaming') &&
          !(streaming && this.isLastAssistant(all, index)));

    const attachmentMetadata = role === 'user' ? this.attachments(element, content) : [];
    const modelHost = element.closest('[data-message-model-slug]') ?? element.querySelector('[data-message-model-slug]');

    return {
      externalConversationId: conversationId,
      externalMessageId,
      externalParentMessageId: null,
      messageIndex: index,
      role,
      contentText,
      contentFormat: 'text',
      occurredAt: null,
      providerModel: clean(modelHost?.getAttribute('data-message-model-slug'), MODEL_SLUG),
      revisionIndex: 0,
      attachmentMetadata,
      // Attachment contents are never captured; say so instead of pretending.
      contextIncomplete: attachmentMetadata.some((a) => !a.content_available),
      final: final && !temporary,
    };
  }

  private contentRoot(element: Element, role: 'user' | 'assistant', lightweight: boolean): Element | null {
    if (lightweight) {
      return element.querySelector(role === 'user' ? '[data-user-message-copy]' : '[data-assistant-markdown]');
    }
    if (role === 'assistant') return element.querySelector('.markdown, [data-message-content]') ?? element;
    return element.querySelector('.whitespace-pre-wrap, [data-message-content]') ?? element;
  }

  private isLastAssistant(all: Element[], index: number): boolean {
    for (let i = all.length - 1; i >= 0; i--) {
      const role = all[i].getAttribute('data-message-author-role') ?? all[i].getAttribute('data-message-role');
      if (role === 'assistant') return i === index;
    }
    return false;
  }

  /** Metadata for attachments visible on a user message. Contents are never read. */
  private attachments(element: Element, content: Element): AttachmentMetadata[] {
    const found: AttachmentMetadata[] = [];
    const seen = new Set<Element>();
    for (const marker of Array.from(element.querySelectorAll(ATTACHMENT_MARKERS))) {
      if (content.contains(marker) || seen.has(marker) || [...seen].some((s) => s.contains(marker))) continue;
      seen.add(marker);
      const filename =
        marker.getAttribute('data-file-name') ??
        marker.getAttribute('download') ??
        marker.getAttribute('aria-label') ??
        (marker.textContent?.trim() || null);
      const name = filename ? filename.slice(0, 255) : null;
      const declared = marker.getAttribute('data-mime-type');
      found.push({
        kind: 'file',
        filename: name,
        mime_type: clean(declared, MIME) ?? guessMime(name),
        content_available: false,
      });
    }
    for (const img of Array.from(element.querySelectorAll('img'))) {
      if (content.contains(img) || [...seen].some((s) => s.contains(img))) continue;
      const width = Number(img.getAttribute('width') ?? '0');
      if (width > 0 && width < 24) continue; // avatars / icons
      const alt = img.getAttribute('alt')?.trim() || null;
      found.push({ kind: 'image', filename: alt ? alt.slice(0, 255) : null, mime_type: null, content_available: false });
    }
    return found.slice(0, 20);
  }
}
