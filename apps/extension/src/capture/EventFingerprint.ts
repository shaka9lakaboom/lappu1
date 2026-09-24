/**
 * Event identity (architecture section 6.6).
 *
 * preferred: (learner_id, provider, external_message_id, revision_index)
 * fallback:  SHA256(provider | conversation_id | role | normalized_content | coarse_timestamp)
 *
 * The client computes these only to avoid resending what it already sent.
 * The backend recomputes the fingerprint and enforces uniqueness itself;
 * learner_id is bound server-side from the verified token.
 */
import {
  fallbackFingerprintInput,
  normalizeContent,
  type MessageRole,
  type SourceProvider,
} from '@skillmirror/contracts';

/** SHA-256 hex digest; injectable so tests can run under fake timers. */
export type Digest = (value: string) => Promise<string>;

export async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function contentHash(text: string, digest: Digest = sha256Hex): Promise<string> {
  return digest(normalizeContent(text));
}

export interface FingerprintFields {
  source_provider: SourceProvider;
  external_conversation_id: string | null;
  external_message_id: string | null;
  role: MessageRole;
  content_text: string;
  occurred_at: string | null;
  captured_at: string;
  revision_index: number;
}

export function fallbackFingerprint(fields: FingerprintFields, digest: Digest = sha256Hex): Promise<string> {
  return digest(fallbackFingerprintInput(fields));
}

/** Client dedup key: `<provider>:<message id>:r<revision>`, or `fp:<fallback fingerprint>`. */
export async function clientEventId(fields: FingerprintFields, digest: Digest = sha256Hex): Promise<string> {
  if (fields.external_message_id) {
    return `${fields.source_provider}:${fields.external_message_id}:r${fields.revision_index}`;
  }
  return `fp:${await fallbackFingerprint(fields, digest)}`;
}
