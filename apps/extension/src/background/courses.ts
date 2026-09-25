/**
 * Active-course choice (architecture §12.2; ADR 0008 H12).
 *
 * The learner picks "Auto" (every course they study) or one of their courses in the popup. The
 * choice lives in the service worker's storage; content scripts never see or choose it. When a
 * capture is queued, the worker binds it to every envelope as `active_course_id`. The backend
 * re-validates it at processing time (only a STUDENT membership counts) and otherwise falls back
 * to all the learner's courses, so a stale choice can never widen what a turn is mapped against.
 */
import type { CourseListResponse } from '@skillmirror/contracts';

import type { UnboundEnvelope } from '../capture/CaptureManager';

export interface CourseChoice {
  id: string;
  name: string;
}

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_COURSES = 50;
const MAX_NAME = 120;

/** The learner's studied, active courses from GET /v1/courses (untrusted JSON, validated). */
export function parseCourses(body: unknown): CourseChoice[] {
  const courses = (body as Partial<CourseListResponse> | null)?.courses;
  if (!Array.isArray(courses)) return [];
  return courses
    .filter(
      (c) =>
        typeof c?.id === 'string' &&
        UUID.test(c.id) &&
        typeof c.name === 'string' &&
        c.role === 'STUDENT' &&
        c.status === 'ACTIVE',
    )
    .slice(0, MAX_COURSES)
    .map((c) => ({ id: c.id.toLowerCase(), name: c.name.slice(0, MAX_NAME) }));
}

export type FetchCoursesResult = { ok: true; courses: CourseChoice[] } | { ok: false; error: string };

export async function fetchCourses(
  apiUrl: string,
  accessToken: string,
  fetchImpl: typeof fetch = (...args) => fetch(...args),
): Promise<FetchCoursesResult> {
  try {
    const response = await fetchImpl(`${apiUrl.replace(/\/+$/, '')}/v1/courses`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (!response.ok) return { ok: false, error: `Could not load your courses (HTTP ${response.status}).` };
    return { ok: true, courses: parseCourses(await response.json()) };
  } catch {
    return { ok: false, error: 'Could not load your courses (offline?).' };
  }
}

/** The choice to keep: the stored course when it is still one of the learner's, else Auto. */
export function effectiveChoice(stored: unknown, courses: CourseChoice[]): string | null {
  return typeof stored === 'string' && courses.some((c) => c.id === stored) ? stored : null;
}

/** Envelopes as queued: the signed-in learner and the active course (null = Auto). */
export function bindEnvelopes(
  events: UnboundEnvelope[],
  learnerId: string,
  activeCourseId: string | null,
): Array<UnboundEnvelope & { learner_id: string }> {
  const course = typeof activeCourseId === 'string' && UUID.test(activeCourseId) ? activeCourseId : null;
  return events.map((event) => ({ ...event, learner_id: learnerId, active_course_id: course }));
}
