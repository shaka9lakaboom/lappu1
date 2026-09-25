'use server';

import {
  VERIFICATION_ASSESSMENT_TYPES,
  type VerificationAssessmentType,
  type VerificationDetailResponse,
  type VerificationSubmissionResponse,
} from '@skillmirror/contracts';
import { refresh } from 'next/cache';
import { redirect } from 'next/navigation';

import { ApiError, apiRequest } from '@/lib/api';
import { isUuid } from '@/lib/course-selection';
import { requireApiSession } from '@/lib/session';
import { buildSubmission } from '@/lib/verification';

export type SubmissionFormState =
  | { status: 'idle' }
  | { status: 'done'; message: string }
  | { status: 'error'; message: string };

function sessionId(formData: FormData): string | null {
  const id = formData.get('session_id');
  return isUuid(id) ? id : null;
}

/** READY -> IN_PROGRESS (or resume). The backend makes no model call here. */
export async function startVerification(formData: FormData): Promise<void> {
  const id = sessionId(formData);
  if (!id) return;
  const { accessToken } = await requireApiSession(`/verifications/${id}`);
  try {
    await apiRequest<VerificationDetailResponse>(`/v1/verifications/${id}/start`, accessToken, { method: 'POST' });
  } catch (error) {
    // A 409 means it is no longer startable (e.g. already submitted): the page shows its state.
    if (!(error instanceof ApiError)) throw error;
  }
  redirect(`/verifications/${id}`);
}

/**
 * Stores the answer through POST /v1/verifications/{id}/submit with this form's idempotency key.
 * The backend saves it durably and grades it in the background; refresh() re-renders the page,
 * which then shows "Checking your answer" until the result exists.
 */
export async function submitVerification(
  _prev: SubmissionFormState,
  formData: FormData,
): Promise<SubmissionFormState> {
  const id = sessionId(formData);
  if (!id) return { status: 'error', message: 'Missing check id; reload the page.' };
  const kind = String(formData.get('assessment_type') ?? '') as VerificationAssessmentType;
  if (!VERIFICATION_ASSESSMENT_TYPES.includes(kind)) return { status: 'error', message: 'Unknown challenge type.' };
  const parsed = buildSubmission(formData, {
    assessment_type: kind,
    choices: String(formData.get('choice_keys') ?? '')
      .split(',')
      .filter(Boolean)
      .map((key) => ({ key, text: key })),
    max_response_chars: Number(formData.get('max_response_chars') ?? 0) || 4000,
  });
  if (!parsed.ok) return { status: 'error', message: parsed.error };

  const { accessToken } = await requireApiSession(`/verifications/${id}`);
  try {
    await apiRequest<VerificationSubmissionResponse>(`/v1/verifications/${id}/submit`, accessToken, {
      method: 'POST',
      body: parsed.request,
      headers: { 'Idempotency-Key': parsed.idempotencyKey },
    });
  } catch (error) {
    if (error instanceof ApiError) {
      // Unreachable API: the answer is kept in the form (and this browser's draft); retry is safe.
      return { status: 'error', message: `Your answer was not sent: ${error.message}` };
    }
    throw error;
  }
  refresh();
  return { status: 'done', message: 'Your answer is saved. SkillMirror is checking it now.' };
}

/** IN_PROGRESS -> ABANDONED. An unfinished check never counts against the learner. */
export async function abandonVerification(formData: FormData): Promise<void> {
  const id = sessionId(formData);
  if (!id) return;
  const { accessToken } = await requireApiSession(`/verifications/${id}`);
  try {
    await apiRequest<VerificationDetailResponse>(`/v1/verifications/${id}/abandon`, accessToken, { method: 'POST' });
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
  }
  refresh();
}
