/**
 * ProviderAdapter contract (architecture section 6.3). Adapters isolate DOM
 * selectors and provider-specific parsing, so a provider UI change needs only
 * one adapter update. They read the visible rendered page only: no network
 * interception, no provider APIs, no page JavaScript state.
 */
import type { AttachmentMetadata, SourceProvider } from '@skillmirror/contracts';

export type CaptureProvider = Exclude<SourceProvider, 'skillmirror'>;

export interface CapturedMessage {
  externalConversationId: string | null;
  /** Provider message id as rendered in the DOM; null when none is exposed (or only a temporary one). */
  externalMessageId: string | null;
  /** The previous message in the rendered thread, which is its parent in the visible branch. */
  externalParentMessageId: string | null;
  /** 0-based position in the rendered thread. */
  messageIndex: number;
  role: 'user' | 'assistant';
  contentText: string;
  contentFormat: 'text' | 'markdown';
  /** Providers render no per-message timestamps; null unless one is visible. */
  occurredAt: string | null;
  providerModel: string | null;
  revisionIndex: number;
  attachmentMetadata: AttachmentMetadata[];
  contextIncomplete: boolean;
  /** False while the provider still marks the message as being generated. */
  final: boolean;
}

export interface ProviderAdapter {
  provider: CaptureProvider;
  /** Changes whenever parsing logic changes; recorded with every captured event. */
  version: string;
  isSupportedPage(url: string): boolean;
  getConversationExternalId(): string | null;
  /** Starts observing; calls back with every parsed message after each DOM change. Returns a stop function. */
  observe(onChange: (candidate: CapturedMessage) => void): () => void;
  parseMessage(element: Element): CapturedMessage | null;
  isAssistantStreaming(): boolean;
}
