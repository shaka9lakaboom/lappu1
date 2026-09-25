/**
 * P9: learner-facing wording follows the RECORDED evidence and the recorded classification.
 *
 * - "Who demonstrated this skill?" never contradicts what the evidence says happened.
 * - A turn is never summarised as a bare "Evidence recorded" when the evidence gave no mastery
 *   credit (AI assistance, exposure).
 * - Learning-related turns without skill evidence are not called "not learning"; true
 *   non-learning turns still are.
 */
import type { ActivitySegment, EvidenceType } from '@skillmirror/contracts';
import { EVIDENCE_TYPES } from '@skillmirror/contracts';
import { describe, expect, it } from 'vitest';

import {
  EVIDENCE_KIND,
  EVIDENCE_TYPE_LABEL,
  LOW_RELEVANCE_LABEL,
  evidenceKind,
  processingOutcomeLabel,
  segmentOutcomeLabel,
  turnEvidenceSummary,
  turnOutcomeLabel,
  whoLabel,
} from './experience';
import { activityRow } from './test-data';

function segmentWith(types: Array<EvidenceType | null>, excluded = false): ActivitySegment {
  const base = activityRow().segments[0];
  return {
    ...base,
    mappings: types.map((type, i) => ({
      ...base.mappings[0],
      mapping_id: `88888888-8888-4888-8888-88888888888${i}`,
      evidence_type: type,
      actor: type === null ? null : type === 'OBSERVATION' || type === 'EXPOSURE' ? 'AI' : type === 'ASSISTED_ATTEMPT' ? 'SHARED' : 'STUDENT',
      excluded,
    })),
  };
}

function unit(route: ActivitySegment['route'], relevance: ActivitySegment['learning_relevance']): ActivitySegment {
  return { ...activityRow().segments[0], route, learning_relevance: relevance, mapping_outcome: null, mappings: [] };
}

describe('who demonstrated the skill (recorded evidence only)', () => {
  it('has a kind, a who and an effect for every evidence type', () => {
    for (const type of EVIDENCE_TYPES) {
      const kind = EVIDENCE_KIND[evidenceKind(type)];
      expect(kind.effect.length).toBeGreaterThan(0);
      expect(whoLabel(type).length).toBeGreaterThan(0);
    }
  });

  it('never pairs "You" with an AI performance, nor "AI" with your own work', () => {
    for (const type of EVIDENCE_TYPES) {
      const who = whoLabel(type);
      const what = EVIDENCE_TYPE_LABEL[type];
      if (who === 'Demonstrated by: You') expect(what).not.toMatch(/The AI|explanation/i);
      if (who === 'Demonstrated by: AI') expect(what).not.toMatch(/yourself/i);
    }
    expect(whoLabel('OBSERVATION')).toBe('Demonstrated by: AI');
    expect(whoLabel('INDEPENDENT_APPLICATION')).toBe('Demonstrated by: You');
    expect(whoLabel('ASSISTED_ATTEMPT')).toBe('Demonstrated by: You + AI');
    expect(whoLabel('EXPOSURE')).toBe('Explanation seen');
    expect(whoLabel('VERIFICATION')).toBe('Demonstrated by: You');
  });

  it('says what the evidence does to mastery', () => {
    expect(EVIDENCE_KIND[evidenceKind('OBSERVATION')].effect).toContain('no mastery credit');
    expect(EVIDENCE_KIND[evidenceKind('EXPOSURE')].effect).toContain('does not affect mastery');
    expect(EVIDENCE_KIND[evidenceKind('INDEPENDENT_EXPLANATION')].effect).toContain('counts toward mastery');
    expect(EVIDENCE_KIND[evidenceKind('ASSISTED_ATTEMPT')].effect).toContain('reduced weight');
  });
});

describe('turn outcome wording', () => {
  it('summarises a turn by its recorded evidence, never as a bare "Evidence recorded"', () => {
    expect(turnEvidenceSummary([segmentWith(['INDEPENDENT_APPLICATION'])])).toBe('Independent learner evidence recorded');
    expect(turnEvidenceSummary([segmentWith(['OBSERVATION'])])).toBe('AI-assistance evidence recorded — no mastery credit');
    expect(turnEvidenceSummary([segmentWith(['EXPOSURE'])])).toBe('Exposure only — does not affect mastery');
    expect(turnEvidenceSummary([segmentWith(['ASSISTED_ATTEMPT'])])).toBe('Assisted-attempt evidence recorded (you + AI)');
    // The learner's own explanation next to the AI's loop: the learner evidence is what counts.
    expect(turnEvidenceSummary([segmentWith(['OBSERVATION', 'INDEPENDENT_EXPLANATION'])])).toBe(
      'Independent learner evidence recorded',
    );
    expect(turnEvidenceSummary([segmentWith(['INDEPENDENT_APPLICATION'], true)])).toBe(
      'Evidence recorded — not counted, at your request',
    );
    expect(turnEvidenceSummary([segmentWith([null])])).toBeNull();
    for (const outcome of ['EVIDENCE_RECORDED', 'MAPPED_NO_EVIDENCE']) {
      expect(turnOutcomeLabel(outcome, null)).not.toBe('Evidence recorded');
      expect(turnOutcomeLabel(outcome, [segmentWith(['OBSERVATION'])])).not.toBe('Evidence recorded');
    }
    expect(turnOutcomeLabel('EVIDENCE_RECORDED', [segmentWith(['OBSERVATION'])])).toContain('no mastery credit');
  });

  it('labels the other outcomes plainly', () => {
    expect(processingOutcomeLabel('DEFERRED_TO_ASSISTANT')).toBe("Analysed together with the AI's reply");
    expect(processingOutcomeLabel('MAPPED_NO_EVIDENCE')).toBe('Matched to a skill — no skill evidence qualified');
    expect(processingOutcomeLabel('ALREADY_ANALYZED')).toBe('Analysed with its turn');
    expect(processingOutcomeLabel(null)).toBeNull();
  });
});

describe('learning-related but no skill evidence (the recorded classification)', () => {
  it('shows LEARNING_RELEVANT + NOT_SKILL_BEARING (METADATA_ONLY) as learning activity without evidence', () => {
    const meta = unit('METADATA_ONLY', 'high');
    expect(segmentOutcomeLabel(meta)).toBe('Learning activity — no skill evidence');
    expect(turnOutcomeLabel('METADATA_ONLY', [meta])).toBe('Learning activity — no skill evidence');
  });

  it('does not call a low-relevance learning question (e.g. "what is python") "not learning"', () => {
    const low = unit('STOP', 'low');
    expect(segmentOutcomeLabel(low)).toBe(LOW_RELEVANCE_LABEL);
    expect(turnOutcomeLabel('NON_LEARNING', [low])).toBe(LOW_RELEVANCE_LABEL);
    expect(LOW_RELEVANCE_LABEL).toContain('no skill evidence');
  });

  it('keeps a true NON_LEARNING turn as "Not learning activity"', () => {
    const none = unit('STOP', 'none');
    expect(segmentOutcomeLabel(none)).toBe('Not learning activity (ignored)');
    expect(turnOutcomeLabel('NON_LEARNING', [none])).toBe('Not learning activity');
    expect(turnOutcomeLabel('NON_LEARNING', null)).toBe('Not learning activity');
  });
});
