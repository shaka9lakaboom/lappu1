/**
 * Admin contracts (P7; architecture §13, §17; ADR 0008).
 *
 * Mirrors services/backend/app/admin/models.py and the enums of migration
 * 0009_teacher_admin_ops.sql. services/backend/tests/test_contract_parity.py checks the enum
 * lists and the interface field names. Admin payloads never carry captured text, prompts or model
 * output; error texts are redacted and truncated by the backend. Mutations take an
 * Idempotency-Key and write an audit event.
 */

import type {
  CourseGraphStatus,
  CourseMemberRole,
  CourseStatus,
  SkillNodeKind,
  SkillSource,
  SkillStatus,
} from './courses';
import type { UserRole } from './index';
import type { ModelRunStatus, SkillCandidateStatus } from './intelligence';
import type { JobState } from './raw-activity';

export const JOB_RETRY_MODES = ['RETRY', 'RESUME_ATTRIBUTION'] as const;
export type JobRetryMode = (typeof JOB_RETRY_MODES)[number];

export const CANDIDATE_REVIEW_ACTIONS = ['APPROVE', 'MERGE', 'REJECT'] as const;
export type CandidateReviewAction = (typeof CANDIDATE_REVIEW_ACTIONS)[number];

/** Matches `public.audit_actor_type`. */
export const AUDIT_ACTOR_TYPES = ['USER', 'OPERATOR'] as const;
export type AuditActorType = (typeof AUDIT_ACTOR_TYPES)[number];

/** Matches `public.audit_action`. */
export const AUDIT_ACTIONS = [
  'JOB_RETRY',
  'JOB_RESUME_ATTRIBUTION',
  'CANDIDATE_APPROVE',
  'CANDIDATE_MERGE',
  'CANDIDATE_REJECT',
  'COURSE_MEMBER_ADD',
  'ROLE_CHANGE',
] as const;
export type AuditAction = (typeof AUDIT_ACTIONS)[number];

/** Matches `public.benchmark_mode`. */
export const BENCHMARK_MODES = ['DETERMINISTIC', 'REPLAY', 'LIVE'] as const;
export type BenchmarkMode = (typeof BENCHMARK_MODES)[number];

/** Matches `public.benchmark_verdict`. */
export const BENCHMARK_VERDICTS = ['PASS', 'FAIL'] as const;
export type BenchmarkVerdict = (typeof BENCHMARK_VERDICTS)[number];

/** Manual retries per job (the processing_jobs check of migration 0009). */
export const MAX_MANUAL_RETRIES = 5;

export interface SkillRef {
  id: string;
  name: string;
}

export interface CourseRef {
  id: string;
  name: string;
}

// --- Jobs ------------------------------------------------------------------------------------

export interface AdminJob {
  id: string;
  job_type: string;
  entity_type: string;
  entity_id: string;
  learner_id: string | null;
  state: JobState;
  attempts: number;
  max_attempts: number;
  outcome: string | null;
  last_error: string | null;
  available_at: string;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  manual_retry_count: number;
  last_manual_retry_at: string | null;
  retry_mode: JobRetryMode | null;
  retry_blocked: string | null;
}

export interface AdminJobsResponse {
  counts: Partial<Record<JobState, number>>;
  jobs: AdminJob[];
  next_before: string | null;
}

/** Body of POST /v1/admin/jobs/{id}/retry (Idempotency-Key required). */
export interface JobRetryRequest {
  mode?: JobRetryMode;
}

export interface JobRetryResponse {
  job: AdminJob;
  mode: JobRetryMode;
  stage_restored: string | null;
  replayed: boolean;
  audit_event_id: string;
}

// --- Model runs ------------------------------------------------------------------------------

export interface AdminModelRun {
  id: string;
  trace_id: string;
  task_type: string;
  provider: string;
  model: string;
  prompt_version: string;
  status: ModelRunStatus;
  attempt: number;
  error_code: string | null;
  error_message: string | null;
  latency_ms: number;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cache_hit: boolean;
  course_id: string | null;
  processing_job_id: string | null;
  created_at: string;
}

export interface ModelBudgetUsage {
  provider: string;
  model: string;
  requests: number;
  limit: number | null;
  reserve: number;
  available: number | null;
  resets_at: string;
}

export interface AdminModelRunsResponse {
  runs: AdminModelRun[];
  next_before: string | null;
  status_counts_24h: Partial<Record<ModelRunStatus, number>>;
  budget: ModelBudgetUsage[];
}

// --- Skill candidates ------------------------------------------------------------------------

export interface SimilarSkill {
  id: string;
  name: string;
  score: number;
}

export interface AdminSkillCandidate {
  id: string;
  canonical_name: string;
  normalized_name: string;
  description: string | null;
  status: SkillCandidateStatus;
  occurrences: number;
  parent: SkillRef | null;
  first_course: CourseRef | null;
  resolved_skill: SkillRef | null;
  reviewed_at: string | null;
  review_note: string | null;
  decision_count: number;
  similar_skills: SimilarSkill[];
  created_at: string;
  updated_at: string;
}

