import type { AdminCoursesResponse, AdminOverview, AdminSkillsResponse } from '@skillmirror/contracts';
import Link from 'next/link';

import { enrollMember } from '@/app/admin/actions';
import { ActionForm } from '@/components/areas/action-form';
import { AreaHeader, ForbiddenPanel } from '@/components/areas/area-header';
import { ErrorNotice } from '@/components/experience/states';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { JOB_STATE_ORDER, benchmarkLine, budgetLine, formKey } from '@/lib/admin';
import { formatTime } from '@/lib/experience';
import { loadArea } from '@/lib/roles';
import { requireApiSession } from '@/lib/session';

function Tile({ label, value, href, testId }: { label: string; value: string | number; href?: string; testId: string }) {
  const body = (
    <Card className={href ? 'h-full transition-colors hover:bg-muted/40' : 'h-full'}>
      <CardContent className="pt-6" data-testid={testId}>
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      </CardContent>
    </Card>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}

export default async function AdminPage({ searchParams }: PageProps<'/admin'>) {
  const { accessToken } = await requireApiSession('/admin');
  const q = (await searchParams).q;
  const query = typeof q === 'string' ? q.trim().slice(0, 200) : '';
  const loaded = await loadArea<AdminOverview>('/v1/admin/overview', accessToken);

  if (loaded.state !== 'ok') {
    return (
      <main className="mx-auto max-w-5xl space-y-8 px-4 py-10 sm:px-6">
        <AreaHeader area="admin" current="/admin" title="Admin" />
        {loaded.state === 'forbidden' ? (
          <ForbiddenPanel area="admin" />
        ) : (
          <ErrorNotice title="The overview could not be loaded" message={loaded.state === 'error' ? loaded.message : 'Not found.'} />
        )}
      </main>
    );
  }
  const overview = loaded.data;
  const [courses, skills] = query
    ? await Promise.all([
        loadArea<AdminCoursesResponse>(`/v1/admin/courses?q=${encodeURIComponent(query)}`, accessToken),
        loadArea<AdminSkillsResponse>(`/v1/admin/skills?q=${encodeURIComponent(query)}`, accessToken),
      ])
    : [null, null];

  return (
    <main className="mx-auto max-w-5xl space-y-8 px-4 py-10 sm:px-6">
      <AreaHeader
        area="admin"
        current="/admin"
        title="Admin"
        description="Pipeline operations. No captured text, prompts or model output are shown here."
      />

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" aria-label="Operations">
        <Tile label="Failed jobs" value={overview.jobs_by_state.FAILED ?? 0} href="/admin/jobs?state=FAILED" testId="tile-failed-jobs" />
        <Tile label="Failed and retryable" value={overview.retryable_failed} href="/admin/jobs?state=FAILED" testId="tile-retryable" />
        <Tile label="Skill candidates to review" value={overview.pending_candidates} href="/admin/skill-candidates" testId="tile-candidates" />
        <Tile label="Model failures (24 h)" value={overview.model_failures_24h} href="/admin/model-runs?failures_only=true" testId="tile-model-failures" />
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Jobs</CardTitle>
            <CardDescription>Processing jobs by state.</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-5 gap-2 text-center text-sm" data-testid="jobs-by-state">
              {JOB_STATE_ORDER.map((state) => (
                <div key={state}>
                  <dt className="text-[11px] text-muted-foreground">{state}</dt>
                  <dd className="font-semibold tabular-nums">{overview.jobs_by_state[state] ?? 0}</dd>
                </div>
              ))}
            </dl>
            {Object.keys(overview.failed_by_type).length ? (
              <p className="mt-3 text-xs text-muted-foreground">
                Failed by type:{' '}
                {Object.entries(overview.failed_by_type)
                  .map(([type, n]) => `${type} ${n}`)
                  .join(' · ')}
              </p>
            ) : null}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Model request budget</CardTitle>
            <CardDescription>Provider requests of the current quota day (cache hits excluded).</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1 text-sm" data-testid="budget">
              {overview.budget.map((b) => (
                <li key={`${b.provider}:${b.model}`} className="flex flex-wrap justify-between gap-2">
                  <span className="font-mono text-xs">{b.model}</span>
                  <span className="text-muted-foreground">{budgetLine(b)}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Benchmark</CardTitle>
            <CardDescription>The latest recorded intelligence gate run.</CardDescription>
          </CardHeader>
          <CardContent className="text-sm" data-testid="latest-benchmark">
            {overview.latest_benchmark ? (
              <p>
                <span className="font-medium">{overview.latest_benchmark.verdict}</span> ·{' '}
                {overview.latest_benchmark.mode.toLowerCase()} · {benchmarkLine(overview.latest_benchmark)} ·{' '}
                {formatTime(overview.latest_benchmark.finished_at)}{' '}
                <Link href="/admin/benchmark" className="underline underline-offset-4">
                  details
                </Link>
              </p>
            ) : (
              <p className="text-muted-foreground">No benchmark run recorded yet.</p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Totals</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-2 text-sm" data-testid="totals">
              <dt className="text-muted-foreground">Accounts</dt>
              <dd className="text-right tabular-nums">{overview.totals.learners}</dd>
              <dt className="text-muted-foreground">Courses</dt>
              <dd className="text-right tabular-nums">{overview.totals.courses}</dd>
              <dt className="text-muted-foreground">Active skills</dt>
              <dd className="text-right tabular-nums">{overview.totals.active_skills}</dd>
              <dt className="text-muted-foreground">Evidence events</dt>
              <dd className="text-right tabular-nums">{overview.totals.evidence_events}</dd>
            </dl>
          </CardContent>
        </Card>
      </section>

      <section className="space-y-3" aria-labelledby="lookup-heading">
        <h2 id="lookup-heading" className="text-lg font-semibold">
          Course and skill lookup
        </h2>
        <form className="flex gap-2" role="search" data-testid="lookup-form">
          <Input name="q" defaultValue={query} placeholder="Course or skill name, slug or id" aria-label="Search" />
          <button type="submit" className="h-9 rounded-md border px-4 text-sm">
            Search
          </button>
        </form>
        {courses?.state === 'ok' ? (
          <Card>
            <CardContent className="overflow-x-auto pt-6">
              <table className="w-full text-left text-sm" data-testid="lookup-courses">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Course</th>
                    <th className="px-2 py-1 font-medium">Graph</th>
                    <th className="px-2 py-1 text-right font-medium">Students</th>
                    <th className="px-2 py-1 text-right font-medium">Teachers</th>
                    <th className="py-1 pl-2 font-medium">Id</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {courses.data.courses.map((c) => (
                    <tr key={c.id}>
                      <td className="py-1 pr-3">{c.name}</td>
                      <td className="px-2 py-1 text-xs">
                        {c.graph_status} v{c.graph_version} · {c.skill_count} skills
                      </td>
                      <td className="px-2 py-1 text-right tabular-nums">{c.student_count}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{c.teacher_count}</td>
                      <td className="py-1 pl-2 font-mono text-[11px]">{c.id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {courses.data.courses.length === 0 ? <p className="text-sm text-muted-foreground">No course matches.</p> : null}
            </CardContent>
          </Card>
        ) : null}
        {skills?.state === 'ok' ? (
          <Card>
            <CardContent className="overflow-x-auto pt-6">
              <table className="w-full text-left text-sm" data-testid="lookup-skills">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Skill</th>
                    <th className="px-2 py-1 font-medium">Kind</th>
                    <th className="px-2 py-1 font-medium">Status</th>
                    <th className="px-2 py-1 text-right font-medium">Courses</th>
                    <th className="px-2 py-1 font-medium">Indexed</th>
                    <th className="py-1 pl-2 font-medium">Id</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {skills.data.skills.map((s) => (
                    <tr key={s.id}>
                      <td className="py-1 pr-3">{s.canonical_name}</td>
                      <td className="px-2 py-1 text-xs">{s.node_kind}</td>
                      <td className="px-2 py-1 text-xs">{s.status}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{s.course_count}</td>
                      <td className="px-2 py-1 text-xs">{s.embedded ? 'yes' : 'no'}</td>
                      <td className="py-1 pl-2 font-mono text-[11px]">{s.id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {skills.data.skills.length === 0 ? <p className="text-sm text-muted-foreground">No skill matches.</p> : null}
            </CardContent>
          </Card>
        ) : null}
      </section>

      <section className="space-y-3" aria-labelledby="enroll-heading">
        <h2 id="enroll-heading" className="text-lg font-semibold">
          Enroll a member
        </h2>
        <p className="text-sm text-muted-foreground">
          Adds an existing account to a course. A teacher needs a TEACHER or ADMIN profile, which the operator grants
          with <span className="font-mono text-xs">scripts/grant_role.py</span>.
        </p>
        <ActionForm action={enrollMember} hidden={{ key: formKey('enroll') }} submitLabel="Enroll" testId="enroll-form" variant="default">
          <Input name="course_id" placeholder="Course id" aria-label="Course id" className="w-80" required />
          <Input name="email" type="email" placeholder="E-mail" aria-label="E-mail" className="w-64" required />
          <select name="role" aria-label="Role" className="h-9 rounded-md border bg-background px-2 text-sm" defaultValue="STUDENT">
            <option value="STUDENT">Student</option>
            <option value="TEACHER">Teacher</option>
          </select>
        </ActionForm>
      </section>

      <section className="space-y-3" aria-labelledby="audit-heading">
        <h2 id="audit-heading" className="text-lg font-semibold">
          Recent admin actions
        </h2>
        {overview.recent_audit.length === 0 ? (
          <p className="text-sm text-muted-foreground">No admin action recorded yet.</p>
        ) : (
          <ul className="divide-y rounded-lg border text-sm" data-testid="recent-audit">
            {overview.recent_audit.map((a) => (
              <li key={a.id} className="flex flex-wrap justify-between gap-2 px-3 py-2">
                <span>
                  <span className="font-medium">{a.action}</span>{' '}
                  <span className="text-muted-foreground">
                    {a.entity_type} {a.entity_id.slice(0, 8)} · {a.actor_type === 'OPERATOR' ? 'operator' : a.actor_role}
                  </span>
                </span>
                <span className="text-xs text-muted-foreground">{formatTime(a.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
