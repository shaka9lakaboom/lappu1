'use server';

import type { CourseCreateResponse } from '@skillmirror/contracts';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import { ApiError, apiRequest } from '@/lib/api';
import { COURSE_COOKIE, COURSE_COOKIE_MAX_AGE, isUuid } from '@/lib/course-selection';
import { buildCourseRequest } from '@/lib/courses';
import { safeRedirectPath } from '@/lib/redirect';
import { requireApiSession } from '@/lib/session';

/**
 * Remembers which course the learner is viewing (a UI preference cookie). It never touches
 * evidence or the extension's active course; pages re-check that the id is one of the
 * learner's courses before using it.
 */
export async function selectCourse(formData: FormData): Promise<void> {
  await requireApiSession('/dashboard');
  const courseId = formData.get('course_id');
  if (!isUuid(courseId)) return;
  (await cookies()).set(COURSE_COOKIE, courseId, {
    path: '/',
    maxAge: COURSE_COOKIE_MAX_AGE,
    sameSite: 'lax',
    httpOnly: true,
  });
  // Back to the page without a stale ?course= (same-origin paths only).
  const returnTo = safeRedirectPath(formData.get('return_to'));
  redirect(returnTo);
}

export type CourseFormState = { error?: string } | undefined;

const IDEMPOTENCY_KEY = /^[A-Za-z0-9._:-]{1,128}$/;

/**
 * Creates the course through POST /v1/courses. The backend stores it and
 * enqueues the skill-graph bootstrap; generation happens asynchronously.
 */
export async function createCourse(_prev: CourseFormState, formData: FormData): Promise<CourseFormState> {
  const parsed = buildCourseRequest(formData);
  if (!parsed.ok) return { error: parsed.error };

  const { accessToken } = await requireApiSession('/courses/new');
  const key = String(formData.get('idempotency_key') ?? '');
  let courseId: string;
  try {
    const response = await apiRequest<CourseCreateResponse>('/v1/courses', accessToken, {
      method: 'POST',
      body: parsed.request,
      headers: IDEMPOTENCY_KEY.test(key) ? { 'Idempotency-Key': key } : {},
    });
    courseId = response.course.id;
  } catch (error) {
    if (error instanceof ApiError) return { error: `Could not create the course: ${error.message}` };
    throw error;
  }
  redirect(`/courses/${courseId}`);
}