export interface AdminSkillCandidatesResponse {
  counts: Partial<Record<SkillCandidateStatus, number>>;
  candidates: AdminSkillCandidate[];
}

/** Body of POST /v1/admin/skill-candidates/{id}/review (Idempotency-Key required). */
export interface CandidateReviewRequest {
  action: CandidateReviewAction;
  /** MERGE only. */
  target_skill_id?: string | null;
  /** APPROVE only. */
  course_id?: string | null;
  description?: string | null;
  difficulty_band?: number | null;
  importance?: number | null;
  note?: string | null;
}

export interface CandidateReviewResponse {
  candidate: AdminSkillCandidate;
  skill: SkillRef | null;
  embed_job_id: string | null;
  replayed: boolean;
  audit_event_id: string;
}

// --- Benchmark -------------------------------------------------------------------------------

export interface BenchmarkRunSummary {
  id: string;
  set_name: string;
  set_version: string;
  mode: BenchmarkMode;
  provider: string | null;
  model: string | null;
  routing: string | null;
  case_count: number;
  passed_count: number;
  failed_count: number;
  blocked_count: number;
  verdict: BenchmarkVerdict;
  hard_gates: Record<string, unknown>;
  metrics: Record<string, unknown>;
  provider_requests: number;
  embedding_requests: number;
  code_sha: string | null;
  started_at: string;
  finished_at: string;
  // P9 presentation, derived from the stored row; the stored verdict is never rewritten.
  hard_gates_total: number;
  hard_gates_failed: string[];
  hard_gate_failure_cases: number;
  /** Failed cases that broke no hard gate (LIVE / REPLAY: they did not complete). */
  failed_without_hard_gate: number;
  failing_cases: string[];
  /** Failing case id -> error code (e.g. ModelUnavailableError(TRANSPORT)); null when not recorded. */
  case_errors: Record<string, string> | null;
  /** Where the error codes come from: the run report, or the saved runner report (sha256). */
  case_errors_source: string | null;
  /** Failing cases whose error is a provider / transport error; null when not recorded. */
  provider_failure_cases: string[] | null;
}

export interface BenchmarkRunDetail extends BenchmarkRunSummary {
  prompt_versions: Record<string, unknown>;
  policy_hash: string;
  report: Record<string, unknown>;
}

export interface AdminBenchmarkResponse {
  latest: Partial<Record<BenchmarkMode, BenchmarkRunSummary>>;
  runs: BenchmarkRunSummary[];
}

// --- Audit + overview -------------------------------------------------------------------------

export interface AuditEventSummary {
  id: string;
  actor_type: AuditActorType;
  actor_id: string | null;
  actor_role: UserRole | null;
  action: AuditAction;
  entity_type: string;
  entity_id: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AdminTotals {
  learners: number;
  courses: number;
  active_skills: number;
  evidence_events: number;
}

export interface AdminOverview {
  jobs_by_state: Partial<Record<JobState, number>>;
  failed_by_type: Record<string, number>;
  retryable_failed: number;
  pending_candidates: number;
  model_failures_24h: number;
  budget: ModelBudgetUsage[];
  latest_benchmark: BenchmarkRunSummary | null;
  totals: AdminTotals;
  recent_audit: AuditEventSummary[];
}

// --- Course / skill lookup ----------------------------------------------------------------------

export interface AdminCourse {
  id: string;
  name: string;
  subject: string | null;
  level: string | null;
  status: CourseStatus;
  graph_status: CourseGraphStatus;
  graph_version: number;
  skill_count: number;
  student_count: number;
  teacher_count: number;
  created_at: string;
}

export interface AdminCoursesResponse {
  courses: AdminCourse[];
}

export interface AdminMember {
  user_id: string;
  role: CourseMemberRole;
  profile_role: UserRole;
  display_name: string | null;
  email: string | null;
  joined_at: string;
}

export interface AdminCourseDetail {
  course: AdminCourse;
  members: AdminMember[];
  bootstrap_job: AdminJob | null;
}

/** Body of POST /v1/admin/courses/{id}/members (exactly one of email / user_id). */
export interface CourseMemberAddRequest {
  email?: string | null;
  user_id?: string | null;
  role: CourseMemberRole;
}

export interface CourseMemberAddResponse {
  member: AdminMember;
  created: boolean;
  replayed: boolean;
  audit_event_id: string;
}

export interface AdminSkill {
  id: string;
  slug: string;
  canonical_name: string;
  node_kind: SkillNodeKind;
  status: SkillStatus;
  difficulty_band: number | null;
  source: SkillSource;
  course_count: number;
  alias_count: number;
  embedded: boolean;
}

export interface AdminSkillsResponse {
  skills: AdminSkill[];
}

export interface AdminSkillCourse {
  id: string;
  name: string;
  importance: number;
}

export interface AdminSkillDetail {
  skill: AdminSkill;
  description: string;
  aliases: string[];
  courses: AdminSkillCourse[];
  parents: SkillRef[];
  prerequisites: SkillRef[];
  candidates: SkillRef[];
}
