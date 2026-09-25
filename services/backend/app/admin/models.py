"""Admin API contracts (architecture §13, §17; ADR 0008). Mirrored in
packages/contracts/src/admin.ts. Change both together.

Admins operate the pipeline; they never see captured text, evidence spans, prompts or model
output. `model_runs.output` and raw message content are never returned, and every error text is
redacted (app.core.redaction) and truncated. Mutations take an Idempotency-Key and write an
audit event in the same transaction; none makes a model call.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.auth.roles import AppRole
from app.courses.models import (
    CourseGraphStatus,
    CourseMemberRole,
    CourseStatus,
    SkillNodeKind,
    SkillSource,
    SkillStatus,
)
from app.experience.models import JobState
from app.intelligence.contracts import SkillCandidateStatus

JobRetryMode = Literal["RETRY", "RESUME_ATTRIBUTION"]
CandidateReviewAction = Literal["APPROVE", "MERGE", "REJECT"]
AuditActorType = Literal["USER", "OPERATOR"]
AuditAction = Literal[
    "JOB_RETRY",
    "JOB_RESUME_ATTRIBUTION",
    "CANDIDATE_APPROVE",
    "CANDIDATE_MERGE",
    "CANDIDATE_REJECT",
    "COURSE_MEMBER_ADD",
    "ROLE_CHANGE",
]
BenchmarkMode = Literal["DETERMINISTIC", "REPLAY", "LIVE"]
BenchmarkVerdict = Literal["PASS", "FAIL"]
ModelRunStatusName = Literal[
    "SUCCEEDED", "INVALID_OUTPUT", "FAILED", "TIMEOUT", "RATE_LIMITED", "UNAVAILABLE"
]

# Manual retries per job (the processing_jobs.manual_retry_count check of migration 0009).
MAX_MANUAL_RETRIES = 5
ERROR_PREVIEW_CHARS = 300

Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class SkillRef(BaseModel):
    id: UUID
    name: str


class CourseRef(BaseModel):
    id: UUID
    name: str


# --- Jobs -----------------------------------------------------------------------------------


class AdminJob(BaseModel):
    id: UUID
    job_type: str
    entity_type: str
    entity_id: UUID
    learner_id: UUID | None
    state: JobState
    attempts: int
    max_attempts: int
    outcome: str | None
    # Redacted and truncated (never raw exception text).
    last_error: str | None
    available_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    manual_retry_count: int
    last_manual_retry_at: datetime | None
    # What an admin can do with the job now, or why not.
    retry_mode: JobRetryMode | None
    retry_blocked: str | None


class AdminJobsResponse(BaseModel):
    counts: dict[JobState, int]
    jobs: list[AdminJob]
    # Pass as `before` to read the next (older) page; null on the last page.
    next_before: datetime | None


class JobRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: JobRetryMode = "RETRY"


class JobRetryResponse(BaseModel):
    job: AdminJob
    mode: JobRetryMode
    # The course graph stage a bootstrap retry resumes from (EMBEDDING = no regeneration).
    stage_restored: str | None
    replayed: bool
    audit_event_id: UUID


# --- Model runs -----------------------------------------------------------------------------


class AdminModelRun(BaseModel):
    """A model_runs row without its output (never returned) and with a redacted error."""

    id: UUID
    trace_id: str
    task_type: str
    provider: str
    model: str
    prompt_version: str
    status: ModelRunStatusName
    attempt: int
    error_code: str | None
    error_message: str | None
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    # A cache hit made no provider request (ADR 0004).
    cache_hit: bool
    course_id: UUID | None
    processing_job_id: UUID | None
    created_at: datetime


class ModelBudgetUsage(BaseModel):
    """Provider requests of the current quota day (cache hits excluded) against the budget."""

    provider: str
    model: str
    requests: int
    limit: int | None
    reserve: int
    available: int | None
    resets_at: datetime


class AdminModelRunsResponse(BaseModel):
    runs: list[AdminModelRun]
    next_before: datetime | None
    # Runs per status in the last 24 hours.
    status_counts_24h: dict[ModelRunStatusName, int]
    budget: list[ModelBudgetUsage]


# --- Skill candidates -------------------------------------------------------------------------


class SimilarSkill(BaseModel):
    id: UUID
    name: str
    score: float


class AdminSkillCandidate(BaseModel):
    id: UUID
    canonical_name: str
    normalized_name: str
    description: str | None
    status: SkillCandidateStatus
    occurrences: int
    parent: SkillRef | None
    first_course: CourseRef | None
    resolved_skill: SkillRef | None
    reviewed_at: datetime | None
    review_note: str | None
    # Mapping decisions that proposed it (provenance; the turns themselves are not shown).
    decision_count: int
    similar_skills: list[SimilarSkill]
    created_at: datetime
    updated_at: datetime


class AdminSkillCandidatesResponse(BaseModel):
    counts: dict[SkillCandidateStatus, int]
    candidates: list[AdminSkillCandidate]


class CandidateReviewRequest(BaseModel):
    """APPROVE: a new ACTIVE skill (optionally added to a course); MERGE: the name becomes an
    alias of `target_skill_id`; REJECT: the name stays out of the registry."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: CandidateReviewAction
    target_skill_id: UUID | None = None
    course_id: UUID | None = None
    description: Annotated[str, StringConstraints(min_length=8, max_length=600)] | None = None
    difficulty_band: int | None = Field(default=None, ge=1, le=5)
    importance: float | None = Field(default=None, ge=0, le=1)
    note: Note | None = None

    @model_validator(mode="after")
    def _shape(self) -> "CandidateReviewRequest":
        if (self.action == "MERGE") != (self.target_skill_id is not None):
            raise ValueError("target_skill_id is required for MERGE and only for MERGE")
        if self.action != "APPROVE" and (
            self.course_id
            or self.description
            or self.difficulty_band
            or self.importance is not None
        ):
            raise ValueError(
                "course_id, description, difficulty_band and importance are APPROVE-only"
            )
        return self


