import type { Course, CourseSkillsResponse } from '@skillmirror/contracts';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { AutoRefresh } from '@/components/auto-refresh';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, apiRequest } from '@/lib/api';
import { graphStatusLabel, groupSkillsByTopic, importanceLabel, isGraphInProgress, prerequisiteNames } from '@/lib/courses';
import { requireApiSession } from '@/lib/session';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default async function CoursePage({ params }: PageProps<'/courses/[id]'>) {
  const { id } = await params;
  if (!UUID.test(id)) notFound();
  const { accessToken } = await requireApiSession(`/courses/${id}`);

  let course: Course;
  let graph: CourseSkillsResponse;
  try {
    [course, graph] = await Promise.all([
      apiRequest<Course>(`/v1/courses/${id}`, accessToken),
      apiRequest<CourseSkillsResponse>(`/v1/courses/${id}/skills`, accessToken),
    ]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    if (error instanceof ApiError) {
      return (
        <main className="mx-auto max-w-4xl px-6 py-12">
          <p role="alert" className="text-sm text-destructive">
            Could not load the course: {error.message}
          </p>
        </main>
      );
    }
    throw error;
  }

  const groups = groupSkillsByTopic(graph);
  const prerequisites = prerequisiteNames(graph);
  const inProgress = isGraphInProgress(course.graph_status);

  return (
    <main className="mx-auto max-w-4xl space-y-6 px-6 py-12">
      {inProgress ? <AutoRefresh /> : null}
      <Link href="/courses" className="text-sm underline underline-offset-4">
        Back to courses
      </Link>
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="course-name">
          {course.name}
        </h1>
        <p className="text-sm text-muted-foreground">
          {[course.level, course.subject].filter(Boolean).join(' · ') || 'Course'}
        </p>
        {/* Learner-supplied text: rendered as plain text only. */}
        {course.description ? <p className="whitespace-pre-line text-sm">{course.description}</p> : null}
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Skill graph</CardTitle>
          <CardDescription data-testid="graph-status" data-status={course.graph_status}>
            {graphStatusLabel(course)}
            {course.graph_status === 'READY'
              ? ` · version ${course.graph_version} · ${course.skill_count} skills in ${groups.filter((g) => g.topic).length} topics`
              : null}
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm">
          {course.graph_status === 'FAILED' ? (
            <p role="alert" className="text-destructive">
              {course.graph_error ?? 'Generation failed.'}
            </p>
          ) : inProgress ? (
            <p className="text-muted-foreground">
              This page updates automatically.
              {course.graph_error ? ` Last attempt: ${course.graph_error}` : null}
            </p>
          ) : null}
        </CardContent>
      </Card>

      {groups.map((group) => (
        <Card key={group.topic?.id ?? 'other'} data-testid="topic-group">
          <CardHeader>
            <CardTitle className="text-base">{group.topic?.canonical_name ?? 'Other skills'}</CardTitle>
            {group.topic ? <CardDescription>{group.topic.description}</CardDescription> : null}
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {group.skills.map(({ skill, importance, embedded }) => (
                <li key={skill.id} className="space-y-1 py-3" data-testid="skill-row" data-skill-id={skill.id}>
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="font-medium" data-testid="skill-name">
                      {skill.canonical_name}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {importanceLabel(importance)}
                      {skill.difficulty_band ? ` · difficulty ${skill.difficulty_band}/5` : ''}
                      {embedded ? '' : ' · not indexed yet'}
                    </span>
                  </div>
                  <p className="text-sm text-muted-foreground" data-testid="skill-description">
                    {skill.description}
                  </p>
                  {skill.aliases.length > 0 ? (
                    <p className="text-xs text-muted-foreground">Also known as: {skill.aliases.join(', ')}</p>
                  ) : null}
                  {prerequisites.get(skill.id)?.length ? (
                    <p className="text-xs text-muted-foreground">
                      Builds on: {prerequisites.get(skill.id)!.join(', ')}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ))}
    </main>
  );
}
