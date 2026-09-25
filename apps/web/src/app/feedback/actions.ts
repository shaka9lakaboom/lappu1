'use server';

import type { FeedbackResponse } from '@skillmirror/contracts';
import { refresh } from 'next/cache';

import { ApiError, apiRequest } from '@/lib/api';
import { buildFeedbackRequest } from '@/lib/feedback';
import { requireApiSession } from '@/lib/session';

export type FeedbackFormState =
  | { status: 'idle' }
  | { status: 'done'; message: string }
  | { status: 'error'; message: string };

/**
 * Sends a correction or evaluation through POST /v1/feedback. The backend authorizes the target,
 * excludes the evidence (one-way), recomputes the ledger and refreshes recommendations before it
 * answers; refresh() then re-renders the page from that recomputed state.
 */
export async function submitFeedback(_prev: FeedbackFormState, formData: FormData): Promise<FeedbackFormState> {
  const parsed = buildFeedbackRequest(formData);
  if (!parsed.ok) return { status: 'error', message: parsed.error };

  const { accessToken } = await requireApiSession(String(formData.get('return_to') ?? '/activity'));
  let response: FeedbackResponse;
  try {
    response = await apiRequest<FeedbackResponse>('/v1/feedback', accessToken, {
      method: 'POST',
      body: parsed.request,
      headers: { 'Idempotency-Key': parsed.idempotencyKey },
    });
  } catch (error) {
    if (error instanceof ApiError) return { status: 'error', message: `Could not save: ${error.message}` };
    throw error;
  }
  refresh();
  const excluded = response.feedback.excluded_evidence_ids.length;
  if (parsed.request.action === 'EVALUATION') return { status: 'done', message: 'Thanks — your feedback was saved.' };
  return {
    status: 'done',
    message:
      excluded > 0
        ? `Done. ${excluded} evidence event${excluded === 1 ? ' is' : 's are'} no longer counted; your skills were recalculated.`
        : 'Done. Nothing from this activity is counted now.',
  };
}
