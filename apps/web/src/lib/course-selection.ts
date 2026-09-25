/**
 * The course the learner is currently *viewing*. A display preference only: it lives in a UI
 * cookie, never changes evidence provenance, and is separate from the extension's
 * active_course_id (which tags captured activity).
 */

export const COURSE_COOKIE = 'sm_course';
export const COURSE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: unknown): value is string {
  return typeof value === 'string' && UUID.test(value);
}

/**
 * The selected course id: an explicit `?course=` wins, then the cookie, then the first course.
 * Only one of the learner's own courses is ever returned; anything else is ignored.
 */
export function resolveSelectedCourse(
  courses: ReadonlyArray<{ id: string }>,
  cookieValue: string | undefined | null,
  queryValue?: string | string[] | undefined | null,
): string | null {
  const ids = new Set(courses.map((c) => c.id));
  const query = Array.isArray(queryValue) ? queryValue[0] : queryValue;
  for (const candidate of [query, cookieValue]) {
    if (isUuid(candidate) && ids.has(candidate)) return candidate;
  }
  return courses[0]?.id ?? null;
}
