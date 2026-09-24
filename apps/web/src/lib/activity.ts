import type { JobState, MessageRole, SourceProvider } from '@skillmirror/contracts';

/** Row of `public.activity_feed` (migration 0002), readable by its owner through RLS. */
export interface ActivityRow {
  id: string;
  conversation_id: string;
  external_conversation_id: string | null;
  source_provider: SourceProvider;
  role: MessageRole;
  message_index: number | null;
  revision_index: number;
  captured_at: string;
  received_at: string;
  context_incomplete: boolean;
  preview: string;
  content_chars: number;
  processing_state: JobState | null;
  processing_attempts: number | null;
}

export const ACTIVITY_COLUMNS =
  'id, conversation_id, external_conversation_id, source_provider, role, message_index, revision_index, captured_at, received_at, context_incomplete, preview, content_chars, processing_state, processing_attempts';

const PROVIDER_LABELS: Record<SourceProvider, string> = {
  chatgpt: 'ChatGPT',
  claude: 'Claude',
  gemini: 'Gemini',
  skillmirror: 'SkillMirror',
};

export function providerLabel(provider: SourceProvider): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

/** Processing status in plain words. Nothing here interprets skills: that starts in P3. */
export function processingLabel(state: JobState | null): string {
  switch (state) {
    case 'PENDING':
      return 'Waiting for processing';
    case 'PROCESSING':
      return 'Processing';
    case 'RETRY_WAIT':
      return 'Retrying';
    case 'COMPLETED':
      return 'Processed';
    case 'FAILED':
      return 'Processing failed';
    default:
      return 'Not queued';
  }
}

/** One-line preview; rendered as text by React, never as HTML. */
export function shortPreview(preview: string, totalChars: number, max = 160): string {
  const flat = preview.replace(/\s+/g, ' ').trim();
  const truncated = flat.length > max || totalChars > preview.length;
  return truncated ? `${flat.slice(0, max).trimEnd()}…` : flat;
}

export function shortId(id: string | null): string {
  return id ? id.slice(0, 8) : '—';
}
