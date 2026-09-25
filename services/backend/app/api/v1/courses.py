"""Course endpoints (architecture §13): POST/GET /v1/courses, GET /v1/courses/{id}[/skills]."""

import logging
from typing import Annotated
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Header, HTTPException, Response, status

from app.auth.dependencies import CurrentUser
from app.courses.models import (
    Course,
    CourseCreateRequest,
    CourseCreateResponse,
    CourseListResponse,
    CourseSkillsResponse,
)
from app.courses.service import (
    UnknownLearnerError,
    create_course,
    get_course,
    get_course_skills,
    list_courses,
)
from app.db.pool import DbPool

router = APIRouter(prefix="/courses", tags=["courses"])
logger = logging.getLogger("skillmirror.courses")

_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Storage is temporarily unavailable; retry later.",
)
_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found.")


@router.post("", response_model=CourseCreateResponse, status_code=status.HTTP_201_CREATED)
def post_course(
    request: CourseCreateRequest,
    user: CurrentUser,
    pool: DbPool,
    response: Response,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    ] = None,
) -> CourseCreateResponse:
    """Create a course for the authenticated learner and enqueue its skill-graph bootstrap.

    The graph is generated asynchronously; poll GET /v1/courses/{id} for `graph_status`.
    A retry with the same Idempotency-Key returns the original course (200).
    """
    correlation_id = idempotency_key or uuid4().hex
    try:
        with pool.connection() as conn:
            created = create_course(conn, user.id, request, idempotency_key)
    except UnknownLearnerError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No SkillMirror profile exists for this account.",
        ) from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    if not created.created:
        response.status_code = status.HTTP_200_OK
    logger.info(
        "course %s correlation_id=%s learner=%s course=%s job=%s",
        "created" if created.created else "replayed",
        correlation_id,
        user.id,
        created.course.id,
        created.bootstrap_job_id,
    )
    return CourseCreateResponse(
        correlation_id=correlation_id,
        created=created.created,
        bootstrap_job_id=created.bootstrap_job_id,
        course=created.course,
    )


@router.get("", response_model=CourseListResponse)
def get_courses(user: CurrentUser, pool: DbPool) -> CourseListResponse:
    try:
        with pool.connection() as conn:
            return CourseListResponse(courses=list_courses(conn, user.id))
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc


@router.get("/{course_id}", response_model=Course)
def get_course_detail(course_id: UUID, user: CurrentUser, pool: DbPool) -> Course:
    try:
        with pool.connection() as conn:
            course = get_course(conn, user.id, course_id)
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    if course is None:
        raise _NOT_FOUND
    return course


@router.get("/{course_id}/skills", response_model=CourseSkillsResponse)
def get_course_skill_graph(
    course_id: UUID, user: CurrentUser, pool: DbPool
) -> CourseSkillsResponse:
    try:
        with pool.connection() as conn:
            skills = get_course_skills(conn, user.id, course_id)
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    if skills is None:
        raise _NOT_FOUND
    return skills
