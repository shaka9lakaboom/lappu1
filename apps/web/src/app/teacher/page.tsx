import type { TeacherCoursesResponse } from '@skillmirror/contracts';
import Link from 'next/link';

import { AreaHeader, ForbiddenPanel } from '@/components/areas/area-header';
import { EmptyState, ErrorNotice } from '@/components/experience/states';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { graphStatusLabel } from '@/lib/courses';
import { loadArea } from '@/lib/roles';
import { requireApiSession } from '@/lib/session';

export default async function TeacherPage() {
  const { accessToken } = await requireApiSession('/teacher');
  const loaded = await loadArea<TeacherCoursesResponse>('/v1/teacher/courses', accessToken);

  return (
    <main className="mx-auto max-w-5xl space-y-8 px-4 py-10 sm:px-6">
      <AreaHeader
        area="teacher"
        current="/teacher"
        title="Teaching"
        description="Class-level views of the courses you teach. No individual student data is shown."
      />
      {loaded.state === 'forbidden' ? (
        <ForbiddenPanel area="teacher" />
      ) : loaded.state === 'error' ? (
        <ErrorNotice title="Your courses could not be loaded" message={loaded.message} />
      ) : loaded.state === 'not_found' || loaded.data.courses.length === 0 ? (
        <EmptyState title="You are not teaching a course yet" testId="teacher-no-courses">
          An administrator adds you to a course as its teacher.
        </EmptyState>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2" data-testid="teacher-courses">
          {loaded.data.courses.map((course) => (
            <Link
              key={course.id}
              href={`/teacher/courses/${course.id}`}
              data-testid="teacher-course-card"
              data-course-id={course.id}
            >
              <Card className="h-full transition-colors hover:bg-muted/40">
                <CardHeader>
                  <CardTitle className="text-base">{course.name}</CardTitle>
                  <CardDescription>
                    {[course.level, course.subject].filter(Boolean).join(' · ') || 'Course'}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-1 text-sm text-muted-foreground">
                  <p>
                    {course.student_count} {course.student_count === 1 ? 'student' : 'students'} ·{' '}
                    {course.skill_count} skills
                    {course.graph_status !== 'READY' ? ` · ${graphStatusLabel({ graph_status: course.graph_status, bootstrap_job_state: null })}` : ''}
                  </p>
                  {course.suppressed ? (
                    <p data-testid="teacher-course-suppressed">
                      Class views appear from {loaded.data.min_cohort} students.
                    </p>
                  ) : null}
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
