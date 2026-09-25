'use client';

import type { VerificationChallenge } from '@skillmirror/contracts';
import { useActionState, useEffect, useRef, useState } from 'react';

import type { SubmissionFormState } from '@/app/verifications/actions';
import { Button } from '@/components/ui/button';

const DRAFT_PREFIX = 'sm-verification-draft:';

interface Draft {
  answer: string;
  selected: string[];
}

function readDraft(sessionId: string): Draft | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_PREFIX + sessionId);
    return raw ? (JSON.parse(raw) as Draft) : null;
  } catch {
    return null;
  }
}

function writeDraft(sessionId: string, draft: Draft | null): void {
  try {
    if (draft) window.localStorage.setItem(DRAFT_PREFIX + sessionId, JSON.stringify(draft));
    else window.localStorage.removeItem(DRAFT_PREFIX + sessionId);
  } catch {
    // Storage can be unavailable (private mode): the check still works without a local draft.
  }
}

/**
 * The learner's answer. One idempotency key per form instance, so a double click or a retry after
 * a network error stores the answer once. The draft is kept in this browser until it is sent, so a
 * refresh or a dropped connection loses nothing (the session itself stays IN_PROGRESS server-side).
 * The inputs are uncontrolled: the draft is restored into the DOM once mounted.
 */
export function ChallengeForm({
  challenge,
  submit,
}: {
  challenge: VerificationChallenge;
  submit: (state: SubmissionFormState, formData: FormData) => Promise<SubmissionFormState>;
}) {
  const [state, formAction, pending] = useActionState(submit, { status: 'idle' });
  const [key] = useState(() => `vs-${crypto.randomUUID()}`);
  const form = useRef<HTMLFormElement>(null);
  const isMcq = challenge.assessment_type === 'mcq';
  const sessionId = challenge.session_id;

  useEffect(() => {
    const draft = readDraft(sessionId);
    const element = form.current;
    if (!draft || !element) return;
    const answer = element.elements.namedItem('answer');
    if (answer instanceof HTMLInputElement || answer instanceof HTMLTextAreaElement) answer.value = draft.answer;
    element.querySelectorAll<HTMLInputElement>('input[name="selected"]').forEach((input) => {
      input.checked = draft.selected.includes(input.value);
    });
  }, [sessionId]);

  useEffect(() => {
    if (state.status === 'done') writeDraft(sessionId, null);
  }, [state.status, sessionId]);

  function remember() {
    if (!form.current) return;
    const data = new FormData(form.current);
    writeDraft(sessionId, {
      answer: String(data.get('answer') ?? ''),
      selected: data.getAll('selected').map(String),
    });
  }

  if (state.status === 'done') {
    return (
      <p role="status" className="text-sm text-muted-foreground" data-testid="submission-done">
        {state.message}
      </p>
    );
  }

  const inputClass =
    'flex w-full rounded-md border border-input bg-transparent px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring';
  return (
    <form ref={form} action={formAction} onChange={remember} className="space-y-4" data-testid="challenge-form">
      <input type="hidden" name="session_id" value={sessionId} />
      <input type="hidden" name="assessment_type" value={challenge.assessment_type} />
      <input type="hidden" name="choice_keys" value={challenge.choices.map((c) => c.key).join(',')} />
      <input type="hidden" name="max_response_chars" value={challenge.max_response_chars} />
      <input type="hidden" name="idempotency_key" value={key} />
      {isMcq ? (
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">
            {challenge.multiple_select ? 'Select every option that applies.' : 'Choose one option.'}
          </legend>
          {challenge.choices.map((choice) => (
            <label
              key={choice.key}
              className="flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm hover:bg-muted/40"
              data-testid="challenge-choice"
            >
              <input
                type={challenge.multiple_select ? 'checkbox' : 'radio'}
                name="selected"
                value={choice.key}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium">{choice.key}.</span> {choice.text}
              </span>
            </label>
          ))}
        </fieldset>
      ) : (
        <label className="block space-y-1 text-sm">
          <span className="font-medium">
            {challenge.assessment_type === 'numeric' ? 'Your answer (a number)' : 'Your answer'}
          </span>
          {challenge.assessment_type === 'numeric' ? (
            <input
              name="answer"
              inputMode="decimal"
              autoComplete="off"
              maxLength={challenge.max_response_chars}
              className={`${inputClass} h-9 max-w-xs`}
              data-testid="challenge-answer"
            />
          ) : (
            <textarea
              name="answer"
              rows={challenge.assessment_type === 'reasoning' ? 6 : 3}
              maxLength={challenge.max_response_chars}
              className={`${inputClass} py-2`}
              data-testid="challenge-answer"
            />
          )}
        </label>
      )}
      {state.status === 'error' ? (
        <p role="alert" className="text-sm text-destructive" data-testid="submission-error">
          {state.message}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={pending} data-testid="challenge-submit">
          {pending ? 'Sending…' : 'Submit answer'}
        </Button>
        <span className="text-xs text-muted-foreground">
          Work on your own, without AI help. You can leave and come back; your progress is kept.
        </span>
      </div>
    </form>
  );
}
