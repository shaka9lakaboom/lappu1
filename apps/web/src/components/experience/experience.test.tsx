import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { FeedbackFormState } from '@/app/feedback/actions';
import { ActivityList } from '@/components/experience/activity-list';
import { DebtBadge, MasteryBadge } from '@/components/experience/badges';
import { CorrectionButton } from '@/components/experience/correction-button';
import { CourseSelector } from '@/components/experience/course-selector';
import { EvaluationForm } from '@/components/experience/evaluation-form';
import { EvidenceTimeline } from '@/components/experience/evidence-timeline';
import { DebtPanel, MasteryPanel, RecommendationCard } from '@/components/experience/panels';
import { EmptyState, ErrorNotice, PageSkeleton, StateCountTiles } from '@/components/experience/states';
import { emptyCounts } from '@/lib/experience';
import {
  COURSE,
  ELIGIBLE_DEBT,
  OTHER_SKILL,
  activityRow,
  debt,
  evidenceItem,
  mastery,
  recommendation,
} from '@/lib/test-data';

const submit = async (): Promise<FeedbackFormState> => ({ status: 'idle' });
const html = (node: React.ReactElement) => renderToStaticMarkup(node);
const count = (markup: string, needle: string) => markup.split(needle).length - 1;

describe('state badges and counts', () => {
  it('renders UNKNOWN neutrally and never in a failure colour', () => {
    const markup = html(<MasteryBadge state="UNKNOWN" />);
    expect(markup).toContain('Not enough activity yet');
    expect(markup).toContain('data-tone="neutral"');
    expect(markup).not.toMatch(/red-|destructive/);
    expect(html(<MasteryBadge state="DEMONSTRATED" />)).toContain('Demonstrated');
    expect(html(<DebtBadge band="MODERATE" />)).toContain('Moderate reliance signal');
  });

  it('counts states with UNKNOWN first and marks the future P6 states', () => {
    const markup = html(<StateCountTiles counts={{ ...emptyCounts(), UNKNOWN: 2, DEMONSTRATED: 1 }} />);
    expect(markup.indexOf('data-state="UNKNOWN"')).toBeLessThan(markup.indexOf('data-state="DEMONSTRATED"'));
    expect(markup).toContain('Not enough activity yet');
    expect(count(markup, 'After SkillMirror checks (coming next)')).toBe(2);
  });
});

describe('mastery panel', () => {
  it('explains UNKNOWN in plain language with the numbers behind "Why?"', () => {
    const markup = html(<MasteryPanel mastery={mastery()} />);
    expect(markup).toContain('SkillMirror does not have enough independent evidence yet.');
    expect(markup).toContain('says nothing about your ability');
    expect(markup).toMatch(/<details[^>]*data-testid="mastery-why"[^>]*><summary[^>]*>Why\?<\/summary>/);
    expect(count(markup, 'data-testid="mastery-gate"')).toBe(1);
  });

  it('lists every DEMONSTRATED gate', () => {
    const markup = html(
      <MasteryPanel
        mastery={mastery({
          state: 'DEMONSTRATED',
          explanation_code: 'INDEPENDENT_EVIDENCE_SUPPORTS',
          support: 3.15,
          mastery_mean: 0.806,
          evidence_count: 4,
          performance_evidence_count: 4,
          excluded_evidence_count: 1,
          has_independent_application: true,
          gates: [
            { code: 'ENOUGH_EVIDENCE', met: true, current: 3.15, required: 1 },
            { code: 'STRONG_RESULTS', met: true, current: 0.806, required: 0.7 },
            { code: 'SUSTAINED_EVIDENCE', met: true, current: 3.15, required: 3 },
            { code: 'INDEPENDENT_APPLICATION', met: true, current: null, required: null },
          ],
        })}
      />,
    );
    expect(markup).toContain('Your recent independent evidence supports this skill.');
    expect(count(markup, 'data-met="true"')).toBe(4);
    expect(markup).toContain('1 not counted at your request');
  });
});

