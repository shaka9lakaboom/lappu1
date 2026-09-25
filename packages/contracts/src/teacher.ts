/**
 * Teacher + account contracts (P7; architecture §12.3, §13; ADR 0008).
 *
 * Mirrors services/backend/app/teacher/models.py and app/api/v1/me.py.
 * services/backend/tests/test_contract_parity.py checks the enum lists and the interface field
 * names. The teacher overview is cohort-level only: no per-student rows, learner ids or names, no
 * AI-usage counts and no debt scores. UNKNOWN is its own neutral count ("not enough evidence
 * yet"), never a weakness. Below the minimum cohort only the cohort size is shown.
 */

import type { CourseGraphStatus } from './courses';
import type { UserRole } from './index';
import type { MasteryState } from './intelligence';

/** The evidence the overview counts as the students' own work (plus VERIFICATION). */
export const INDEPENDENT_EVIDENCE_TYPES = [
  'INDEPENDENT_EXPLANATION',
  'INDEPENDENT_APPLICATION',
  'TRANSFER',
  'EXECUTION_RESULT',
  'TEACHER_EVIDENCE',
] as const;
export type IndependentEvidenceType = (typeof INDEPENDENT_EVIDENCE_TYPES)[number];

/** Body of GET /v1/me. The role is the database's, never a token claim. */
export interface MeCapabilities {
  student: boolean;
  teacher: boolean;
  admin: boolean;
}

export interface MeResponse {
  id: string;
  email: string | null;
  role: UserRole;
  display_name: string | null;
  timezone: string;
  taught_course_count: number;
  capabilities: MeCapabilities;
}

export interface TeacherCourse {
  id: string;
  name: string;
  subject: string | null;
  level: string | null;
  graph_status: CourseGraphStatus;
  graph_version: number;
  skill_count: number;
  student_count: number;
  /** True below the minimum cohort: the overview then shows the cohort size only. */
  suppressed: boolean;
}

/** Body of GET /v1/teacher/courses. */
export interface TeacherCoursesResponse {
  min_cohort: number;
  courses: TeacherCourse[];
}

export interface TeacherCohort {
  student_count: number;
  min_cohort: number;
  suppressed: boolean;
}

export interface TeacherSkillRow {
  skill_id: string;
  name: string;
  topic: string | null;
  importance: number;
  /** Students per mastery state (they add up to the cohort; no ledger row = UNKNOWN). */
  states: Record<MasteryState, number>;
  students_with_evidence: number;
  verification_need_students: number;
}

export interface TeacherMappedSkill {
  skill_id: string;
  name: string;
  /** Distinct students whose learning activity mapped to the skill in the window. */
  students: number;
}

export interface TeacherVerificationNeed {
  skill_id: string;
  name: string;
  /** Distinct students with an open verification suggestion for the skill. */
  students: number;
}

export interface TeacherEvidenceCounts {
  window_days: number;
  independent: number;
  verification: number;
  students_with_evidence: number;
  window_independent: number;
  window_verification: number;
}

/** Body of GET /v1/teacher/courses/{id}/overview. */
export interface TeacherCourseOverview {
  course: TeacherCourse;
  cohort: TeacherCohort;
  as_of: string | null;
  /** Null (and the lists empty) while the cohort is suppressed. */
  state_totals: Record<MasteryState, number> | null;
  skills: TeacherSkillRow[];
  common_mapped_skills: TeacherMappedSkill[];
  verification_needs: TeacherVerificationNeed[];
  evidence_counts: TeacherEvidenceCounts | null;
}
