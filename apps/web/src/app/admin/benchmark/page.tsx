import { BENCHMARK_MODES, type AdminBenchmarkResponse } from '@skillmirror/contracts';

import { AreaHeader, ForbiddenPanel } from '@/components/areas/area-header';
import { EmptyState, ErrorNotice } from '@/components/experience/states';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { benchmarkBreakdown, benchmarkLine } from '@/lib/admin';
import { formatTime } from '@/lib/experience';
import { loadArea } from '@/lib/roles';
import { requireApiSession } from '@/lib/session';

const MODE_TEXT = {
  DETERMINISTIC: 'Scripted model answers through the production code (CI).',
  REPLAY: 'Recorded real-model answers replayed (CI, no request).',
  LIVE: 'The real model, against its daily quota.',
} as const;

export default async function AdminBenchmarkPage() {
  const { accessToken } = await requireApiSession('/admin/benchmark');
  const loaded = await loadArea<AdminBenchmarkResponse>('/v1/admin/benchmark', accessToken);

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-10 sm:px-6">
      <AreaHeader
        area="admin"
        current="/admin/benchmark"
        title="Benchmark"
        description="The intelligence gate. Hard gates are zero-tolerance safety and correctness checks; each stored verdict is shown as recorded, with its parts next to it."
      />
      {loaded.state === 'forbidden' ? (
        <ForbiddenPanel area="admin" />
      ) : loaded.state !== 'ok' ? (
        <ErrorNotice title="Benchmark runs could not be loaded" message={loaded.state === 'error' ? loaded.message : 'Not found.'} />
      ) : loaded.data.runs.length === 0 ? (
        <EmptyState title="No benchmark run recorded yet" testId="benchmark-empty">
          Runs appear here once the benchmark runner records them.
        </EmptyState>
      ) : (
        <>
          <section className="grid gap-4 md:grid-cols-3" aria-label="Latest per mode">
            {BENCHMARK_MODES.map((mode) => {
              const run = loaded.data.latest[mode];
              const parts = run ? benchmarkBreakdown(run) : null;
              return (
                <Card key={mode} data-testid="benchmark-latest" data-mode={mode} data-verdict={run?.verdict ?? 'NONE'}>
                  <CardHeader>
                    <CardTitle className="text-base">{mode.toLowerCase()}</CardTitle>
                    <CardDescription>{MODE_TEXT[mode]}</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-1 text-sm">
                    {run && parts ? (
                      <>
                        <dl className="space-y-2" data-testid="benchmark-breakdown">
                          <div>
                            <dt className="text-xs text-muted-foreground">Safety &amp; correctness hard gates</dt>
                            <dd
                              className={parts.gatesOk ? 'font-medium' : 'font-medium text-destructive'}
                              data-testid="benchmark-gates"
                            >
                              {parts.gates}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-xs text-muted-foreground">Case completion</dt>
                            <dd className="font-medium" data-testid="benchmark-completion">
                              {parts.completion}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-xs text-muted-foreground">Provider / transport failures</dt>
                            <dd className="font-medium" data-testid="benchmark-provider">
                              {parts.provider}
                            </dd>
                          </div>
                          <div>
                            <dt className="text-xs text-muted-foreground">Stored benchmark verdict</dt>
                            <dd className="text-lg font-semibold" data-testid="benchmark-verdict">
                              {run.verdict}
                            </dd>
                          </div>
                        </dl>
                        {parts.note ? (
                          <p className="text-muted-foreground" data-testid="benchmark-note">
                            {parts.note}
                          </p>
                        ) : null}
                        <p className="text-muted-foreground">
                          {benchmarkLine(run)} · {run.model ?? 'scripted'} · {run.provider_requests} requests ·{' '}
                          {formatTime(run.finished_at)}
                        </p>
                        {parts.source ? (
                          <p className="break-all text-xs text-muted-foreground" data-testid="benchmark-source">
                            Failure cause from the {parts.source}; the stored row keeps only the case id.
                          </p>
                        ) : null}
                      </>
                    ) : (
                      <p className="text-muted-foreground">No run yet.</p>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </section>
          <Card>
            <CardContent className="overflow-x-auto pt-6">
              <table className="w-full text-left text-sm" data-testid="benchmark-runs">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Set</th>
                    <th className="px-2 py-1 font-medium">Mode</th>
                    <th className="px-2 py-1 font-medium">Model</th>
                    <th className="px-2 py-1 font-medium">Result</th>
                    <th className="px-2 py-1 font-medium">Hard gates</th>
                    <th className="px-2 py-1 font-medium">Provider failures</th>
                    <th className="px-2 py-1 font-medium">Stored verdict</th>
                    <th className="py-1 pl-2 font-medium">Finished</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {loaded.data.runs.map((run) => (
                    <tr key={run.id}>
                      <td className="py-1 pr-3 font-mono text-xs">
                        {run.set_name}@{run.set_version}
                      </td>
                      <td className="px-2 py-1 text-xs">{run.mode}</td>
                      <td className="px-2 py-1 font-mono text-xs">{run.model ?? '—'}</td>
                      <td className="px-2 py-1 text-xs">{benchmarkLine(run)}</td>
                      <td className="px-2 py-1 text-xs">{run.hard_gates_failed.length === 0 ? 'PASS' : 'FAIL'}</td>
                      <td className="px-2 py-1 text-xs">{benchmarkBreakdown(run).provider}</td>
                      <td className="px-2 py-1 text-xs font-medium">{run.verdict}</td>
                      <td className="py-1 pl-2 text-xs">{formatTime(run.finished_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </>
      )}
    </main>
  );
}
