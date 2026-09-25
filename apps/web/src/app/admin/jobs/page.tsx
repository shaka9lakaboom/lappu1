import { JOB_STATES, type AdminJobsResponse, type JobState } from '@skillmirror/contracts';
import Link from 'next/link';

import { retryJob } from '@/app/admin/actions';
import { ActionForm } from '@/components/areas/action-form';
import { AreaHeader, ForbiddenPanel } from '@/components/areas/area-header';
import { EmptyState, ErrorNotice } from '@/components/experience/states';
import { Card, CardContent } from '@/components/ui/card';
import { formKey, retryAction } from '@/lib/admin';
import { formatTime } from '@/lib/experience';
import { loadArea } from '@/lib/roles';
import { requireApiSession } from '@/lib/session';
import { cn } from '@/lib/utils';

const JOB_TYPES = [
  'PROCESS_RAW_MESSAGE',
  'BOOTSTRAP_COURSE_GRAPH',
  'GENERATE_VERIFICATION',
  'GRADE_VERIFICATION',
  'EMBED_SKILL',
] as const;

function one(value: string | string[] | undefined): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

export default async function AdminJobsPage({ searchParams }: PageProps<'/admin/jobs'>) {
  const params = await searchParams;
  const state = JOB_STATES.find((s) => s === one(params.state)) as JobState | undefined;
  const jobType = JOB_TYPES.find((t) => t === one(params.job_type));
  const before = one(params.before);
  const { accessToken } = await requireApiSession('/admin/jobs');
  const query = new URLSearchParams({ limit: '50' });
  if (state) query.set('state', state);
  if (jobType) query.set('job_type', jobType);
  if (before && !Number.isNaN(Date.parse(before))) query.set('before', before);
  const loaded = await loadArea<AdminJobsResponse>(`/v1/admin/jobs?${query}`, accessToken);

  const tab = (label: string, target: JobState | undefined) => {
    const next = new URLSearchParams();
    if (target) next.set('state', target);
    if (jobType) next.set('job_type', jobType);
    return (
      <Link
        href={`/admin/jobs${next.size ? `?${next}` : ''}`}
        aria-current={state === target ? 'page' : undefined}
        className={cn('rounded-md px-3 py-1.5 text-sm', state === target ? 'bg-muted font-medium' : 'text-muted-foreground')}
      >
        {label}
      </Link>
    );
  };

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-4 py-10 sm:px-6">
      <AreaHeader
        area="admin"
        current="/admin/jobs"
        title="Processing jobs"
        description="A retry re-queues a failed job with a fresh attempt budget (at most 5 times). Errors are redacted."
      />
      {loaded.state === 'forbidden' ? (
        <ForbiddenPanel area="admin" />
      ) : loaded.state !== 'ok' ? (
        <ErrorNotice title="Jobs could not be loaded" message={loaded.state === 'error' ? loaded.message : 'Not found.'} />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <nav className="flex gap-1" aria-label="Job state">
              {tab('All', undefined)}
              {tab(`Failed (${loaded.data.counts.FAILED ?? 0})`, 'FAILED')}
              {tab(`Pending (${loaded.data.counts.PENDING ?? 0})`, 'PENDING')}
              {tab(`Waiting to retry (${loaded.data.counts.RETRY_WAIT ?? 0})`, 'RETRY_WAIT')}
            </nav>
            <form className="flex items-center gap-2 text-sm" data-testid="job-type-filter">
              {state ? <input type="hidden" name="state" value={state} /> : null}
              <select name="job_type" defaultValue={jobType ?? ''} aria-label="Job type" className="h-8 rounded-md border bg-background px-2">
                <option value="">All types</option>
                {JOB_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
              <button type="submit" className="h-8 rounded-md border px-3">
                Filter
              </button>
            </form>
          </div>
          {loaded.data.jobs.length === 0 ? (
            <EmptyState title="No jobs match" testId="jobs-empty" />
          ) : (
            <Card>
              <CardContent className="overflow-x-auto pt-6">
                <table className="w-full text-left text-sm" data-testid="jobs-table">
                  <thead className="text-xs text-muted-foreground">
                    <tr>
                      <th className="py-1 pr-3 font-medium">Job</th>
                      <th className="px-2 py-1 font-medium">State</th>
                      <th className="px-2 py-1 font-medium">Attempts</th>
                      <th className="px-2 py-1 font-medium">Outcome / error</th>
                      <th className="px-2 py-1 font-medium">Updated</th>
                      <th className="py-1 pl-2 font-medium">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {loaded.data.jobs.map((job) => {
                      const action = retryAction(job);
                      return (
                        <tr key={job.id} data-testid="job-row" data-job-id={job.id} data-state={job.state}>
                          <td className="py-2 pr-3 align-top">
                            <div className="font-mono text-xs">{job.job_type}</div>
                            <div className="font-mono text-[11px] text-muted-foreground">
                              {job.entity_type} {job.entity_id.slice(0, 8)}
                            </div>
                          </td>
                          <td className="px-2 py-2 align-top text-xs">{job.state}</td>
                          <td className="px-2 py-2 align-top text-xs tabular-nums">
                            {job.attempts}/{job.max_attempts}
                            {job.manual_retry_count ? ` · ${job.manual_retry_count} manual` : ''}
                          </td>
                          <td className="max-w-md px-2 py-2 align-top text-xs">
                            {job.outcome ? <div>{job.outcome}</div> : null}
                            {job.last_error ? <div className="break-words text-muted-foreground">{job.last_error}</div> : null}
                          </td>
                          <td className="px-2 py-2 align-top text-xs">{formatTime(job.updated_at)}</td>
                          <td className="py-2 pl-2 align-top">
                            {action.kind === 'action' ? (
                              <ActionForm
                                action={retryJob}
                                hidden={{ job_id: job.id, mode: action.mode, key: formKey('retry') }}
                                submitLabel={action.label}
                                testId="retry-form"
                              />
                            ) : action.kind === 'blocked' ? (
                              <span className="text-xs text-muted-foreground" data-testid="retry-blocked">
                                {action.label}
                              </span>
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {loaded.data.next_before ? (
                  <Link
                    href={`/admin/jobs?${new URLSearchParams({
                      ...(state ? { state } : {}),
                      ...(jobType ? { job_type: jobType } : {}),
                      before: loaded.data.next_before,
                    })}`}
                    className="mt-3 inline-block text-sm underline underline-offset-4"
                  >
                    Older jobs
                  </Link>
                ) : null}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </main>
  );
}
