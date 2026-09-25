import type { VerificationResult, VerificationSessionSummary } from '@skillmirror/contracts';
import Link from 'next/link';

import { ToneBadge } from '@/components/experience/badges';
import { formatTime } from '@/lib/experience';
import { ASSESSMENT_LABEL, STATUS, minutesLabel, triggerText } from '@/lib/verification';

export function StatusBadge({ session }: { session: Pick<VerificationSessionSummary, 'status'> }) {
  const presentation = STATUS[session.status];
  return (
    <span data-status={session.status} className="inline-flex">
      <ToneBadge tone={presentation.tone} testId="verification-status">
        {presentation.label}
      </ToneBadge>
    </span>
  );
}

const ACTION: Partial<Record<VerificationSessionSummary['status'], string>> = {
  READY: 'Open the check',
  IN_PROGRESS: 'Continue',
  EVALUATING: 'See your answer',
  PASSED: 'See the result',
  PARTIAL: 'See the result',
  NOT_PASSED: 'See the result',
};

/** One verification in the Verification Center. */
export function SessionCard({ session }: { session: VerificationSessionSummary }) {
  const action = ACTION[session.status];
  const details = [
    session.assessment_type ? ASSESSMENT_LABEL[session.assessment_type] : null,
    minutesLabel(session.estimated_minutes) || null,
  ].filter(Boolean);
  return (
    <div
      className="flex flex-wrap items-start justify-between gap-3 py-3 first:pt-0 last:pb-0"
      data-testid="verification-session"
      data-session-id={session.id}
      data-status={session.status}
    >
      <div className="min-w-0 space-y-1">
        <p className="flex flex-wrap items-center gap-2 font-medium">
          <Link href={`/skills/${session.skill_id}`} className="underline-offset-4 hover:underline">
            {session.canonical_name}
          </Link>
          <StatusBadge session={session} />
        </p>
        <p className="text-sm text-muted-foreground">
          {session.status === 'READY' || session.status === 'PREPARING'
            ? triggerText(session)
            : STATUS[session.status].detail}
        </p>
        <p className="text-xs text-muted-foreground">
          {details.length ? `${details.join(' · ')} · ` : ''}
          {session.evaluated_at
            ? `checked ${formatTime(session.evaluated_at)}`
            : `suggested ${formatTime(session.created_at)}`}
        </p>
      </div>
      {action ? (
        <Link
          href={`/verifications/${session.id}`}
          className="inline-flex h-9 items-center rounded-md border px-4 text-sm font-medium hover:bg-muted"
          data-testid="verification-open"
        >
          {action}
        </Link>
      ) : null}
    </div>
  );
}

/** The graded result with its feedback (after evaluation only). */
export function ResultPanel({ result }: { result: VerificationResult }) {
  return (
    <section className="space-y-2" data-testid="verification-result" data-passed={result.passed}>
      <p className="text-base font-medium">
        {result.passed ? 'You passed this check.' : result.outcome_signal === 'PARTIAL' ? 'Partly there.' : 'Not yet.'}
      </p>
      <p className="text-sm" data-testid="verification-feedback">
        {result.feedback}
      </p>
      {result.criteria.length > 0 ? (
        <ul className="space-y-1 text-sm" data-testid="verification-criteria">
          {result.criteria.map((c) => (
            <li key={c.criterion} className="flex gap-2">
              <span aria-hidden="true">{c.met ? '✓' : '○'}</span>
              <span>{c.criterion}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="text-xs text-muted-foreground">
        {result.evaluator_type === 'DETERMINISTIC'
          ? 'Checked automatically against the answer key.'
          : `Checked against the rubric (confidence ${Math.round(result.grading_confidence * 100)}%).`}{' '}
        This result is now one piece of independent evidence for the skill; your skill state was recalculated
        from all of your evidence.
      </p>
    </section>
  );
}
