'use client';

import type { Course } from '@skillmirror/contracts';

/**
 * Chooses the course being *viewed* (a UI preference). It does not change the extension's
 * active course or any evidence. Works without JavaScript through the submit button.
 */
export function CourseSelector({
  courses,
  selected,
  returnTo,
  action,
}: {
  courses: Pick<Course, 'id' | 'name'>[];
  selected: string | null;
  returnTo: string;
  action: (formData: FormData) => void | Promise<void>;
}) {
  if (courses.length === 0) return null;
  return (
    <form action={action} className="flex items-center gap-2 text-sm" data-testid="course-selector">
      <input type="hidden" name="return_to" value={returnTo} />
      <label htmlFor="course-select" className="text-muted-foreground">
        Course
      </label>
      <select
        id="course-select"
        name="course_id"
        defaultValue={selected ?? undefined}
        onChange={(event) => event.currentTarget.form?.requestSubmit()}
        className="h-9 max-w-[16rem] truncate rounded-md border border-input bg-background px-2"
      >
        {courses.map((course) => (
          <option key={course.id} value={course.id}>
            {course.name}
          </option>
        ))}
      </select>
      <noscript>
        <button type="submit" className="h-9 rounded-md border px-3">
          Show
        </button>
      </noscript>
    </form>
  );
}
