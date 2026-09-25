'use server';

import {
  CANDIDATE_REVIEW_ACTIONS,
  COURSE_MEMBER_ROLES,
  JOB_RETRY_MODES,
  type CandidateReviewAction,
  type CandidateReviewRequest,
  type CandidateReviewResponse,
  type CourseMemberAddResponse,
  type CourseMemberRole,
  type JobRetryMode,
  type JobRetryResponse,
} from '@skillmirror/contracts';
import { refresh } from 'next/cache';

import { ApiError, apiRequest } from '@/lib/api';
import { isUuid } from '@/lib/course-selection';
import { requireApiSession } from '@/lib/session';

export type AdminFormState =
  | { status: 'idle' }
  | { status: 'done'; message: string }
  | { status: 'error'; message: string };

const KEY = /^[A-Za-z0-9._:-]{1,128}$/;

function text(formData: FormData, name: string): string | null {
  const value = String(formData.get(name) ?? '').trim();
  return value ? value : null;
}

/** POST with the form's Idempotency-Key: a double submit replays the first result. */
async function mutate<T>(path: string, body: unknown, key: string | null, next: string): Promise<T | AdminFormState> {
  if (!key || !KEY.test(key)) return { status: 'error', message: 'This form expired; reload the page.' };
  const { accessToken } = await requireApiSession(next);
  try {
    return await apiRequest<T>(path, accessToken, { method: 'POST', body, headers: { 'Idempotency-Key': key } });
  } catch (error) {
    if (error instanceof ApiError) return { status: 'error', message: error.message };
    throw error;
  }
}

function isState(value: unknown): value is AdminFormState {
  return typeof value === 'object' && value !== null && 'status' in value;
}

export async function retryJob(_prev: AdminFormState, formData: FormData): Promise<AdminFormState> {
  const id = formData.get('job_id');
  const mode = String(formData.get('mode') ?? 'RETRY') as JobRetryMode;
  if (!isUuid(id) || !JOB_RETRY_MODES.includes(mode)) return { status: 'error', message: 'Unknown job.' };
  const result = await mutate<JobRetryResponse>(`/v1/admin/jobs/${id}/retry`, { mode }, text(formData, 'key'), '/admin/jobs');
  if (isState(result)) return result;
  refresh();
  const restored = result.stage_restored ? ` (resumes at ${result.stage_restored})` : '';
  return {
    status: 'done',
    message: `${result.replayed ? 'Already queued' : 'Queued again'}: ${result.job.state}${restored}.`,
  };
}

export async function reviewCandidate(_prev: AdminFormState, formData: FormData): Promise<AdminFormState> {
  const id = formData.get('candidate_id');
  const action = String(formData.get('action') ?? '') as CandidateReviewAction;
  if (!isUuid(id) || !CANDIDATE_REVIEW_ACTIONS.includes(action)) {
    return { status: 'error', message: 'Unknown candidate or action.' };
  }
  const body: CandidateReviewRequest = { action, note: text(formData, 'note') };
  if (action === 'MERGE') {
    const target = formData.get('target_skill_id');
    if (!isUuid(target)) return { status: 'error', message: 'Choose the skill to merge into.' };
    body.target_skill_id = target;
  }
  if (action === 'APPROVE') {
    body.description = text(formData, 'description');
    const band = Number(formData.get('difficulty_band') ?? '');
    if (band >= 1 && band <= 5) body.difficulty_band = band;
  }
  const result = await mutate<CandidateReviewResponse>(
    `/v1/admin/skill-candidates/${id}/review`,
    body,
    text(formData, 'key'),
    '/admin/skill-candidates',
  );
  if (isState(result)) return result;
  refresh();
  return {
    status: 'done',
    message: `${result.candidate.status}${result.skill ? `: ${result.skill.name}` : ''}${
      result.embed_job_id ? ' · indexing queued' : ''
    }.`,
  };
}

export async function enrollMember(_prev: AdminFormState, formData: FormData): Promise<AdminFormState> {
  const course = formData.get('course_id');
  const role = String(formData.get('role') ?? '') as CourseMemberRole;
  const email = text(formData, 'email');
  if (!isUuid(course) || !COURSE_MEMBER_ROLES.includes(role) || !email) {
    return { status: 'error', message: 'Give a course id, an e-mail address and a role.' };
  }
  const result = await mutate<CourseMemberAddResponse>(
    `/v1/admin/courses/${course}/members`,
    { email, role },
    text(formData, 'key'),
    '/admin',
  );
  if (isState(result)) return result;
  refresh();
  return {
    status: 'done',
    message: `${result.member.email ?? result.member.user_id} is ${result.created ? 'now' : 'already'} a ${result.member.role} member.`,
  };
}
