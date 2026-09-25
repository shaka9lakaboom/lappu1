import {
  FEEDBACK_ACTIONS,
  FEEDBACK_NOTE_MAX_CHARS,
  FEEDBACK_TARGETS,
  FEEDBACK_VERDICTS,
  type FeedbackAction,
  type FeedbackRequest,
  type FeedbackTargetType,
  type FeedbackVerdict,
} from '@skillmirror/contracts';

import { isUuid } from './course-selection';

export const IDEMPOTENCY_KEY = /^[A-Za-z0-9._:-]{1,128}$/;

export type FeedbackFormResult =
  | { ok: true; request: FeedbackRequest; idempotencyKey: string }
  | { ok: false; error: string };

function text(value: FormDataEntryValue | null): string {
  return typeof value === 'string' ? value.trim() : '';
}

/**
 * Validates a correction / evaluation form the way POST /v1/feedback does. The idempotency key
 * is generated once per form instance, so a double submit or a retry records one correction.
 */
export function buildFeedbackRequest(formData: FormData): FeedbackFormResult {
  const action = text(formData.get('action')) as FeedbackAction;
  const targetType = text(formData.get('target_type')) as FeedbackTargetType;
  const targetId = text(formData.get('target_id'));
  const key = text(formData.get('idempotency_key'));
  const verdict = text(formData.get('verdict')) as FeedbackVerdict | '';
  const note = text(formData.get('note'));

  if (!FEEDBACK_ACTIONS.includes(action)) return { ok: false, error: 'Unknown feedback action.' };
  if (!FEEDBACK_TARGETS[action]?.includes(targetType)) return { ok: false, error: 'This correction does not apply here.' };
  if (!isUuid(targetId)) return { ok: false, error: 'Missing feedback target.' };
  if (!IDEMPOTENCY_KEY.test(key)) return { ok: false, error: 'Missing request key; reload the page and try again.' };
  if (verdict && !FEEDBACK_VERDICTS.includes(verdict)) return { ok: false, error: 'Unknown verdict.' };
  if (note.length > FEEDBACK_NOTE_MAX_CHARS) {
    return { ok: false, error: `The note must be at most ${FEEDBACK_NOTE_MAX_CHARS} characters.` };
  }
  if (action === 'EVALUATION' && !verdict && !note) return { ok: false, error: 'Choose an answer or write a note.' };

  const request: FeedbackRequest = { action, target_type: targetType, target_id: targetId };
  if (action === 'EVALUATION' && verdict) request.verdict = verdict;
  if (note) request.note = note;
  return { ok: true, request, idempotencyKey: key };
}
