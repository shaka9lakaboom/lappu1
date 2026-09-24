/**
 * Course + skill-graph contracts (architecture §7.1, §8, §13).
 *
 * Mirrors services/backend/app/courses/models.py and the enums of migration
 * 0003_courses_skill_graph.sql. services/backend/tests/test_contract_parity.py
 * checks that the enum lists agree. Change them together.
 */

export const COURSE_STATUSES = ['ACTIVE', 'ARCHIVED'] as const;
export type CourseStatus = (typeof COURSE_STATUSES)[number];

export const COURSE_GRAPH_STATUSES = ['PENDING', 'GENERATING', 'EMBEDDING', 'READY', 'FAILED'] as const;
export type CourseGraphStatus = (typeof COURSE_GRAPH_STATUSES)[number];

export const COURSE_MEMBER_ROLES = ['STUDENT', 'TEACHER'] as const;
export type CourseMemberRole = (typeof COURSE_MEMBER_ROLES)[number];

export const SKILL_NODE_KINDS = ['DOMAIN', 'SUBJECT', 'TOPIC', 'SKILL', 'SUBSKILL'] as const;
export type SkillNodeKind = (typeof SKILL_NODE_KINDS)[number];

export const SKILL_STATUSES = ['ACTIVE', 'CANDIDATE', 'DEPRECATED', 'MERGED'] as const;
export type SkillStatus = (typeof SKILL_STATUSES)[number];

export const SKILL_SOURCES = ['COURSE_BOOTSTRAP', 'CANDIDATE_APPROVAL', 'MANUAL', 'SEED'] as const;
export type SkillSource = (typeof SKILL_SOURCES)[number];

export const SKILL_EDGE_TYPES = ['PARENT', 'PREREQUISITE', 'RELATED'] as const;
export type SkillEdgeType = (typeof SKILL_EDGE_TYPES)[number];

export const ASSESSMENT_TYPES = ['mcq', 'numeric', 'code', 'sql', 'short_response', 'reasoning'] as const;
export type AssessmentType = (typeof ASSESSMENT_TYPES)[number];

/** Limits enforced by POST /v1/courses. */
export const COURSE_LIMITS = {
  nameMaxChars: 200,
  subjectMaxChars: 120,
  levelMaxChars: 60,
  descriptionMaxChars: 4000,
} as const;

/** Body of POST /v1/courses. Empty optional fields must be omitted or null. */
export interface CourseCreateRequest {
  name: string;
  subject?: string | null;
  level?: string | null;
  description?: string | null;
}

export interface Course {
  id: string;
  name: string;
  subject: string | null;
  level: string | null;
  description: string | null;
  status: CourseStatus;
  graph_status: CourseGraphStatus;
  graph_version: number;
  graph_error: string | null;
  graph_generated_at: string | null;
  role: CourseMemberRole;
  is_owner: boolean;
  /** Assessable (SKILL/SUBSKILL) nodes in the active overlay. */
  skill_count: number;
  bootstrap_job_state: string | null;
  created_at: string;
  updated_at: string;
}

export interface CourseCreateResponse {
  correlation_id: string;
  /** False when an Idempotency-Key replay returned an existing course. */
  created: boolean;
  bootstrap_job_id: string | null;
  course: Course;
}

export interface CourseListResponse {
  courses: Course[];
}

export interface CourseMembership {
  course_id: string;
  user_id: string;
  role: CourseMemberRole;
  created_at: string;
}

/** A canonical registry node. The UUID is immutable; names and aliases may change. */
export interface SkillNode {
  id: string;
  slug: string;
  canonical_name: string;
  description: string;
  node_kind: SkillNodeKind;
  status: SkillStatus;
  version: number;
  difficulty_band: number | null;
  assessment_types: AssessmentType[];
  aliases: string[];
}

/** A registry node in a course's overlay. */
export interface CourseSkill {
  skill: SkillNode;
  importance: number;
  source: SkillSource;
  active: boolean;
  graph_version: number;
  embedded: boolean;
}

/** PARENT: from is the parent of to. PREREQUISITE: from is required for to. RELATED: symmetric. */
export interface SkillEdge {
  from_skill_id: string;
  to_skill_id: string;
  edge_type: SkillEdgeType;
  weight: number;
}

export interface CourseSkillsResponse {
  course_id: string;
  graph_status: CourseGraphStatus;
  graph_version: number;
  skills: CourseSkill[];
  edges: SkillEdge[];
}
