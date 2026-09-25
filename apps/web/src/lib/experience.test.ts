import type { CourseSkill, CourseSkillsResponse, SkillNode } from '@skillmirror/contracts';
import { MASTERY_STATES, RECOMMENDATION_TYPES } from '@skillmirror/contracts';
import { describe, expect, it } from 'vitest';

import { resolveSelectedCourse } from './course-selection';
import {
  DEBT_BAND,
  MASTERY,
  NOT_ENOUGH_DELEGATION,
  STATE_ORDER,
  TONE_CLASSES,
  countStates,
  debtSummary,
  exclusionLabel,
  explanationDetail,
  factorText,
  independenceLabel,
  overlaySkillMap,
  percent,
  processingOutcomeLabel,
  recommendationText,
  segmentOutcomeLabel,
} from './experience';
import { buildFeedbackRequest } from './feedback';
import { COURSE, ELIGIBLE_DEBT, OTHER_SKILL, SKILL, activityRow, debt, ledgerEntry } from './test-data';

describe('mastery presentation', () => {
  it('shows UNKNOWN neutrally as "Not enough activity yet"', () => {
    expect(MASTERY.UNKNOWN.label).toBe('Not enough activity yet');
    expect(MASTERY.UNKNOWN.tone).toBe('neutral');
    expect(MASTERY.UNKNOWN.headline).toBe('SkillMirror does not have enough independent evidence yet.');
    expect(MASTERY.DEMONSTRATED.headline).toBe('Your recent independent evidence supports this skill.');
  });

  it('never presents a skill state or debt band as failure (no red)', () => {
    const classes = Object.values(TONE_CLASSES).join(' ');
    expect(classes).not.toMatch(/red-|destructive|rose-/);
    for (const state of MASTERY_STATES) expect(MASTERY[state].label).not.toMatch(/fail|weak|poor|low/i);
  });

  it('covers every state, UNKNOWN first', () => {
    expect([...STATE_ORDER].sort()).toEqual([...MASTERY_STATES].sort());
    expect(STATE_ORDER[0]).toBe('UNKNOWN');
  });

  it('explains every mastery code in plain language', () => {
    for (const code of [
      'NO_EVIDENCE',
      'NO_INDEPENDENT_PERFORMANCE',
      'NOT_ENOUGH_SUPPORT',
      'EARLY_DIFFICULTY',
      'MIXED_RESULTS',
      'NEEDS_MORE_EVIDENCE',
      'NEEDS_INDEPENDENT_APPLICATION',
      'INDEPENDENT_EVIDENCE_SUPPORTS',
      'RECENT_VERIFICATION',
      'VERIFICATION_STALE',
    ]) {
      expect(explanationDetail(code)).not.toBe('SkillMirror derived this state from your evidence.');
    }
    expect(explanationDetail('NO_EVIDENCE')).toContain('says nothing about your ability');
  });
});

describe('AI Assistance Debt', () => {
  it('explains ineligible debt without a score', () => {
    expect(debtSummary(debt()).headline).toBe(NOT_ENOUGH_DELEGATION);
    expect(NOT_ENOUGH_DELEGATION).toBe('There is not enough repeated AI delegation here to infer reliance.');
    const once = debtSummary(debt({ recent_delegation_count: 1, eligibility_code: 'INSUFFICIENT_DELEGATION' }));
    expect(once.detail).toContain('once');
    expect(once.detail).toContain('at least 2');
  });

  it('leads eligible debt with a qualitative band and never the number', () => {
    const summary = debtSummary(ELIGIBLE_DEBT);
    expect(summary.headline).toBe('Moderate reliance signal');
    expect(`${summary.headline} ${summary.detail}`).not.toContain('20');
    expect(DEBT_BAND.HIGH.label).toBe('High reliance signal');
    expect(Object.values(DEBT_BAND).map((b) => b.label).join(' ')).not.toMatch(/debt score|cheat|bad/i);
  });

  it('describes each contributing factor', () => {
    const texts = ELIGIBLE_DEBT.factors.map((f) => factorText(f, 'UNVERIFIED'));
    expect(texts[0]).toMatch(/^Delegation pressure/);
    expect(texts[1]).toMatch(/^Independent evidence gap/);
    expect(texts[2]).toMatch(/^Importance/);
    expect(texts[3]).toMatch(/^Confidence/);
    expect(texts[4]).toMatch(/Not verified yet\.$/);
  });
});

