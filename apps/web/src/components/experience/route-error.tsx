'use client';

import Link from 'next/link';
import { useEffect } from 'react';

import { Button } from '@/components/ui/button';

/** Error boundary body for the student pages: a system problem, never a skill judgement. */
export function RouteError({ title, error, retry }: { title: string; error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <main className="mx-auto max-w-3xl space-y-4 px-4 py-10 sm:px-6" data-testid="route-error">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p role="alert" className="text-sm text-muted-foreground">
        Something went wrong while loading this page. Your evidence and skills are safe; nothing was changed.
        {error.digest ? ` (reference ${error.digest})` : ''}
      </p>
      <div className="flex gap-3">
        <Button type="button" onClick={() => retry()}>
          Try again
        </Button>
        <Link href="/dashboard" className="inline-flex h-9 items-center rounded-md border px-4 text-sm">
          Back to the dashboard
        </Link>
      </div>
    </main>
  );
}
