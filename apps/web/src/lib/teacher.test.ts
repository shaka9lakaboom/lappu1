import { describe, expect, it } from 'vitest';

import { STATE_ORDER } from './experience';
import { TEACHER_STATE_LABELS, cohortMessage, nonZeroStates, stateSegments } from './teacher';

const states = { UNKNOWN: 6, EMERGING: 3, DEVELOPING: 3, DEMONSTRATED: 3, VERIFIED: 0, NEEDS_REVERIFICATION: 0 };

describe('teacher state segments', () => {
  it('keeps UNKNOWN first, neutral, and labelled as missing evidence (never weak)', () => {
    const segments = stateSegments(states);
    expect(segments.map((s) => s.state)).toEqual(STATE_ORDER);
    expect(segments[0]).toMatchObject({ state: 'UNKNOWN', label: 'Not enough evidence yet', tone: 'neutral', count: 6 });
    expect(TEACHER_STATE_LABELS.UNKNOWN).not.toMatch(/weak|low|poor|fail/i);
    expect(segments.map((s) => s.percent)).toEqual([40, 20, 20, 20, 0, 0]);
  });

  it('handles an empty distribution without dividing by zero', () => {
    const empty = stateSegments({ UNKNOWN: 0, EMERGING: 0, DEVELOPING: 0, DEMONSTRATED: 0, VERIFIED: 0, NEEDS_REVERIFICATION: 0 });
    expect(empty.every((s) => s.percent === 0)).toBe(true);
  });

  it('lists only the states that occur', () => {
    expect(nonZeroStates({ states }).map((s) => s.state)).toEqual(['UNKNOWN', 'EMERGING', 'DEVELOPING', 'DEMONSTRATED']);
  });
});

describe('cohort message', () => {
  it('explains suppression below the minimum cohort', () => {
    const text = cohortMessage({ student_count: 2, min_cohort: 3, suppressed: true });
    expect(text).toContain('2 students');
    expect(text).toContain('from 3 students');
  });

  it('says the numbers are class counts', () => {
    expect(cohortMessage({ student_count: 1, min_cohort: 3, suppressed: true })).toContain('1 student ');
    expect(cohortMessage({ student_count: 24, min_cohort: 3, suppressed: false })).toContain('never one student');
  });
});