describe('recommendations', () => {
  it('has a title and body for every Engine 16 action', () => {
    for (const type of RECOMMENDATION_TYPES) {
      const text = recommendationText({ type, reason_code: 'X', related_skill_name: 'Variables' });
      expect(text.title.length).toBeGreaterThan(3);
      expect(text.body.length).toBeGreaterThan(10);
    }
    expect(recommendationText({ type: 'PREREQUISITE', reason_code: 'PREREQUISITE_GAP', related_skill_name: 'Variables' }).title).toBe(
      'Strengthen Variables first',
    );
    expect(recommendationText({ type: 'NO_ACTION', reason_code: 'NOT_ENOUGH_EVIDENCE', related_skill_name: null }).title).toBe(
      'Nothing to do yet',
    );
  });
});

describe('dashboard counts and skill map', () => {
  it('counts states per course', () => {
    const entries = [
      ledgerEntry({ skill_id: 'a', mastery_state: 'DEMONSTRATED' }),
      ledgerEntry({ skill_id: 'b', mastery_state: 'UNKNOWN' }),
      ledgerEntry({ skill_id: 'c', mastery_state: 'UNKNOWN', course_ids: ['other'] }),
    ];
    expect(countStates(entries)).toMatchObject({ DEMONSTRATED: 1, UNKNOWN: 2, VERIFIED: 0 });
    expect(countStates(entries, COURSE)).toMatchObject({ DEMONSTRATED: 1, UNKNOWN: 1 });
  });

  it('overlays the ledger on the graph; a skill without a ledger entry is UNKNOWN, not 0%', () => {
    const node = (id: string, name: string, kind: SkillNode['node_kind'] = 'SKILL'): CourseSkill => ({
      skill: {
        id,
        slug: id,
        canonical_name: name,
        description: 'd',
        node_kind: kind,
        status: 'ACTIVE',
        version: 1,
        difficulty_band: 2,
        assessment_types: [],
        aliases: [],
      },
      importance: 0.5,
      source: 'COURSE_BOOTSTRAP',
      active: true,
      graph_version: 1,
      embedded: true,
    });
    const graph: CourseSkillsResponse = {
      course_id: COURSE,
      graph_status: 'READY',
      graph_version: 1,
      skills: [node('topic', 'Loops', 'TOPIC'), node(SKILL, 'For loops'), node(OTHER_SKILL, 'While loops')],
      edges: [
        { from_skill_id: 'topic', to_skill_id: SKILL, edge_type: 'PARENT', weight: 1 },
        { from_skill_id: 'topic', to_skill_id: OTHER_SKILL, edge_type: 'PARENT', weight: 1 },
      ],
    };
    const groups = overlaySkillMap(graph, [ledgerEntry({ skill_id: SKILL, mastery_state: 'DEMONSTRATED', mastery_mean: 0.8 })]);
    expect(groups).toHaveLength(1);
    expect(groups[0].topic?.canonical_name).toBe('Loops');
    const states = Object.fromEntries(groups[0].skills.map((s) => [s.entry.skill.id, s.state]));
    expect(states).toEqual({ [SKILL]: 'DEMONSTRATED', [OTHER_SKILL]: 'UNKNOWN' });
    expect(groups[0].skills.find((s) => s.entry.skill.id === OTHER_SKILL)?.ledger).toBeNull();
  });
});

