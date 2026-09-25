/**
 * Teacher overview view models (P7, ADR 0008).
 *
 * Product rules carried here:
 * - UNKNOWN reads "Not enough evidence yet", keeps the neutral tone and comes first; it is never a
 *   low score and never ranks a skill.
 * - Skills keep the backend's order (topic, importance, name), never a weakness order.
 * - Only cohort counts exist: no student, no AI-usage number, no debt.
 */
import type { MasteryState, TeacherCohort, TeacherSkillRow } from '@skillmirror/contracts';

import { MASTERY, STATE_ORDER, type Tone } from './experience';

export const TEACHER_STATE_LABELS: Record<MasteryState, string> = {
  ...Object.fromEntries(STATE_ORDER.map((s) => [s, MASTERY[s].label])),
  UNKNOWN: 'Not enough evidence yet',
} as Record<MasteryState, string>;

export interface StateSegment {
  state: MasteryState;
  label: string;
  tone: Tone;
  count: number;
  /** Share of the cohort x skills, 0-100, rounded (0 when there is nothing to count). */
  percent: number;
}

export function stateSegments(states: Record<MasteryState, number>): StateSegment[] {
  const total = STATE_ORDER.reduce((sum, s) => sum + (states[s] ?? 0), 0);
  return STATE_ORDER.map((state) => ({
    state,
    label: TEACHER_STATE_LABELS[state],
    tone: MASTERY[state].tone,
    count: states[state] ?? 0,
    percent: total ? Math.round(((states[state] ?? 0) / total) * 100) : 0,
  }));
}

export function cohortMessage(cohort: TeacherCohort): string {
  const students = `${cohort.student_count} ${cohort.student_count === 1 ? 'student' : 'students'}`;
  if (cohort.suppressed) {
    return (
      `${students} enrolled. Course aggregates appear from ${cohort.min_cohort} students, ` +
      'so that no individual student can be singled out.'
    );
  }
  return `${students} enrolled. Every number below is a count across the class, never one student.`;
}

/** The per-skill states that are non-zero, in display order (for a compact table cell). */
export function nonZeroStates(row: Pick<TeacherSkillRow, 'states'>): StateSegment[] {
  return stateSegments(row.states).filter((s) => s.count > 0);
}
