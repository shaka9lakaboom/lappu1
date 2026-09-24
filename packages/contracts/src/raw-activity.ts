/**
 * RawActivityEnvelope (architecture §6.5) and the ingestion API contract
 * (POST /v1/events/batch, architecture §13).
 *
 * Mirrors services/backend/app/ingestion/models.py and
 * schemas/raw-activity-envelope.schema.json. Change all three together.
 */
import type { SourceProvider } from './index';

export const RAW_ACTIVITY_SCHEMA_VERSION = 1 as const;

export const SOURCE_METHODS = ['browser_extension', 'native', 'manual'] as const;
export type SourceMethod = (typeof SOURCE_METHODS)[number];

export const MESSAGE_ROLES = ['user', 'assistant'] as const;
export type MessageRole = (typeof MESSAGE_ROLES)[number];

export const CONTENT_FORMATS = ['text', 'markdown'] as const;
export type ContentFormat = (typeof CONTENT_FORMATS)[number];

export const ATTACHMENT_KINDS = ['file', 'image', 'unknown'] as const;
export type AttachmentKind = (typeof ATTACHMENT_KINDS)[number];

/** Limits enforced by the backend; clients must stay within them. */
export const INGESTION_LIMITS = {
  maxEventsPerBatch: 50,
  maxContentChars: 100_000,
  maxAttachmentsPerEvent: 20,
  maxRevisionIndex: 10_000,
  maxMessageIndex: 100_000,
  maxBodyBytes: 6_000_000,
} as const;

/** What the page shows about an attachment. Content itself is never captured in V1. */
export interface AttachmentMetadata {
  kind: AttachmentKind;
  filename: string | null;
  mime_type: string | null;
  /** False in V1: SkillMirror records that an attachment exists, not its contents. */
  content_available: boolean;
}

export interface RawActivityEnvelope {
  event_id: string;
  schema_version: typeof RAW_ACTIVITY_SCHEMA_VERSION;
  /** Set by the extension from its session; the backend only accepts the token's subject. */
  learner_id: string;
  source_provider: SourceProvider;
  source_method: SourceMethod;
  external_conversation_id: string | null;
  external_message_id: string | null;
  external_parent_message_id: string | null;
  /**
   * P1 addition to the §6.5 envelope: 0-based position of the message in the
   * rendered thread, or null when unknown. Carries "message order" (§6.4).
   */
  message_index: number | null;
  role: MessageRole;
  content_text: string;
  content_format: ContentFormat;
  occurred_at: string | null;
  captured_at: string;
  provider_model: string | null;
  revision_index: number;
  attachment_metadata: AttachmentMetadata[];
  context_incomplete: boolean;
  active_course_id: string | null;
  /** SHA-256 hex of normalizeContent(content_text); recomputed and checked by the backend. */
  content_hash: string;
  /** Client-side dedup key from EventFingerprint. Stored as provenance, never trusted. */
  client_event_id: string;
}

export interface EventBatchClientInfo {
  extension_version: string;
  adapter_version: string;
}

export interface EventBatchRequest {
  client: EventBatchClientInfo;
  events: RawActivityEnvelope[];
}

export type IngestStatus = 'accepted' | 'duplicate';

export interface IngestResult {
  event_id: string;
  status: IngestStatus;
  raw_message_id: string;
  conversation_id: string;
  revision_index: number;
}

export interface EventBatchResponse {
  correlation_id: string;
  accepted: number;
  duplicates: number;
  results: IngestResult[];
}

export interface SyncStatusResponse {
  raw_message_count: number;
  last_received_at: string | null;
  pending_jobs: number;
}

/** Matches the `public.job_state` Postgres enum (architecture §7.3). */
export const JOB_STATES = ['PENDING', 'PROCESSING', 'COMPLETED', 'RETRY_WAIT', 'FAILED'] as const;
export type JobState = (typeof JOB_STATES)[number];

/**
 * Whitespace set shared with the Python implementation. Spelled out because
 * JavaScript `\s` and Python `\s` differ at the edges.
 */
export const WHITESPACE_CODE_POINTS: ReadonlySet<number> = new Set([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x20, 0xa0, 0x1680,
  0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a,
  0x2028, 0x2029, 0x202f, 0x205f, 0x3000, 0xfeff,
]);

/**
 * Canonical form used for content_hash and the fallback fingerprint:
 * Unicode NFC, every whitespace run collapsed to one space, trimmed.
 */
export function normalizeContent(text: string): string {
  let out = '';
  let pendingSpace = false;
  for (const ch of text.normalize('NFC')) {
    if (WHITESPACE_CODE_POINTS.has(ch.codePointAt(0)!)) {
      pendingSpace = out.length > 0;
      continue;
    }
    if (pendingSpace) out += ' ';
    pendingSpace = false;
    out += ch;
  }
  return out;
}

/** UTC hour bucket used as the fallback fingerprint's coarse timestamp: "YYYY-MM-DDTHH". */
export function coarseTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) throw new Error(`invalid timestamp: ${iso}`);
  return date.toISOString().slice(0, 13);
}

/** Pre-image of the §6.6 fallback fingerprint, hashed with SHA-256. */
export function fallbackFingerprintInput(fields: {
  source_provider: SourceProvider;
  external_conversation_id: string | null;
  role: MessageRole;
  content_text: string;
  occurred_at: string | null;
  captured_at: string;
}): string {
  return [
    fields.source_provider,
    fields.external_conversation_id ?? '',
    fields.role,
    normalizeContent(fields.content_text),
    coarseTimestamp(fields.occurred_at ?? fields.captured_at),
  ].join('|');
}
