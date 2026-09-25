'use client';

import type { FeedbackAction, FeedbackTargetType } from '@skillmirror/contracts';
import { useActionState, useState } from 'react';

import type { FeedbackFormState } from '@/app/feedback/actions';
import { Button } from '@/components/ui/button';

const COPY: Record<'WRONG_SKILL' | 'DONT_COUNT', { label: string; confirm: string }> = {
  WRONG_SKILL: {
    label: 'Wrong skill',
    confirm:
      'Mark this as the wrong skill? Evidence from it stops counting for this skill. The activity stays visible, and this cannot be undone.',
  },
  DONT_COUNT: {
    label: "Don't count this",
    confirm:
      'Stop counting this activity as evidence? It stays visible in your activity, and this cannot be undone.',
  },
};

/**
 * A correction (Wrong skill / Don't count this) with an explicit confirmation step: the exclusion
 * is one-way. One idempotency key per button instance, so a double submit records it once.
 */
export function CorrectionButton({
  action,
  targetType,
  targetId,
  returnTo,
  submit,
}: {
  action: Extract<FeedbackAction, 'WRONG_SKILL' | 'DONT_COUNT'>;
  targetType: FeedbackTargetType;
  targetId: string;
  returnTo: string;
  submit: (state: FeedbackFormState, formData: FormData) => Promise<FeedbackFormState>;
}) {
  const [state, formAction, pending] = useActionState(submit, { status: 'idle' });
  const [confirming, setConfirming] = useState(false);
  const [key] = useState(() => `fb-${crypto.randomUUID()}`);
  const copy = COPY[action];

  if (state.status === 'done') {
    return (
      <p role="status" className="text-xs text-muted-foreground" data-testid="correction-done">
        {state.message}
      </p>
    );
  }
  return (
    <form action={formAction} className="inline-flex flex-wrap items-center gap-2" data-testid="correction-form" data-action={action}>
      <input type="hidden" name="action" value={action} />
      <input type="hidden" name="target_type" value={targetType} />
      <input type="hidden" name="target_id" value={targetId} />
      <input type="hidden" name="idempotency_key" value={key} />
      <input type="hidden" name="return_to" value={returnTo} />
      {confirming ? (
        <>
          <span className="text-xs text-muted-foreground">{copy.confirm}</span>
          <Button type="submit" size="sm" disabled={pending} data-testid="correction-confirm">
            {pending ? 'Saving…' : 'Confirm'}
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={pending}>
            Cancel
          </Button>
        </>
      ) : (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setConfirming(true)}
          data-testid="correction-start"
        >
          {copy.label}
        </Button>
      )}
      {state.status === 'error' ? (
        <span role="alert" className="text-xs text-destructive">
          {state.message}
        </span>
      ) : null}
    </form>
  );
}
