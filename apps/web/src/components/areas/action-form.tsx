'use client';

import { useActionState, type ReactNode } from 'react';

import type { AdminFormState } from '@/app/admin/actions';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

/**
 * A small admin form around a server action. `hidden` carries the ids and the Idempotency-Key
 * minted when the page was rendered, so a double submit replays instead of repeating.
 */
export function ActionForm({
  action,
  hidden,
  submitLabel,
  children,
  testId,
  variant = 'outline',
  className,
}: {
  action: (prev: AdminFormState, formData: FormData) => Promise<AdminFormState>;
  hidden: Record<string, string>;
  submitLabel: string;
  children?: ReactNode;
  testId?: string;
  variant?: 'default' | 'outline' | 'ghost';
  className?: string;
}) {
  const [state, formAction, pending] = useActionState(action, { status: 'idle' });
  return (
    <form action={formAction} className={cn('flex flex-wrap items-center gap-2', className)} data-testid={testId}>
      {Object.entries(hidden).map(([name, value]) => (
        <input key={name} type="hidden" name={name} value={value} />
      ))}
      {children}
      <Button type="submit" size="sm" variant={variant} disabled={pending}>
        {pending ? 'Working…' : submitLabel}
      </Button>
      {state.status !== 'idle' ? (
        <p
          role={state.status === 'error' ? 'alert' : 'status'}
          className={cn('w-full text-xs', state.status === 'error' ? 'text-destructive' : 'text-muted-foreground')}
          data-testid="action-result"
        >
          {state.message}
        </p>
      ) : null}
    </form>
  );
}
