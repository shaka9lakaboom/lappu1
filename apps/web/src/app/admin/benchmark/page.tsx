import { BENCHMARK_MODES, type AdminBenchmarkResponse } from '@skillmirror/contracts';

import { AreaHeader, ForbiddenPanel } from '@/components/areas/area-header';
import { EmptyState, ErrorNotice } from '@/components/experience/states';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { benchmarkLine, failedGates } from '@/lib/admin';
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
        description="The intelligence gate. Hard gates are zero-tolerance; quality metrics are calibration targets."
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
              const failing = run ? failedGates(run) : [];
              return (
                <Card key={mode} data-testid="benchmark-latest" data-mode={mode} data-verdict={run?.verdict ?? 'NONE'}>
                  <CardHeader>
                    <CardTitle className="text-base">{mode.toLowerCase()}</CardTitle>
                    <CardDescription>{MODE_TEXT[mode]}</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-1 text-sm">
                    {run ? (
                      <>
                        <p className="text-lg font-semibold">{run.verdict}</p>
                        <p>{benchmarkLine(run)}</p>
                        <p className="text-muted-foreground">
                          {run.model ?? 'scripted'} · {run.provider_requests} requests · {formatTime(run.finished_at)}
                        </p>
                        {failing.length ? (
                          <p className="text-destructive">Hard gates failed: {failing.join(', ')}</p>
                        ) : (
                          <p className="text-muted-foreground">All hard gates held.</p>
                        )}
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
                    <th className="px-2 py-1 font-medium">Verdict</th>
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
