import type { TeacherCourseOverview } from '@skillmirror/contracts';
import { notFound } from 'next/navigation';

import { AreaHeader, ForbiddenPanel } from '@/components/areas/area-header';
import { EmptyState, ErrorNotice } from '@/components/experience/states';
import { StateBar } from '@/components/teacher/state-bar';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { isUuid } from '@/lib/course-selection';
import { formatTime } from '@/lib/experience';
import { loadArea } from '@/lib/roles';
import { requireApiSession } from '@/lib/session';
import { TEACHER_STATE_LABELS, cohortMessage, stateSegments } from '@/lib/teacher';

function Tile({ label, value, testId }: { label: string; value: number; testId: string }) {
  return (
    <div className="rounded-lg border p-3" data-testid={testId}>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

export default async function TeacherCoursePage({ params }: PageProps<'/teacher/courses/[id]'>) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const { accessToken } = await requireApiSession(`/teacher/courses/${id}`);
  const loaded = await loadArea<TeacherCourseOverview>(`/v1/teacher/courses/${id}/overview`, accessToken);
  if (loaded.state === 'not_found') notFound();

  if (loaded.state !== 'ok') {
    return (
      <main className="mx-auto max-w-5xl space-y-8 px-4 py-10 sm:px-6">
        <AreaHeader area="teacher" current={null} title="Course overview" />
        {loaded.state === 'forbidden' ? (
          <ForbiddenPanel area="teacher" />
        ) : (
          <ErrorNotice title="The course overview could not be loaded" message={loaded.message} />
        )}
      </main>
    );
  }

  const overview = loaded.data;
  const { course, cohort } = overview;
  return (
    <main className="mx-auto max-w-5xl space-y-8 px-4 py-10 sm:px-6">
      <AreaHeader
        area="teacher"
        current={null}
        title={course.name}
        description={[course.level, course.subject].filter(Boolean).join(' · ') || 'Course overview'}
      />

      <section
        className="rounded-lg border bg-muted/40 p-4 text-sm"
        aria-label="Class"
        data-testid="cohort-banner"
        data-suppressed={cohort.suppressed}
      >
        <p className="font-medium">{cohortMessage(cohort)}</p>
        <p className="mt-1 text-muted-foreground">
          SkillMirror counts evidence of the students&apos; own work. How much a student uses AI is not shown, and
          “{TEACHER_STATE_LABELS.UNKNOWN}” means there is too little evidence to say anything — it is not a low score.
        </p>
      </section>

      {cohort.suppressed || !overview.state_totals ? (
        <EmptyState title="Class views are hidden for small classes" testId="overview-suppressed">
          Aggregates appear once {cohort.min_cohort} students are enrolled.
        </EmptyState>
      ) : (
        <>
          <section className="space-y-3" aria-labelledby="distribution-heading">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="distribution-heading" className="text-lg font-semibold">
                Skill states across the class
              </h2>
              <p className="text-xs text-muted-foreground">
                {course.skill_count} skills × {cohort.student_count} students · as of {formatTime(overview.as_of)}
              </p>
            </div>
            <StateBar states={overview.state_totals} label="Skill states across the class" />
          </section>

          <section className="space-y-3" aria-labelledby="skills-heading">
            <h2 id="skills-heading" className="text-lg font-semibold">
              By skill
            </h2>
            <Card>
              <CardContent className="overflow-x-auto pt-6">
                <table className="w-full text-left text-sm" data-testid="teacher-skill-table">
                  <thead className="text-xs text-muted-foreground">
                    <tr>
                      <th className="py-2 pr-4 font-medium">Skill</th>
                      {stateSegments(overview.state_totals).map((s) => (
                        <th key={s.state} className="px-2 py-2 text-right font-medium" data-state={s.state}>
                          {s.label}
                        </th>
                      ))}
                      <th className="px-2 py-2 text-right font-medium">With own evidence</th>
                      <th className="py-2 pl-2 text-right font-medium">Check suggested</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {overview.skills.map((row) => (
                      <tr key={row.skill_id} data-testid="teacher-skill-row" data-skill-id={row.skill_id}>
                        <td className="py-2 pr-4">
                          <div className="font-medium">{row.name}</div>
                          {row.topic ? <div className="text-xs text-muted-foreground">{row.topic}</div> : null}
                        </td>
                        {stateSegments(row.states).map((s) => (
                          <td key={s.state} className="px-2 py-2 text-right tabular-nums" data-state={s.state}>
                            {s.count}
                          </td>
                        ))}
                        <td className="px-2 py-2 text-right tabular-nums">{row.students_with_evidence}</td>
                        <td className="py-2 pl-2 text-right tabular-nums">{row.verification_need_students}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          </section>

          <section className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Skills students worked on</CardTitle>
                <CardDescription>
                  Students whose learning activity involved the skill in the last{' '}
                  {overview.evidence_counts?.window_days ?? 30} days.
                </CardDescription>
              </CardHeader>
              <CardContent>
                {overview.common_mapped_skills.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No captured learning activity yet.</p>
                ) : (
                  <ul className="space-y-1 text-sm" data-testid="common-mapped-skills">
                    {overview.common_mapped_skills.map((s) => (
                      <li key={s.skill_id} className="flex justify-between gap-4">
                        <span>{s.name}</span>
                        <span className="tabular-nums text-muted-foreground">{s.students} students</span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Where a short check is suggested</CardTitle>
                <CardDescription>
                  Skills with open SkillMirror verification suggestions. A suggestion is an invitation to confirm the
                  skill, not a judgement.
                </CardDescription>
              </CardHeader>
              <CardContent>
                {overview.verification_needs.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No verification is suggested right now.</p>
                ) : (
                  <ul className="space-y-1 text-sm" data-testid="verification-needs">
                    {overview.verification_needs.map((s) => (
                      <li key={s.skill_id} className="flex justify-between gap-4">
                        <span>{s.name}</span>
                        <span className="tabular-nums text-muted-foreground">{s.students} students</span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </section>

          {overview.evidence_counts ? (
            <section className="space-y-3" aria-labelledby="evidence-heading">
              <h2 id="evidence-heading" className="text-lg font-semibold">
                Evidence of the students&apos; own work
              </h2>
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="evidence-counts">
                <Tile label="Independent work" value={overview.evidence_counts.independent} testId="evidence-independent" />
                <Tile label="Passed or attempted checks" value={overview.evidence_counts.verification} testId="evidence-verification" />
                <Tile
                  label={`Independent work, last ${overview.evidence_counts.window_days} days`}
                  value={overview.evidence_counts.window_independent}
                  testId="evidence-window"
                />
                <Tile label="Students with evidence" value={overview.evidence_counts.students_with_evidence} testId="evidence-students" />
              </dl>
            </section>
          ) : null}
        </>
      )}
    </main>
  );
}
