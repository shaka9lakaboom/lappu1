import Link from 'next/link';
import type { ReactNode } from 'react';

import type { AreaLink } from '@/lib/roles';
import { cn } from '@/lib/utils';

const LINKS = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/skills', label: 'Skill map' },
  { href: '/verifications', label: 'Verification' },
  { href: '/activity', label: 'Activity' },
  { href: '/courses', label: 'Courses' },
] as const;

/**
 * Page title, section navigation and an optional control (e.g. the course selector). `areas` are
 * the teacher / admin entries the account's profile role opens (from GET /v1/me).
 */
export function AppHeader({
  current,
  title,
  description,
  areas = [],
  children,
}: {
  current: (typeof LINKS)[number]['href'] | null;
  title: string;
  description?: ReactNode;
  areas?: AreaLink[];
  children?: ReactNode;
}) {
  return (
    <header className="space-y-4">
      <nav aria-label="Sections" className="flex flex-wrap gap-1 text-sm">
        {LINKS.map((link) => (
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
        {areas.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className="rounded-md border px-3 py-1.5 text-muted-foreground transition-colors hover:bg-muted"
            data-testid="area-link"
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