describe('debt panel', () => {
  it('shows no score when debt is not eligible', () => {
    const markup = html(<DebtPanel debt={debt()} />);
    expect(markup).toContain('There is not enough repeated AI delegation here to infer reliance.');
    expect(markup).not.toContain('data-testid="debt-factors"');
    expect(markup).not.toContain('Internal reliance score');
  });

  it('leads eligible debt with the band and its factors, the score only in internal detail', () => {
    const markup = html(<DebtPanel debt={ELIGIBLE_DEBT} />);
    expect(markup).toContain('Moderate reliance signal');
    expect(count(markup, 'data-testid="debt-factor"')).toBe(5);
    expect(markup).toContain('Delegation pressure');
    expect(markup).toContain('This is not a judgement of you or of using AI.');
    const beforeDetails = markup.slice(0, markup.indexOf('data-testid="debt-why"'));
    expect(beforeDetails).not.toContain('20.0');
    expect(markup).toContain('Internal reliance score 20.0 / 100');
  });
});

describe('evidence timeline', () => {
  it('reveals the exact span, confidences, reason code and source link behind "Why?"', () => {
    const markup = html(<EvidenceTimeline items={[evidenceItem()]} returnTo="/skills/x" submit={submit} />);
    const why = markup.slice(markup.indexOf('data-testid="evidence-why"'));
    expect(why).toContain('for name in names: print(name)');
    expect(why).toContain('data-testid="mapping-confidence">90%');
    expect(why).toContain('data-testid="attribution-confidence">95%');
    expect(why).toContain('STUDENT_WROTE_CODE');
    expect(why).toContain('href="/activity?focus=bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb,cccccccc-cccc-4ccc-8ccc-cccccccccccc"');
    expect(markup).toContain('Applied it yourself');
    expect(markup).toContain('Actor: You');
    expect(markup).toContain('Counts toward mastery');
    // A counted captured-activity event can be corrected both ways.
    expect(count(markup, 'data-testid="correction-form"')).toBe(2);
  });

  it('keeps excluded evidence visible, marked, and not correctable again', () => {
    const excluded = evidenceItem(
      { excluded: true, exclusion_reason: 'LEARNER_DONT_COUNT', excluded_at: '2026-09-25T11:00:00Z' },
      {
        correction: {
          id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
          action: 'DONT_COUNT',
          target_type: 'EVIDENCE_EVENT',
          target_id: '55555555-5555-4555-8555-555555555555',
          skill_id: null,
          mapping_id: null,
          segment_id: null,
          verdict: null,
          note: 'A classmate typed this.',
          excluded_evidence_ids: ['55555555-5555-4555-8555-555555555555'],
          recomputed_skill_ids: [],
          created_at: '2026-09-25T11:00:00Z',
        },
      },
    );
    const markup = html(<EvidenceTimeline items={[excluded]} returnTo="/skills/x" submit={submit} />);
    expect(markup).toContain('data-excluded="true"');
    expect(markup).toContain('You chose not to count this');
    expect(markup).toContain('A classmate typed this.');
    expect(markup).not.toContain('data-testid="correction-form"');
  });

  it('has an empty state', () => {
    expect(html(<EvidenceTimeline items={[]} returnTo="/x" submit={submit} />)).toContain('No evidence yet.');
  });
});

