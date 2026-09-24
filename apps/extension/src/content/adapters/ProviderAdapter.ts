/**
 * ProviderAdapter contract (architecture section 6.3). Type definitions only:
 * P1 implements ChatGPTAdapter against this interface and registers a
 * content script for it. No capture code exists in P0.
 */
import type { SourceProvider } from '@skillmirror/contracts';

export type CaptureProvider = Exclude<SourceProvider, 'skillmirror'>;

export interface AttachmentMetadata {
  filename: string | null;
  mimeType: string | null;
  contentAvailable: boolean;
}

export interface CapturedMessage {
  externalConversationId: string | null;
  externalMessageId: string | null;
  externalParentMessageId: string | null;
  role: 'user' | 'assistant';
  contentText: string;
  contentFormat: 'text' | 'markdown';
  occurredAt: string | null;
  revisionIndex: number;
  attachmentMetadata: AttachmentMetadata[];
  contextIncomplete: boolean;
}

export interface ProviderAdapter {
  provider: CaptureProvider;
  isSupportedPage(url: string): boolean;
  getConversationExternalId(): string | null;
  /** Starts observing; returns a function that stops observing. */
  observe(onChange: (candidate: CapturedMessage) => void): () => void;
  parseMessage(element: Element): CapturedMessage | null;
  isAssistantStreaming(): boolean;
}
