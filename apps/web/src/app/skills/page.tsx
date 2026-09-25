import type { CourseListResponse, CourseSkillsResponse, LedgerResponse } from '@skillmirror/contracts';
import { cookies } from 'next/headers';
import Link from 'next/link';
import type { ReactNode } from 'react';

import { selectCourse } from '@/app/courses/actions';
import { AutoRefresh } from '@/components/auto-refresh';
import { GraphWaitNotice } from '@/components/graph-wait-notice';
import { AppHeader } from '@/components/experience/app-header';
import { DebtBadge, MasteryBadge } from '@/components/experience/badges';
import { CourseSelector } from '@/components/experience/course-selector';
import { EmptyState, ErrorNotice, StateCountTiles } from '@/components/experience/states';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, apiRequest } from '@/lib/api';
import { COURSE_COOKIE, resolveSelectedCourse } from '@/lib/course-selection';
import { graphStatusLabel, importanceLabel, isGraphInProgress } from '@/lib/courses';
import { MASTERY, countStates, overlaySkillMap } from '@/lib/experience';
import { requireApiSession } from '@/lib/session';

export default async function SkillMapPage({ searchParams }: PageProps<'/skills'>) {
  const { accessToken } = await requireApiSession('/skills');
  const query = (await searchParams).course;
  const cookieValue = (await cookies()).get(COURSE_COOKIE)?.value;

  let courses: CourseListResponse['courses'];
  try {
    courses = (await apiRequest<CourseListResponse>('/v1/courses', accessToken)).courses;
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    return (
      <main className="mx-auto max-w-5xl space-y-6 px-4 py-10 sm:px-6">
        <AppHeader current="/skills" title="Skill map" />
        <ErrorNotice title="The skill map could not be loaded" message={error.message} />
      </main>
    );
  }
  const selected = resolveSelectedCourse(courses, cookieValue, query);
  const course = courses.find((c) => c.id === selected);

  let body: ReactNode;
  if (!course) {
    body = (
      <EmptyState title="No course yet" testId="skills-no-course">
        <Link href="/courses/new" className="underline underline-offset-4">
          Create a course
        </Link>{' '}
        and SkillMirror builds its skill map.
      </EmptyState>
    );
  } else if (course.graph_status !== 'READY') {
    body = (
      <EmptyState title={graphStatusLabel(course)} testId="skills-graph-pending">
        {isGraphInProgress(course.graph_status) ? (
          <>
            <AutoRefresh />
            <GraphWaitNotice course={course} />
            The skill map appears here as soon as it is ready. This page updates automatically.
          </>
        ) : (
          course.graph_error ?? 'Open the course to see what happened.'
        )}
      </EmptyState>
    );
  } else {
    let graph: CourseSkillsResponse;
    let ledger: LedgerResponse;
    try {
      [graph, ledger] = await Promise.all([
        apiRequest<CourseSkillsResponse>(`/v1/courses/${course.id}/skills`, accessToken),
        apiRequest<LedgerResponse>(`/v1/ledger?course_id=${course.id}`, accessToken),
      ]);
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      return (
        <main className="mx-auto max-w-5xl space-y-6 px-4 py-10 sm:px-6">
          <AppHeader current="/skills" title="Skill map" />
          <ErrorNotice title="The skill map could not be loaded" message={error.message} />
        </main>
      );
    }
    const groups = overlaySkillMap(graph, ledger.skills);
    body = (
      <>
        <StateCountTiles counts={countStates(ledger.skills)} />
        {groups.length === 0 ? (
          <EmptyState title="This course has no assessable skills yet" />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2" data-testid="skill-map">
            {groups.map((group) => (
              <Card key={group.topic?.id ?? 'other'} data-testid="topic-group">
                <CardHeader>
                  <CardTitle className="text-base">{group.topic?.canonical_name ?? 'Other skills'}</CardTitle>
                  {group.topic ? <CardDescription>{group.topic.description}</CardDescription> : null}
                </CardHeader>
                <CardContent>
                  <ul className="divide-y">
                    {group.skills.map(({ entry, state, ledger: row }) => (
                      <li key={entry.skill.id} data-testid="skill-map-row" data-skill-id={entry.skill.id} data-state={state}>
                        <Link
                          href={`/skills/${entry.skill.id}`}
                          className="flex flex-wrap items-center justify-between gap-2 py-2.5 hover:underline"
                        >
                          <span className="min-w-0 text-sm font-medium">{entry.skill.canonical_name}</span>
                          <span className="flex flex-wrap items-center gap-1.5">
                            {row?.debt_eligible ? <DebtBadge band={row.debt_band} /> : null}
                            <MasteryBadge state={state} />
                          </span>
                        </Link>
                        <p className="-mt-1 pb-2 text-xs text-muted-foreground">{importanceLabel(entry.importance)}</p>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </>
    );
  }

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-10 sm:px-6">
      <AppHeader
        current="/skills"
        title="Skill map"
        description={
          course
            ? `${course.name}: each topic and its skills, with what your independent evidence shows.`
            : 'Topics and skills of your courses.'
        }
      >
        <CourseSelector courses={courses} selected={selected} returnTo="/skills" action={selectCourse} />
      </AppHeader>
      <p className="text-xs text-muted-foreground">
        “{MASTERY.UNKNOWN.label}” is neutral: SkillMirror has not seen enough of your own work on that skill yet.
      </p>
      {body}
    </main>
  );
}