class CandidateReviewResponse(BaseModel):
    candidate: AdminSkillCandidate
    skill: SkillRef | None
    embed_job_id: UUID | None
    replayed: bool
    audit_event_id: UUID


# --- Benchmark ------------------------------------------------------------------------------


class BenchmarkRunSummary(BaseModel):
    id: UUID
    set_name: str
    set_version: str
    mode: BenchmarkMode
    provider: str | None
    model: str | None
    routing: str | None
    case_count: int
    passed_count: int
    failed_count: int
    blocked_count: int
    verdict: BenchmarkVerdict
    hard_gates: dict[str, Any]
    metrics: dict[str, Any]
    provider_requests: int
    embedding_requests: int
    code_sha: str | None
    started_at: datetime
    finished_at: datetime


class BenchmarkRunDetail(BenchmarkRunSummary):
    prompt_versions: dict[str, Any]
    policy_hash: str
    report: dict[str, Any]


class AdminBenchmarkResponse(BaseModel):
    # The newest run of each mode.
    latest: dict[BenchmarkMode, BenchmarkRunSummary]
    runs: list[BenchmarkRunSummary]


# --- Audit + overview -------------------------------------------------------------------------


class AuditEventSummary(BaseModel):
    id: UUID
    actor_type: AuditActorType
    actor_id: UUID | None
    actor_role: AppRole | None
    action: AuditAction
    entity_type: str
    entity_id: UUID
    metadata: dict[str, Any]
    created_at: datetime


class AdminTotals(BaseModel):
    learners: int
    courses: int
    active_skills: int
    evidence_events: int


class AdminOverview(BaseModel):
    jobs_by_state: dict[JobState, int]
    failed_by_type: dict[str, int]
    retryable_failed: int
    pending_candidates: int
    model_failures_24h: int
    budget: list[ModelBudgetUsage]
    latest_benchmark: BenchmarkRunSummary | None
    totals: AdminTotals
    recent_audit: list[AuditEventSummary]


# --- Course / skill lookup --------------------------------------------------------------------


class AdminCourse(BaseModel):
    id: UUID
    name: str
    subject: str | None
    level: str | None
    status: CourseStatus
    graph_status: CourseGraphStatus
    graph_version: int
    skill_count: int
    student_count: int
    teacher_count: int
    created_at: datetime


class AdminCoursesResponse(BaseModel):
    courses: list[AdminCourse]


class AdminMember(BaseModel):
    user_id: UUID
    role: CourseMemberRole
    profile_role: AppRole
    display_name: str | None
    email: str | None
    joined_at: datetime


class AdminCourseDetail(BaseModel):
    course: AdminCourse
    members: list[AdminMember]
    bootstrap_job: AdminJob | None


class CourseMemberAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: Annotated[str, StringConstraints(min_length=3, max_length=320)] | None = None
    user_id: UUID | None = None
    role: CourseMemberRole

    @model_validator(mode="after")
    def _one_identity(self) -> "CourseMemberAddRequest":
        if (self.email is None) == (self.user_id is None):
            raise ValueError("give exactly one of email or user_id")
        return self


class CourseMemberAddResponse(BaseModel):
    member: AdminMember
    created: bool
    replayed: bool
    audit_event_id: UUID


class AdminSkill(BaseModel):
    id: UUID
    slug: str
    canonical_name: str
    node_kind: SkillNodeKind
    status: SkillStatus
    difficulty_band: int | None
    source: SkillSource
    course_count: int
    alias_count: int
    embedded: bool


class AdminSkillsResponse(BaseModel):
    skills: list[AdminSkill]


class AdminSkillCourse(BaseModel):
    id: UUID
    name: str
    importance: float


class AdminSkillDetail(BaseModel):
    skill: AdminSkill
    description: str
    aliases: list[str]
    courses: list[AdminSkillCourse]
    parents: list[SkillRef]
    prerequisites: list[SkillRef]
    candidates: list[SkillRef]
