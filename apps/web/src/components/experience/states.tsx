import type { ReactNode } from 'react';

import { Card, CardContent } from '@/components/ui/card';
import { MASTERY, STATE_ORDER, TONE_CLASSES, type StateCounts } from '@/lib/experience';
import { cn } from '@/lib/utils';

const FUTURE_STATES = new Set(['VERIFIED', 'NEEDS_REVERIFICATION']);

/** Mastery-state counts. UNKNOWN comes first and reads "Not enough activity yet". */
export function StateCountTiles({ counts }: { counts: StateCounts }) {
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6" data-testid="state-counts">
      {STATE_ORDER.map((state) => (
        <div
          key={state}
          className={cn('rounded-lg border p-3', TONE_CLASSES[MASTERY[state].tone])}
          data-testid="state-count"
          data-state={state}
        >
          <dt className="text-xs font-medium">{MASTERY[state].label}</dt>
          <dd className="mt-1 text-2xl font-semibold tabular-nums">{counts[state]}</dd>
          {FUTURE_STATES.has(state) && counts[state] === 0 ? (
            <p className="mt-1 text-[11px] leading-tight opacity-80">After a SkillMirror check</p>
          ) : null}
        </div>
      ))}
    </dl>
  );
}

export function EmptyState({ title, children, testId }: { title: string; children?: ReactNode; testId?: string }) {
  return (
    <Card>
      <CardContent className="space-y-2 py-8 text-sm" data-testid={testId ?? 'empty-state'}>
        <p className="font-medium">{title}</p>
        {children ? <div className="text-muted-foreground">{children}</div> : null}
      </CardContent>
    </Card>
  );
}

/** A request that failed (a system problem, not a skill state). */
export function ErrorNotice({ title, message }: { title: string; message: string }) {
  return (
    <div role="alert" className="rounded-lg border border-destructive/40 p-4 text-sm" data-testid="error-notice">
      <p className="font-medium text-destructive">{title}</p>
      <p className="text-muted-foreground">{message}</p>
    </div>
  );
}

export function PageSkeleton({ title, rows = 3 }: { title: string; rows?: number }) {
  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-10 sm:px-6" aria-busy="true" data-testid="page-loading">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="text-sm text-muted-foreground">Loading…</p>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-24 animate-pulse rounded-xl border bg-muted" />
      ))}
    </main>
  );
}
