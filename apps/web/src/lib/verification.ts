/**
 * Plain-language view models for the Verification Center (P6).
 *
 * Product rules carried by this module:
 * - A check is a short, independent confirmation, not a test of the person: no failure colours.
 * - Incomplete is not failure: a check that was stopped, or could not be prepared or graded,
 *   is never shown as a wrong answer.
 * - The answer key never reaches the browser before grading (the API does not send it).
 * - A check the learner is no longer asked for (NOT_NEEDED: its skill lost the VERIFY / REVERIFY
 *   recommendation) is history: never a ready check, never a required action, never in a count.
 */
import type {
  Recommendation,
  VerificationAssessmentType,
  VerificationChallenge,
  VerificationSessionSummary,
  VerificationStatus,
  VerificationSubmissionRequest,
  VerificationsResponse,
} from '@skillmirror/contracts';

import type { Tone } from './experience';
import { IDEMPOTENCY_KEY } from './feedback';

export const STATUS: Record<VerificationStatus, { label: string; tone: Tone; detail: string }> = {
  PREPARING: {
    label: 'Being prepared',
    tone: 'neutral',
    detail: 'SkillMirror is writing a fresh challenge for this skill. It is usually ready within a minute.',
  },
  NOT_ISSUED: {
    label: 'Not available',
    tone: 'neutral',
    detail: 'No suitable challenge could be prepared this time. Nothing about your skills changed.',
  },
  READY: { label: 'Ready', tone: 'sky', detail: 'A short challenge is waiting for you.' },
  IN_PROGRESS: {
    label: 'In progress',
    tone: 'indigo',
    detail: 'You started this check. Your progress is kept; you can come back to it.',
  },
  EVALUATING: {
    label: 'Checking your answer',
    tone: 'indigo',
    detail: 'Your answer is saved and being checked.',
  },
  NEEDS_REVIEW: {
    label: 'Could not be graded',
    tone: 'neutral',
    detail: 'Your answer is saved, but it could not be graded reliably. It does not count against you.',
  },
  PASSED: { label: 'Passed', tone: 'emerald', detail: 'You showed this skill on your own.' },
  PARTIAL: {
    label: 'Partly there',
    tone: 'amber',
    detail: 'Part of the answer was right. This counts as partial evidence.',
  },
  NOT_PASSED: {
    label: 'Not yet',
    tone: 'amber',
    detail: 'This one did not work out yet. It is one piece of evidence, not a verdict.',
  },
  ABANDONED: {
    label: 'Stopped',
    tone: 'neutral',
    detail: 'You stopped this check. An unfinished check never counts against you.',
  },
  NOT_NEEDED: {
    label: 'No longer needed',
    tone: 'neutral',
    detail:
      'SkillMirror suggested this check earlier, but no longer needs it: the reason for it has gone. There is nothing to do; it stays here for your records.',
  },
};

/** Current checks: what the learner may be asked to do now (never a NOT_NEEDED session). */
export function currentCheckCount(response: Pick<VerificationsResponse, 'ready' | 'in_progress'>): number {
  return response.ready.length + response.in_progress.length;
}

export const ASSESSMENT_LABEL: Record<VerificationAssessmentType, string> = {
  mcq: 'Multiple choice',
  numeric: 'Number answer',
  short_response: 'Short answer',
  reasoning: 'Short explanation',
  code: 'Code',
  sql: 'SQL',
};

export const TRIGGER_TEXT: Record<string, string> = {
  REPEATED_DELEGATION_UNVERIFIED:
    'The AI has repeatedly done this skill for you, and SkillMirror has not seen you do it on your own yet.',
  VERIFICATION_STALE: 'Your last check of this skill was a while ago.',
  VERIFICATION_CONTRADICTED: 'Recent results disagree with your earlier check.',
};

export function triggerText(session: Pick<VerificationSessionSummary, 'reason_code'>): string {
  return TRIGGER_TEXT[session.reason_code] ?? 'SkillMirror suggested a short check of this skill.';
}

export function minutesLabel(minutes: number | null | undefined): string {
  if (!minutes) return '';
  return minutes === 1 ? 'about 1 minute' : `about ${minutes} minutes`;
}

/** Where the learner should go for a VERIFY / REVERIFY recommendation. */
export function verificationHref(rec: Pick<Recommendation, 'type' | 'verification_session_id'>): string | null {
  if (rec.type !== 'VERIFY' && rec.type !== 'REVERIFY') return null;
  return rec.verification_session_id ? `/verifications/${rec.verification_session_id}` : '/verifications';
}

export function verificationActionLabel(rec: Pick<Recommendation, 'type' | 'verification_state'>): string {
  if (rec.verification_state === 'IN_PROGRESS') return 'Continue the check';
  if (rec.verification_state === 'SUBMITTED') return 'See the check';
  if (rec.verification_state === 'PLANNED') return 'Open the Verification Center';
  return rec.type === 'REVERIFY' ? 'Start the fresh check' : 'Start the check';
}

/** True while background work (generation or grading) is expected to change the page soon. */
export function isWaiting(response: Pick<VerificationsResponse, 'preparing' | 'pending'>): boolean {
  return response.preparing.length > 0 || response.pending.length > 0;
}

export type SubmissionFormResult =
  | { ok: true; request: VerificationSubmissionRequest; idempotencyKey: string }
  | { ok: false; error: string };

function text(value: FormDataEntryValue | null): string {
  return typeof value === 'string' ? value.trim() : '';
}

/**
 * Builds the submission the way POST /v1/verifications/{id}/submit validates it. The idempotency
 * key is generated once per form instance, so a double click or a retry stores one submission.
 */
export function buildSubmission(
  formData: FormData,
  challenge: Pick<VerificationChallenge, 'assessment_type' | 'choices' | 'max_response_chars'>,
): SubmissionFormResult {
  const key = text(formData.get('idempotency_key'));
  if (!IDEMPOTENCY_KEY.test(key)) return { ok: false, error: 'Missing request key; reload the page and try again.' };
  if (challenge.assessment_type === 'mcq') {
    const selected = formData.getAll('selected').map((v) => String(v).trim().toUpperCase());
    const keys = new Set(challenge.choices.map((c) => c.key));
    if (selected.length === 0) return { ok: false, error: 'Choose an option first.' };
    if (selected.some((k) => !keys.has(k))) return { ok: false, error: 'Choose one of the listed options.' };
    return { ok: true, request: { selected: [...new Set(selected)].sort() }, idempotencyKey: key };
  }
  const answer = text(formData.get('answer'));
  if (!answer) return { ok: false, error: 'Write an answer first.' };
  if (answer.length > challenge.max_response_chars) {
    return { ok: false, error: `Keep the answer under ${challenge.max_response_chars} characters.` };
  }
  return { ok: true, request: { answer }, idempotencyKey: key };
}

/** The learner's stored answer as plain text (never rendered as HTML). */
export function responseText(response: Record<string, string | string[]> | null): string {
  if (!response) return '';
  const selected = response.selected;
  if (Array.isArray(selected)) return selected.join(', ');
  return typeof response.answer === 'string' ? response.answer : '';
}
