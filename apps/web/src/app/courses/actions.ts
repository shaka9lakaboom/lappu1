'use server';

import type { CourseCreateResponse } from '@skillmirror/contracts';
import { redirect } from 'next/navigation';

import { ApiError, apiRequest } from '@/lib/api';
import { buildCourseRequest } from '@/lib/courses';
import { requireApiSession } from '@/lib/session';

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
