import Link from 'next/link';
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

const AREAS = {
  teacher: [{ href: '/teacher', label: 'My courses' }],
  admin: [
    { href: '/admin', label: 'Overview' },
    { href: '/admin/jobs', label: 'Jobs' },
    { href: '/admin/model-runs', label: 'Model runs' },
    { href: '/admin/skill-candidates', label: 'Skill candidates' },
    { href: '/admin/benchmark', label: 'Benchmark' },
  ],
} as const;

/** Header of the teacher / admin areas: area navigation, a way back, the page title. */
export function AreaHeader({
  area,
  current,
  title,
  description,
  children,
}: {
  area: keyof typeof AREAS;
  current: string | null;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="space-y-4">
      <nav aria-label={area === 'admin' ? 'Admin' : 'Teaching'} className="flex flex-wrap items-center gap-1 text-sm">
        <Link href="/dashboard" className="mr-2 rounded-md px-3 py-1.5 text-muted-foreground hover:bg-muted">
          ← Dashboard
        </Link>
        {AREAS[area].map((link) => (
          <Link
            key={link.href}
            href={link.href}
            aria-current={current === link.href ? 'page' : undefined}
            className={cn(
              'rounded-md px-3 py-1.5 transition-colors hover:bg-muted',
              current === link.href ? 'bg-muted font-medium' : 'text-muted-foreground',
            )}
          >
            {link.label}
          </Link>
        ))}
      </nav>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
        </div>
        {children}
      </div>
    </header>
  );
}

/** A role the backend refused (403): a plain panel, not an error page. */
export function ForbiddenPanel({ area }: { area: 'teacher' | 'admin' }) {
  return (
    <div className="rounded-lg border p-6 text-sm" role="status" data-testid="forbidden-panel">
      <p className="font-medium">
        {area === 'admin' ? 'This area is for SkillMirror administrators.' : 'This area is for teachers.'}
      </p>
      <p className="mt-1 text-muted-foreground">
        Your account does not have that role. Roles are granted by the SkillMirror operator; your own learning
        pages are unchanged.{' '}
        <Link href="/dashboard" className="underline underline-offset-4">
          Back to the dashboard
        </Link>
      </p>
    </div>
  );
}
