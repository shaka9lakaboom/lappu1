/**
 * CaptureManager: turns adapter observations into RawActivityEnvelopes.
 *
 * - Emits a message once it is final (provider no longer marks it as
 *   streaming) and its text has been unchanged for `stableMs`, so a streaming
 *   answer becomes one record, not many partial ones.
 * - Ignores repeated DOM mutations that do not change a message's text.
 * - A later change to an already-emitted message becomes the next revision.
 * - Captures nothing while paused, including messages that appeared during
 *   the pause and finish after it.
 *
 * Runs in the content script. It never sees credentials: the service worker
 * binds learner_id and owns the durable queue.
 */
import {
  INGESTION_LIMITS,
  normalizeContent,
  RAW_ACTIVITY_SCHEMA_VERSION,
  type RawActivityEnvelope,
} from '@skillmirror/contracts';

import type { CapturedMessage, ProviderAdapter } from '../content/adapters/ProviderAdapter';
import { clientEventId, contentHash, sha256Hex, type Digest } from './EventFingerprint';

export type UnboundEnvelope = Omit<RawActivityEnvelope, 'learner_id'>;

export interface CaptureSink {
  /** Resolves ok only once the events are durably queued. */
  enqueue(events: UnboundEnvelope[]): Promise<{ ok: boolean; reason?: string }>;
}

export type CaptureState = 'stopped' | 'tracking' | 'paused';

export interface CaptureManagerOptions {
  adapter: ProviderAdapter;
  sink: CaptureSink;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
  randomUUID?: () => string;
  digest?: Digest;
  /** How long a final message's text must stay unchanged before it is emitted. */
  stableMs?: number;
  /** How long to wait for the page to expose a conversation id before emitting without one. */
  conversationWaitMs?: number;
  onStateChange?: (state: CaptureState) => void;
  onEmitted?: (events: UnboundEnvelope[]) => void;
}

interface Pending {
  candidate: CapturedMessage;
  normalized: string;
  changedAt: number;
  firstSeenAt: number;
}

export class CaptureManager {
  private _state: CaptureState = 'stopped';
  private stopObserving: (() => void) | null = null;
  private readonly pending = new Map<string, Pending>();
  private readonly emitted = new Map<string, { normalized: string; revision: number }>();
  /** Message ids seen while paused. */
  private readonly pausedIds = new Set<string>();
  /** Highest message index seen while paused, per conversation (null = no id yet). */
  private readonly pausedMaxIndex = new Map<string | null, number>();
  private timer: unknown = null;
  private flushing = false;
  private failures = 0;

  private readonly adapter: ProviderAdapter;
  private readonly sink: CaptureSink;
  private readonly now: () => number;
  private readonly setTimer: (fn: () => void, ms: number) => unknown;
  private readonly clearTimer: (handle: unknown) => void;
  private readonly randomUUID: () => string;
  private readonly digest: Digest;
  private readonly stableMs: number;
  private readonly conversationWaitMs: number;
  private readonly onStateChange?: (state: CaptureState) => void;
  private readonly onEmitted?: (events: UnboundEnvelope[]) => void;

  constructor(options: CaptureManagerOptions) {
    this.adapter = options.adapter;
    this.sink = options.sink;
    this.now = options.now ?? Date.now;
    this.setTimer = options.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = options.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
    this.randomUUID = options.randomUUID ?? (() => crypto.randomUUID());
    this.digest = options.digest ?? sha256Hex;
    this.stableMs = options.stableMs ?? 1500;
    this.conversationWaitMs = options.conversationWaitMs ?? 10_000;
    this.onStateChange = options.onStateChange;
    this.onEmitted = options.onEmitted;
  }

  get state(): CaptureState {
    return this._state;
  }

  get pendingCount(): number {
    return this.pending.size;
  }

  start({ paused = false }: { paused?: boolean } = {}): void {
    if (this._state !== 'stopped') return;
    this.setState(paused ? 'paused' : 'tracking');
    this.stopObserving = this.adapter.observe((candidate) => this.onCandidate(candidate));
  }

  stop(): void {
    this.stopObserving?.();
    this.stopObserving = null;
    this.cancelTimer();
    this.pending.clear();
    this.setState('stopped');
  }

  pause(): void {
    if (this._state !== 'tracking') return;
    this.cancelTimer();
    this.pending.clear();
    this.setState('paused');
  }

  resume(): void {
    if (this._state !== 'paused') return;
    this.setState('tracking');
  }

  private setState(state: CaptureState): void {
    if (state === this._state) return;
    this._state = state;
    this.onStateChange?.(state);
  }

  private keyOf(c: CapturedMessage): string {
    return c.externalMessageId ?? `idx:${c.externalConversationId ?? ''}:${c.messageIndex}:${c.role}`;
  }

  private seenWhilePaused(c: CapturedMessage): boolean {
    if (c.externalMessageId && this.pausedIds.has(c.externalMessageId)) return true;
    let max = this.pausedMaxIndex.get(c.externalConversationId);
    // A new chat started while paused gets its conversation id afterwards.
    if (max === undefined && c.externalConversationId !== null && this.pausedMaxIndex.has(null)) {
      max = this.pausedMaxIndex.get(null)!;
      this.pausedMaxIndex.set(c.externalConversationId, max);
    }
    return max !== undefined && c.messageIndex <= max;
  }