describe('activity list', () => {
  it('shows mapped skills, actor, evidence type and corrections; keeps the P1 test ids', () => {
    const markup = html(<ActivityList rows={[activityRow()]} returnTo="/activity" submit={submit} />);
    for (const id of ['activity-row', 'activity-role', 'activity-status', 'activity-preview', 'skill-chip']) {
      expect(markup).toContain(`data-testid="${id}"`);
    }
    expect(markup).toContain('Writing for loops');
    expect(markup).toContain('Actor: You');
    expect(markup).toContain('Applied it yourself');
    expect(markup).toContain('Matched to 1 skill');
    expect(markup).toContain('Evidence recorded');
    expect(markup).toContain('data-action="WRONG_SKILL"');
    expect(markup).toContain('data-action="DONT_COUNT"');
  });

  it('renders captured text as plain text and marks corrected mappings', () => {
    const row = activityRow({ preview: '<img src=x onerror=alert(1)>', content_chars: 28 });
    row.segments[0].mappings[0] = { ...row.segments[0].mappings[0], excluded: true, exclusion_reason: 'LEARNER_WRONG_SKILL' };
    const markup = html(<ActivityList rows={[row]} returnTo="/activity" submit={submit} />);
    expect(markup).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(markup).not.toContain('<img');
    expect(markup).toContain('You marked this as the wrong skill');
    expect(markup).not.toContain('data-action="WRONG_SKILL"');
  });

  it('points the answer of an analysed turn to its question', () => {
    const answer = activityRow({ id: OTHER_SKILL, role: 'assistant', segments: [], processing_outcome: 'ALREADY_ANALYZED' });
    const markup = html(<ActivityList rows={[answer]} returnTo="/activity" submit={submit} />);
    expect(markup).toContain('Analysed together with the message it answers.');
    expect(markup).toContain('Analysed with its turn');
  });
});

describe('recommendations, selector, forms and page states', () => {
  it('renders a recommendation with its skill link', () => {
    const verify = html(<RecommendationCard rec={recommendation({ type: 'VERIFY', reason_code: 'REPEATED_DELEGATION_UNVERIFIED', debt_band: 'MODERATE' })} />);
    expect(verify).toContain('Show you can do it on your own');
    expect(verify).toContain('href="/skills/11111111-1111-4111-8111-111111111111"');
    const prerequisite = html(
      <RecommendationCard
        rec={recommendation({ type: 'PREREQUISITE', reason_code: 'PREREQUISITE_GAP', mastery_state: 'DEVELOPING', related_skill_id: OTHER_SKILL, related_skill_name: 'Variables' })}
      />,
    );
    expect(prerequisite).toContain('Strengthen Variables first');
    expect(prerequisite).toContain(`href="/skills/${OTHER_SKILL}"`);
  });

  it('renders the course selector with the current course selected', () => {
    const markup = html(
      <CourseSelector
        courses={[{ id: COURSE, name: 'Python' }, { id: OTHER_SKILL, name: 'SQL' }]}
        selected={OTHER_SKILL}
        returnTo="/skills"
        action={async () => {}}
      />,
    );
    expect(markup).toContain('data-testid="course-selector"');
    expect(markup).toMatch(new RegExp(`<option value="${OTHER_SKILL}" selected="">SQL</option>`));
    expect(markup).toContain('name="return_to" value="/skills"');
    expect(html(<CourseSelector courses={[]} selected={null} returnTo="/" action={async () => {}} />)).toBe('');
  });

  it('asks for confirmation before a one-way correction and carries an idempotency key', () => {
    const markup = html(
      <CorrectionButton action="DONT_COUNT" targetType="EVIDENCE_EVENT" targetId={COURSE} returnTo="/x" submit={submit} />,
    );
    expect(markup).toContain("Don&#x27;t count this");
    expect(markup).not.toContain('data-testid="correction-confirm"');
    expect(markup).toMatch(/name="idempotency_key" value="fb-[0-9a-f-]{36}"/);
    const evaluation = html(<EvaluationForm targetType="SKILL" targetId={COURSE} returnTo="/x" submit={submit} />);
    expect(evaluation).toContain('name="action" value="EVALUATION"');
    expect(evaluation).toContain('Does this assessment look right to you?');
  });

  it('has empty, error and loading states', () => {
    expect(html(<EmptyState title="Nothing captured yet">Install the Companion.</EmptyState>)).toContain('Nothing captured yet');
    const error = html(<ErrorNotice title="Could not load" message="API unreachable" />);
    expect(error).toContain('role="alert"');
    expect(error).toContain('API unreachable');
    expect(html(<PageSkeleton title="Skill map" />)).toContain('aria-busy="true"');
  });
});
