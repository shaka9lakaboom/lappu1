import type { CourseListResponse } from '@skillmirror/contracts';
import Link from 'next/link';

import { AutoRefresh } from '@/components/auto-refresh';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, apiRequest } from '@/lib/api';
import { graphStatusLabel, isGraphInProgress } from '@/lib/courses';
import { requireApiSession } from '@/lib/session';

export default async function CoursesPage() {
  const { accessToken } = await requireApiSession('/courses');
  let courses: CourseListResponse['courses'] = [];
  let loadError: string | null = null;
  try {
    courses = (await apiRequest<CourseListResponse>('/v1/courses', accessToken)).courses;
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    loadError = error.message;
  }
  const anyInProgress = courses.some((c) => isGraphInProgress(c.graph_status));

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-6 py-12">
      {anyInProgress ? <AutoRefresh /> : null}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Courses</h1>
          <p className="text-sm text-muted-foreground">
            Each course gets a skill graph of specific, assessable skills from the shared registry.
          </p>
        </div>
        <div className="flex items-center gap-4 text-sm">
          <Link href="/dashboard" className="underline underline-offset-4">
            Dashboard
          </Link>
          <Link
            href="/courses/new"
            className="rounded-md bg-primary px-3 py-2 font-medium text-primary-foreground"
            data-testid="new-course-link"
          >
            New course
          </Link>
        </div>
      </header>

      {loadError ? (
        <p role="alert" className="text-sm text-destructive">
          Could not load courses: {loadError}
        </p>
      ) : courses.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-sm text-muted-foreground" data-testid="courses-empty">
            No courses yet. Create one to generate its skill graph.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2" data-testid="course-list">
          {courses.map((course) => (
            <Link key={course.id} href={`/courses/${course.id}`} data-testid="course-card">
              <Card className="h-full transition-colors hover:bg-muted/40">
                <CardHeader>
                  <CardTitle>{course.name}</CardTitle>
                  <CardDescription>{[course.level, course.subject].filter(Boolean).join(' · ') || 'Course'}</CardDescription>
                </CardHeader>
                <CardContent className="text-sm">
                  <div data-testid="course-graph-status">{graphStatusLabel(course)}</div>
                  <div className="text-muted-foreground">{course.skill_count} skills</div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