  private onCandidate(c: CapturedMessage): void {
    if (this._state === 'paused') {
      if (c.externalMessageId) this.pausedIds.add(c.externalMessageId);
      const max = this.pausedMaxIndex.get(c.externalConversationId) ?? -1;
      this.pausedMaxIndex.set(c.externalConversationId, Math.max(max, c.messageIndex));
      return;
    }
    if (this._state !== 'tracking' || !c.final) return;
    if (this.seenWhilePaused(c)) return;

    const key = this.keyOf(c);
    const normalized = normalizeContent(c.contentText);
    if (!normalized) return;
    if (this.emitted.get(key)?.normalized === normalized) return; // unchanged DOM re-render

    const now = this.now();
    const existing = this.pending.get(key);
    if (existing && existing.normalized === normalized) {
      existing.candidate = c; // e.g. the conversation id appeared meanwhile
    } else {
      this.pending.set(key, { candidate: c, normalized, changedAt: now, firstSeenAt: existing?.firstSeenAt ?? now });
    }
    this.schedule(this.stableMs);
  }

  private schedule(ms: number): void {
    if (this.timer !== null) return;
    this.timer = this.setTimer(() => {
      this.timer = null;
      void this.flushReady();
    }, ms);
  }

  private cancelTimer(): void {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
  }

  /** Emits every pending message that is final and stable. Exposed for tests. */
  async flushReady(): Promise<void> {
    if (this._state !== 'tracking' || this.flushing) return;
    this.flushing = true;
    try {
      const now = this.now();
      const pageConversationId = this.adapter.getConversationExternalId();
      const ready: Array<[string, Pending]> = [];
      for (const [key, entry] of this.pending) {
        if (now - entry.changedAt < this.stableMs) continue;
        if (
          entry.candidate.externalConversationId === null &&
          pageConversationId === null &&
          now - entry.firstSeenAt < this.conversationWaitMs
        ) {
          continue; // a brand-new chat usually gets its id within a second or two
        }
        ready.push([key, entry]);
      }
      if (ready.length === 0) return;
      ready.sort((a, b) => a[1].candidate.messageIndex - b[1].candidate.messageIndex);

      const built = await Promise.all(
        ready.map(async ([key, entry]) => {
          const revision = (this.emitted.get(key)?.revision ?? entry.candidate.revisionIndex - 1) + 1;
          return { key, entry, revision, envelope: await this.toEnvelope(entry, revision, pageConversationId, now) };
        }),
      );
      const result = await this.sink.enqueue(built.map((b) => b.envelope)).catch((error: unknown) => ({
        ok: false,
        reason: error instanceof Error ? error.message : String(error),
      }));
      if (!result.ok) {
        this.failures++;
        return;
      }
      this.failures = 0;
      for (const { key, entry, revision } of built) {
        this.emitted.set(key, { normalized: entry.normalized, revision });
        if (this.pending.get(key)?.normalized === entry.normalized) this.pending.delete(key);
      }
      this.onEmitted?.(built.map((b) => b.envelope));
    } finally {
      this.flushing = false;
      if (this._state === 'tracking' && this.pending.size > 0) {
        this.schedule(this.failures > 0 ? Math.min(this.stableMs * 2 ** this.failures, 60_000) : this.stableMs);
      }
    }
  }

  private async toEnvelope(
    entry: Pending,
    revision: number,
    pageConversationId: string | null,
    now: number,
  ): Promise<UnboundEnvelope> {
    const c = entry.candidate;
    let text = c.contentText;
    let incomplete = c.contextIncomplete;
    if (text.length > INGESTION_LIMITS.maxContentChars) {
      // Keep what fits and say the capture is partial rather than dropping it.
      text = text.slice(0, INGESTION_LIMITS.maxContentChars);
      incomplete = true;
    }
    const conversationId = c.externalConversationId ?? pageConversationId;
    const capturedAt = new Date(now).toISOString();
    const fields = {
      source_provider: this.adapter.provider,
      external_conversation_id: conversationId,
      external_message_id: c.externalMessageId,
      role: c.role,
      content_text: text,
      occurred_at: c.occurredAt,
      captured_at: capturedAt,
      revision_index: revision,
    };
    return {
      event_id: this.randomUUID(),
      schema_version: RAW_ACTIVITY_SCHEMA_VERSION,
      source_provider: this.adapter.provider,
      source_method: 'browser_extension',
      external_conversation_id: conversationId,
      external_message_id: c.externalMessageId,
      external_parent_message_id: c.externalParentMessageId,
      message_index: c.messageIndex,
      role: c.role,
      content_text: text,
      content_format: c.contentFormat,
      occurred_at: c.occurredAt,
      captured_at: capturedAt,
      provider_model: c.providerModel,
      revision_index: revision,
      attachment_metadata: c.attachmentMetadata.slice(0, INGESTION_LIMITS.maxAttachmentsPerEvent),
      context_incomplete: incomplete,
      active_course_id: null,
      content_hash: await contentHash(text, this.digest),
      client_event_id: await clientEventId(fields, this.digest),
    };
  }
}
