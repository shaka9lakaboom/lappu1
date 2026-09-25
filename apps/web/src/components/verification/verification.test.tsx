import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { SubmissionFormState } from '@/app/verifications/actions';
import { ChallengeForm } from '@/components/verification/challenge-form';
import { ResultPanel, SessionCard, StatusBadge } from '@/components/verification/session-card';
import { verificationChallenge, verificationSession } from '@/lib/test-data';

const submit = async (): Promise<SubmissionFormState> => ({ status: 'idle' });
const html = (node: React.ReactElement) => renderToStaticMarkup(node);

describe('Verification Center', () => {
  it('shows a ready check with its reason and a link to it', () => {
    const markup = html(<SessionCard session={verificationSession()} />);
    expect(markup).toContain('data-status="READY"');
    expect(markup).toContain('Choosing LEFT JOIN');
    expect(markup).toContain('repeatedly done this skill for you');
    expect(markup).toContain('href="/verifications/77777777-7777-4777-8777-777777777777"');
    expect(markup).toContain('Multiple choice · about 2 minutes');
  });

  it('never shows an abandoned check as a failure', () => {
    const markup = html(
      <SessionCard session={verificationSession({ state: 'ABANDONED', status: 'ABANDONED', abandon_reason: 'LEARNER_ABANDONED' })} />,
    );
    expect(markup).toContain('Stopped');
    expect(markup).toContain('never counts against you');
    expect(markup).toContain('data-tone="neutral"');
    expect(markup).not.toMatch(/red-|destructive/);
  });

  it('labels the status with its tone', () => {
    expect(html(<StatusBadge session={{ status: 'PASSED' }} />)).toContain('data-tone="emerald"');
    expect(html(<StatusBadge session={{ status: 'EVALUATING' }} />)).toContain('Checking your answer');
  });
});

describe('the challenge form', () => {
  it('renders the options, one idempotency key and no answer key', () => {
    const markup = html(<ChallengeForm challenge={verificationChallenge()} submit={submit} />);
    expect(markup.split('data-testid="challenge-choice"').length - 1).toBe(2);
    expect(markup).toContain('type="radio"');
    expect(markup).toMatch(/name="idempotency_key" value="vs-[0-9a-f-]{36}"/);
    expect(markup).toContain('name="choice_keys" value="A,B"');
    expect(markup).not.toMatch(/expected_answer|rubric/);
    expect(markup).toContain('without AI help');
  });

  it('uses checkboxes for select-all-that-apply and a number field for numeric answers', () => {
    expect(html(<ChallengeForm challenge={verificationChallenge({ multiple_select: true })} submit={submit} />)).toContain(
      'type="checkbox"',
    );
    const numeric = html(
      <ChallengeForm challenge={verificationChallenge({ assessment_type: 'numeric', choices: [], max_response_chars: 64 })} submit={submit} />,
    );
    expect(numeric).toContain('inputMode="decimal"');
    expect(numeric).toContain('maxLength="64"');
  });
});

describe('the result', () => {
  it('shows the feedback and that the result became evidence', () => {
    const markup = html(
      <ResultPanel
        result={{
          id: '99999999-9999-4999-8999-999999999999',
          score: 1,
          passed: true,
          outcome_signal: 'CORRECT',
          feedback: 'Correct.',
          grading_confidence: 1,
          evaluator_type: 'DETERMINISTIC',
          criteria: [],
          evidence_id: '12121212-1212-4121-8121-121212121212',
          created_at: '2026-09-25T10:05:00Z',
        }}
      />,
    );
    expect(markup).toContain('You passed this check.');
    expect(markup).toContain('Checked automatically against the answer key.');
    expect(markup).toContain('one piece of independent evidence');
  });

  it('lists rubric criteria for an AI-graded answer', () => {
    const markup = html(
      <ResultPanel
        result={{
          id: '99999999-9999-4999-8999-999999999999',
          score: 0.4,
          passed: false,
          outcome_signal: 'PARTIAL',
          feedback: 'You kept the right rows; name the join too.',
          grading_confidence: 0.85,
          evaluator_type: 'AI_RUBRIC',
          criteria: [
            { criterion: 'Keeps unmatched rows', met: true },
            { criterion: 'Names LEFT JOIN', met: false },
          ],
          evidence_id: null,
          created_at: '2026-09-25T10:05:00Z',
        }}
      />,
    );
    expect(markup).toContain('Partly there.');
    expect(markup).toContain('confidence 85%');
    expect(markup).toContain('Names LEFT JOIN');
  });
});