describe('activity and evidence labels', () => {
  it('labels route outcomes, processing outcomes and exclusions', () => {
    const segment = activityRow().segments[0];
    expect(segmentOutcomeLabel(segment)).toBe('Matched to 1 skill');
    expect(segmentOutcomeLabel({ ...segment, mapping_outcome: 'ABSTAINED', mappings: [] })).toBe(
      'No confident skill match (no evidence)',
    );
    expect(segmentOutcomeLabel({ ...segment, route: 'STOP' })).toBe('Not learning activity (ignored)');
    expect(processingOutcomeLabel('EVIDENCE_RECORDED')).toBe('Analysed — see the matched skills for what counts');
    expect(processingOutcomeLabel(null)).toBeNull();
    expect(exclusionLabel('LEARNER_WRONG_SKILL')).toBe('You marked this as the wrong skill');
    expect(exclusionLabel('LEARNER_DONT_COUNT')).toBe('You chose not to count this');
    expect([independenceLabel(1), independenceLabel(0.3), independenceLabel(0)]).toEqual([
      'Independent',
      'Assisted',
      'Not independent',
    ]);
    expect([percent(0.9), percent(null)]).toEqual(['90%', '—']);
  });
});

describe('course selector', () => {
  const courses = [{ id: COURSE }, { id: '44444444-4444-4444-8444-444444444444' }];

  it('prefers ?course=, then the cookie, then the first course', () => {
    expect(resolveSelectedCourse(courses, courses[1].id)).toBe(courses[1].id);
    expect(resolveSelectedCourse(courses, courses[1].id, COURSE)).toBe(COURSE);
    expect(resolveSelectedCourse(courses, undefined)).toBe(COURSE);
    expect(resolveSelectedCourse([], COURSE)).toBeNull();
  });

  it("ignores ids that are not one of the learner's courses", () => {
    expect(resolveSelectedCourse(courses, 'ffffffff-ffff-4fff-8fff-ffffffffffff')).toBe(COURSE);
    expect(resolveSelectedCourse(courses, 'not-a-uuid', ['also bad'])).toBe(COURSE);
  });
});

describe('feedback form', () => {
  const form = (fields: Record<string, string>) => {
    const data = new FormData();
    for (const [k, v] of Object.entries(fields)) data.set(k, v);
    return data;
  };
  const base = { action: 'DONT_COUNT', target_type: 'EVIDENCE_EVENT', target_id: SKILL, idempotency_key: 'fb-1' };

  it('builds a correction with its idempotency key', () => {
    expect(buildFeedbackRequest(form(base))).toEqual({
      ok: true,
      request: { action: 'DONT_COUNT', target_type: 'EVIDENCE_EVENT', target_id: SKILL },
      idempotencyKey: 'fb-1',
    });
    expect(buildFeedbackRequest(form({ ...base, action: 'EVALUATION', target_type: 'SKILL', verdict: 'AGREE', note: ' ok ' }))).toEqual({
      ok: true,
      request: { action: 'EVALUATION', target_type: 'SKILL', target_id: SKILL, verdict: 'AGREE', note: 'ok' },
      idempotencyKey: 'fb-1',
    });
  });

  it('refuses what the API would refuse', () => {
    expect(buildFeedbackRequest(form({ ...base, action: 'WRONG_SKILL', target_type: 'ACTIVITY_SEGMENT' }))).toMatchObject({ ok: false });
    expect(buildFeedbackRequest(form({ ...base, target_id: 'nope' }))).toMatchObject({ ok: false });
    expect(buildFeedbackRequest(form({ ...base, idempotency_key: '' }))).toMatchObject({ ok: false });
    expect(buildFeedbackRequest(form({ ...base, action: 'EVALUATION', target_type: 'SKILL' }))).toMatchObject({ ok: false });
    expect(buildFeedbackRequest(form({ ...base, note: 'x'.repeat(2001) }))).toMatchObject({ ok: false });
    expect(buildFeedbackRequest(form({ ...base, action: 'DELETE' }))).toMatchObject({ ok: false });
  });
});
