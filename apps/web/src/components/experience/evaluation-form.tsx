'use client';

import { FEEDBACK_NOTE_MAX_CHARS, type FeedbackTargetType } from '@skillmirror/contracts';
import { useActionState, useState } from 'react';

import type { FeedbackFormState } from '@/app/feedback/actions';
import { Button } from '@/components/ui/button';

const VERDICTS = [
  { value: 'AGREE', label: 'Looks right' },
  { value: 'DISAGREE', label: "Doesn't look right" },
  { value: 'UNCLEAR', label: 'Unclear' },
] as const;

/** Feedback on SkillMirror's assessment or explanation. It never changes evidence. */
export function EvaluationForm({
  targetType,
  targetId,
  returnTo,
  submit,
  question = 'Does this assessment look right to you?',
}: {
  targetType: FeedbackTargetType;
  targetId: string;
  returnTo: string;
  submit: (state: FeedbackFormState, formData: FormData) => Promise<FeedbackFormState>;
  question?: string;
}) {
  const [state, formAction, pending] = useActionState(submit, { status: 'idle' });
  const [key] = useState(() => `eval-${crypto.randomUUID()}`);

  if (state.status === 'done') {
    return (
      <p role="status" className="text-sm text-muted-foreground" data-testid="evaluation-done">
        {state.message}
      </p>
    );
  }
  return (
    <form action={formAction} className="space-y-3 text-sm" data-testid="evaluation-form">
      <input type="hidden" name="action" value="EVALUATION" />
      <input type="hidden" name="target_type" value={targetType} />
      <input type="hidden" name="target_id" value={targetId} />
      <input type="hidden" name="idempotency_key" value={key} />
      <input type="hidden" name="return_to" value={returnTo} />
      <fieldset className="space-y-2">
        <legend className="font-medium">{question}</legend>
        <div className="flex flex-wrap gap-3">
          {VERDICTS.map((verdict) => (
            <label key={verdict.value} className="inline-flex items-center gap-1.5">
              <input type="radio" name="verdict" value={verdict.value} required />
              {verdict.label}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="block space-y-1">
        <span className="text-muted-foreground">Anything to add? (optional)</span>
        <textarea
          name="note"
          rows={2}
          maxLength={FEEDBACK_NOTE_MAX_CHARS}
          className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </label>
      {state.status === 'error' ? (
        <p role="alert" className="text-destructive">
          {state.message}
        </p>
      ) : null}
      <Button type="submit" size="sm" variant="outline" disabled={pending}>
        {pending ? 'Sending…' : 'Send feedback'}
      </Button>
    </form>
  );
}
