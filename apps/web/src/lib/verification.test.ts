import { VERIFICATION_STATUSES } from '@skillmirror/contracts';
import { describe, expect, it } from 'vitest';

import { recommendationText } from '@/lib/experience';
import { recommendation, verificationChallenge, verificationQueue, verificationSession } from '@/lib/test-data';
import {
  STATUS,
  buildSubmission,
  isWaiting,
  responseText,
  triggerText,
  verificationActionLabel,
  verificationHref,
} from '@/lib/verification';

function form(values: Record<string, string | string[]>): FormData {
  const data = new FormData();
  for (const [name, value] of Object.entries(values)) {
    for (const v of Array.isArray(value) ? value : [value]) data.append(name, v);
  }
  return data;
}

describe('verification statuses', () => {
  it('has a plain-language label for every status, and none reads as a failure colour', () => {
    for (const status of VERIFICATION_STATUSES) {
      expect(STATUS[status].label.length).toBeGreaterThan(0);
      expect(STATUS[status].tone).not.toMatch(/red|destructive/);
    }
  });

  it('never presents an unfinished or ungraded check as a wrong answer', () => {
    for (const status of ['ABANDONED', 'NOT_ISSUED', 'NEEDS_REVIEW'] as const) {
      expect(STATUS[status].tone).toBe('neutral');
      expect(STATUS[status].detail).toMatch(/(not count|never counts) against you|Nothing about your skills changed/);
    }
  });

  it('explains why a check was suggested', () => {
    expect(triggerText(verificationSession())).toContain('repeatedly done this skill for you');
    expect(triggerText(verificationSession({ reason_code: 'VERIFICATION_STALE' }))).toContain('a while ago');
    expect(triggerText(verificationSession({ reason_code: 'VERIFICATION_CONTRADICTED' }))).toContain('disagree');
  });

  it('refreshes the page only while generation or grading is running', () => {
    expect(isWaiting(verificationQueue())).toBe(false);
    expect(isWaiting(verificationQueue({ preparing: [verificationSession({ status: 'PREPARING' })] }))).toBe(true);
    expect(isWaiting(verificationQueue({ pending: [verificationSession({ status: 'EVALUATING' })] }))).toBe(true);
  });
});

describe('recommendation actions', () => {
  it('links VERIFY / REVERIFY to the open check, or to the Verification Center', () => {
    expect(verificationHref(recommendation({ type: 'VERIFY' }))).toBe('/verifications');
    expect(
      verificationHref(recommendation({ type: 'REVERIFY', verification_session_id: verificationSession().id })),
    ).toBe(`/verifications/${verificationSession().id}`);
    expect(verificationHref(recommendation({ type: 'PRACTICE' }))).toBeNull();
    expect(verificationActionLabel(recommendation({ type: 'VERIFY', verification_state: 'IN_PROGRESS' }))).toBe(
      'Continue the check',
    );
    expect(verificationActionLabel(recommendation({ type: 'REVERIFY' }))).toBe('Start the fresh check');
  });

  it('describes a stale and a contradicted re-verification differently', () => {
    expect(recommendationText(recommendation({ type: 'REVERIFY', reason_code: 'VERIFICATION_STALE' })).body).toContain(
      'a while ago',
    );
    expect(
      recommendationText(recommendation({ type: 'REVERIFY', reason_code: 'VERIFICATION_CONTRADICTED' })).body,
    ).toContain('disagree');
  });
});

describe('the submission form', () => {
  const key = 'vs-11111111-2222-4333-8444-555555555555';

  it('builds an MCQ submission from the chosen options', () => {
    const result = buildSubmission(form({ idempotency_key: key, selected: ['b'] }), verificationChallenge());
    expect(result).toEqual({ ok: true, request: { selected: ['B'] }, idempotencyKey: key });
  });

  it('refuses a missing or unknown option and a missing request key', () => {
    expect(buildSubmission(form({ idempotency_key: key }), verificationChallenge())).toMatchObject({ ok: false });
    expect(buildSubmission(form({ idempotency_key: key, selected: 'D' }), verificationChallenge())).toMatchObject({
      ok: false,
    });
    expect(buildSubmission(form({ selected: 'B' }), verificationChallenge())).toMatchObject({ ok: false });
  });

  it('builds a written answer within the challenge limit', () => {
    const numeric = verificationChallenge({ assessment_type: 'numeric', choices: [], max_response_chars: 64 });
    expect(buildSubmission(form({ idempotency_key: key, answer: ' 42.5 ' }), numeric)).toEqual({
      ok: true,
      request: { answer: '42.5' },
      idempotencyKey: key,
    });
    expect(buildSubmission(form({ idempotency_key: key, answer: 'x'.repeat(65) }), numeric)).toMatchObject({
      ok: false,
    });
    expect(buildSubmission(form({ idempotency_key: key, answer: '   ' }), numeric)).toMatchObject({ ok: false });
  });

  it('shows the stored answer as plain text', () => {
    expect(responseText({ selected: ['A', 'C'] })).toBe('A, C');
    expect(responseText({ answer: '<b>not html</b>' })).toBe('<b>not html</b>');
    expect(responseText(null)).toBe('');
  });
});
