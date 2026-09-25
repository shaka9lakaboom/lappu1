"""Admin endpoints (architecture §13, §17; ADR 0008). ADMIN profile only (403 otherwise).

    GET  /v1/admin/overview                       ops tiles
    GET  /v1/admin/jobs[/{id}]                    processing jobs (redacted errors)
    POST /v1/admin/jobs/{id}/retry                RETRY | RESUME_ATTRIBUTION  (Idempotency-Key)
    GET  /v1/admin/model-runs                     model runs without output + request budget
    GET  /v1/admin/skill-candidates               candidate review queue
    POST /v1/admin/skill-candidates/{id}/review   APPROVE | MERGE | REJECT     (Idempotency-Key)
    GET  /v1/admin/benchmark[/{id}]               benchmark runs (P8)
    GET  /v1/admin/courses[/{id}]                 course lookup, members
    POST /v1/admin/courses/{id}/members           enroll a STUDENT / TEACHER    (Idempotency-Key)
    GET  /v1/admin/skills[/{id}]                  skill lookup

No endpoint makes a model call or returns captured text, prompts or model output. Every
mutation writes an audit event in its transaction; a retry with the same Idempotency-Key
returns the original result (200), the same key with another request is 409.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.admin.audit import IdempotencyConflictError
from app.admin.candidates import (
    CandidateNotFoundError,
    CandidateReviewError,
    list_candidates,
    review_candidate,
)
from app.admin.jobs import JobNotFoundError, JobNotRetryableError, get_job, list_jobs, retry_job
from app.admin.lookup import (
    LookupNotFoundError,
    MembershipError,
    add_member,
    course_detail,
    search_courses,
    search_skills,
    skill_detail,
)
from app.admin.models import (
    AdminBenchmarkResponse,
    AdminCourseDetail,
    AdminCoursesResponse,
    AdminJob,
    AdminJobsResponse,
    AdminModelRunsResponse,
    AdminOverview,
    AdminSkillCandidatesResponse,
    AdminSkillDetail,
    AdminSkillsResponse,
    BenchmarkRunDetail,
    CandidateReviewRequest,
    CandidateReviewResponse,
    CourseMemberAddRequest,
    CourseMemberAddResponse,
    JobRetryRequest,
    JobRetryResponse,
    ModelRunStatusName,
)
from app.admin.monitoring import (
    admin_overview,
    get_benchmark_run,
    list_benchmark_runs,
    list_model_runs,
)
from app.auth.roles import AdminActor
from app.core.config import Settings, get_settings
from app.db.pool import DbPool
from app.experience.models import JobState
from app.intelligence.contracts import SkillCandidateStatus
from app.intelligence.policy import load_policy

router = APIRouter(prefix="/admin", tags=["admin"])

IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9._:-]{1,128}$")
]
AppSettings = Annotated[Settings, Depends(get_settings)]

_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Storage is temporarily unavailable; retry later.",
)


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found.")


def _conflict_key() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="This Idempotency-Key was already used for a different request.",
    )


@router.get("/overview", response_model=AdminOverview)
def get_overview(actor: AdminActor, pool: DbPool, settings: AppSettings) -> AdminOverview:
    try:
        with pool.connection() as conn:
            return admin_overview(conn, settings)
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc


@router.get("/jobs", response_model=AdminJobsResponse)
def get_jobs(
    actor: AdminActor,
    pool: DbPool,
    state: JobState | None = None,
    job_type: Annotated[str | None, Query(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")] = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdminJobsResponse:
    try:
        with pool.connection() as conn:
            return list_jobs(conn, state=state, job_type=job_type, before=before, limit=limit)
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc


@router.get("/jobs/{job_id}", response_model=AdminJob)
def get_job_detail(job_id: UUID, actor: AdminActor, pool: DbPool) -> AdminJob:
    try:
        with pool.connection() as conn:
            job = get_job(conn, job_id)
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    if job is None:
        raise _not_found("Job")
    return job


@router.post("/jobs/{job_id}/retry", response_model=JobRetryResponse)
def post_job_retry(
    job_id: UUID,
    actor: AdminActor,
    pool: DbPool,
    idempotency_key: IdempotencyKey,
    request: JobRetryRequest | None = None,
) -> JobRetryResponse:
    mode = (request or JobRetryRequest()).mode
    try:
        with pool.connection() as conn:
            outcome = retry_job(conn, actor, job_id, mode, idempotency_key)
    except JobNotFoundError as exc:
        raise _not_found("Job") from exc
    except JobNotRetryableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"{exc.code}: {exc}"
        ) from exc
    except IdempotencyConflictError as exc:
        raise _conflict_key() from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    return JobRetryResponse(
        job=outcome.job,
        mode=outcome.mode,
        stage_restored=outcome.stage_restored,
        replayed=outcome.replayed,
        audit_event_id=outcome.audit_event_id,
    )


@router.get("/model-runs", response_model=AdminModelRunsResponse)
def get_model_runs(
    actor: AdminActor,
    pool: DbPool,
    settings: AppSettings,
    failures_only: bool = False,
    status_filter: Annotated[ModelRunStatusName | None, Query(alias="status")] = None,
    task_type: Annotated[str | None, Query(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")] = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdminModelRunsResponse:
    try:
        with pool.connection() as conn:
            return list_model_runs(
                conn,
                settings,
                failures_only=failures_only,
                status=status_filter,
                task_type=task_type,
                before=before,
                limit=limit,
            )
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc


@router.get("/skill-candidates", response_model=AdminSkillCandidatesResponse)
def get_skill_candidates(
    actor: AdminActor,
    pool: DbPool,
    status_filter: Annotated[
        SkillCandidateStatus | Literal["ALL"], Query(alias="status")
    ] = "PENDING_REVIEW",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AdminSkillCandidatesResponse:
    try:
        with pool.connection() as conn:
            return list_candidates(
                conn, status=None if status_filter == "ALL" else status_filter, limit=limit
            )
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc


@router.post("/skill-candidates/{candidate_id}/review", response_model=CandidateReviewResponse)
def post_candidate_review(
    candidate_id: UUID,
    request: CandidateReviewRequest,
    actor: AdminActor,
    pool: DbPool,
    idempotency_key: IdempotencyKey,
) -> CandidateReviewResponse:
    try:
        with pool.connection() as conn:
            importance = load_policy(conn).skill_graph.default_importance
            outcome = review_candidate(
                conn, actor, candidate_id, request, idempotency_key, default_importance=importance
            )
    except CandidateNotFoundError as exc:
        raise _not_found("Skill candidate") from exc
    except CandidateReviewError as exc:
        raise HTTPException(status_code=exc.status, detail=f"{exc.code}: {exc}") from exc
    except IdempotencyConflictError as exc:
        raise _conflict_key() from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    return CandidateReviewResponse(
        candidate=outcome.candidate,
        skill=outcome.skill,
        embed_job_id=outcome.embed_job_id,
        replayed=outcome.replayed,
        audit_event_id=outcome.audit_event_id,
    )


@router.get("/benchmark", response_model=AdminBenchmarkResponse)
def get_benchmark(actor: AdminActor, pool: DbPool) -> AdminBenchmarkResponse:
    try:
        with pool.connection() as conn:
            return list_benchmark_runs(conn)
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc


@router.get("/benchmark/{run_id}", response_model=BenchmarkRunDetail)
def get_benchmark_detail(run_id: UUID, actor: AdminActor, pool: DbPool) -> BenchmarkRunDetail:
    try:
        with pool.connection() as conn:
            run = get_benchmark_run(conn, run_id)
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    if run is None:
        raise _not_found("Benchmark run")
    return run


@router.get("/courses", response_model=AdminCoursesResponse)
def get_admin_courses(
    actor: AdminActor,
    pool: DbPool,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> AdminCoursesResponse:
    try:
        with pool.connection() as conn:
            return AdminCoursesResponse(courses=search_courses(conn, q))
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc


@router.get("/courses/{course_id}", response_model=AdminCourseDetail)
def get_admin_course(course_id: UUID, actor: AdminActor, pool: DbPool) -> AdminCourseDetail:
    try:
        with pool.connection() as conn:
            return course_detail(conn, course_id)
    except LookupNotFoundError as exc:
        raise _not_found("Course") from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc


@router.post(
    "/courses/{course_id}/members",
    response_model=CourseMemberAddResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_course_member(
    course_id: UUID,
    request: CourseMemberAddRequest,
    actor: AdminActor,
    pool: DbPool,
    response: Response,
    idempotency_key: IdempotencyKey,
) -> CourseMemberAddResponse:
    try:
        with pool.connection() as conn:
            outcome = add_member(conn, actor, course_id, request, idempotency_key)
    except LookupNotFoundError as exc:
        raise _not_found("Course") from exc
    except MembershipError as exc:
        raise HTTPException(status_code=exc.status, detail=f"{exc.code}: {exc}") from exc
    except IdempotencyConflictError as exc:
        raise _conflict_key() from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    if outcome.replayed or not outcome.created:
        response.status_code = status.HTTP_200_OK
    return CourseMemberAddResponse(
        member=outcome.member,
        created=outcome.created,
        replayed=outcome.replayed,
        audit_event_id=outcome.audit_event_id,
    )


@router.get("/skills", response_model=AdminSkillsResponse)
def get_admin_skills(
    actor: AdminActor,
    pool: DbPool,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> AdminSkillsResponse:
    try:
        with pool.connection() as conn:
            return AdminSkillsResponse(skills=search_skills(conn, q))
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc


@router.get("/skills/{skill_id}", response_model=AdminSkillDetail)
def get_admin_skill(skill_id: UUID, actor: AdminActor, pool: DbPool) -> AdminSkillDetail:
    try:
        with pool.connection() as conn:
            return skill_detail(conn, skill_id)
    except LookupNotFoundError as exc:
        raise _not_found("Skill") from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
