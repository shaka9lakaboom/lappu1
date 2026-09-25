import type { AdminModelRunsResponse } from '@skillmirror/contracts';
import Link from 'next/link';

import { AreaHeader, ForbiddenPanel } from '@/components/areas/area-header';
import { EmptyState, ErrorNotice } from '@/components/experience/states';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { budgetLine } from '@/lib/admin';
import { formatTime } from '@/lib/experience';
import { loadArea } from '@/lib/roles';
import { requireApiSession } from '@/lib/session';
import { cn } from '@/lib/utils';

export default async function AdminModelRunsPage({ searchParams }: PageProps<'/admin/model-runs'>) {
  const params = await searchParams;
  const failuresOnly = params.failures_only === 'true';
  const { accessToken } = await requireApiSession('/admin/model-runs');
  const loaded = await loadArea<AdminModelRunsResponse>(
    `/v1/admin/model-runs?limit=100${failuresOnly ? '&failures_only=true' : ''}`,
    accessToken,
  );

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-4 py-10 sm:px-6">
      <AreaHeader
        area="admin"
        current="/admin/model-runs"
        title="Model runs"
        description="Every model call with its task, prompt version, status and usage. Outputs are never shown."
      />
      {loaded.state === 'forbidden' ? (
        <ForbiddenPanel area="admin" />
      ) : loaded.state !== 'ok' ? (
        <ErrorNotice title="Model runs could not be loaded" message={loaded.state === 'error' ? loaded.message : 'Not found.'} />
      ) : (
        <>
          <section className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Last 24 hours</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm" data-testid="status-counts">
                  {Object.entries(loaded.data.status_counts_24h).map(([status, n]) => (
                    <li key={status}>
                      {status}: <span className="font-medium tabular-nums">{n}</span>
                    </li>
                  ))}
                  {Object.keys(loaded.data.status_counts_24h).length === 0 ? (
                    <li className="text-muted-foreground">No model call.</li>
                  ) : null}
                </ul>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Request budget (quota day)</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-1 text-sm">
                  {loaded.data.budget.map((b) => (
                    <li key={`${b.provider}:${b.model}`} className="flex flex-wrap justify-between gap-2">
                      <span className="font-mono text-xs">{b.model}</span>
                      <span className="text-muted-foreground">{budgetLine(b)}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          </section>
          <nav className="flex gap-1 text-sm" aria-label="Filter">
            <Link
              href="/admin/model-runs"
              className={cn('rounded-md px-3 py-1.5', !failuresOnly ? 'bg-muted font-medium' : 'text-muted-foreground')}
            >
              All
            </Link>
            <Link
              href="/admin/model-runs?failures_only=true"
              className={cn('rounded-md px-3 py-1.5', failuresOnly ? 'bg-muted font-medium' : 'text-muted-foreground')}
            >
              Failures only
            </Link>
          </nav>
          {loaded.data.runs.length === 0 ? (
            <EmptyState title="No model runs match" testId="model-runs-empty" />
          ) : (
            <Card>
              <CardContent className="overflow-x-auto pt-6">
                <table className="w-full text-left text-sm" data-testid="model-runs-table">
                  <thead className="text-xs text-muted-foreground">
                    <tr>
                      <th className="py-1 pr-3 font-medium">Task</th>
                      <th className="px-2 py-1 font-medium">Model</th>
                      <th className="px-2 py-1 font-medium">Status</th>
                      <th className="px-2 py-1 text-right font-medium">Tokens</th>
                      <th className="px-2 py-1 text-right font-medium">ms</th>
                      <th className="px-2 py-1 font-medium">Error</th>
                      <th className="py-1 pl-2 font-medium">When</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {loaded.data.runs.map((run) => (
                      <tr key={run.id} data-testid="model-run-row" data-status={run.status}>
                        <td className="py-1 pr-3 align-top">
                          <div className="font-mono text-xs">{run.task_type}</div>
                          <div className="font-mono text-[11px] text-muted-foreground">{run.prompt_version}</div>
                        </td>
                        <td className="px-2 py-1 align-top font-mono text-xs">{run.model}</td>
                        <td className="px-2 py-1 align-top text-xs">
                          {run.status}
                          {run.cache_hit ? ' · cache' : ''}
                          {run.attempt > 1 ? ` · attempt ${run.attempt}` : ''}
                        </td>
                        <td className="px-2 py-1 text-right align-top tabular-nums text-xs">{run.total_tokens ?? '—'}</td>
                        <td className="px-2 py-1 text-right align-top tabular-nums text-xs">{run.latency_ms}</td>
                        <td className="max-w-sm px-2 py-1 align-top text-xs text-muted-foreground">
                          {run.error_code ? <span className="font-mono">{run.error_code}</span> : null}
                          {run.error_message ? <div className="break-words">{run.error_message}</div> : null}
                        </td>
                        <td className="py-1 pl-2 align-top text-xs">{formatTime(run.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </main>
  );
}
