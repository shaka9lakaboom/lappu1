"""Teacher endpoints (architecture §12.3, §13; ADR 0008). Read-only, no model call.

    GET /v1/teacher/courses                  the courses this account teaches
    GET /v1/teacher/courses/{id}/overview    cohort aggregates of one of them

A TEACHER or ADMIN profile is required (403 otherwise; the role is read from the database).
The overview needs a TEACHER membership of that specific course, else 404 so that a course's
existence does not leak - for an ADMIN too: admin course access is the admin course lookup
(`/v1/admin/courses[/{id}]`), not this endpoint.
"""

from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, status

from app.auth.roles import TeachingActor
from app.db.pool import DbPool
from app.intelligence.policy import PolicyConfigError
from app.teacher.models import TeacherCourseOverview, TeacherCoursesResponse
from app.teacher.service import (
    TeacherCourseNotFoundError,
    course_overview,
    list_taught_courses,
    load_teacher_view_policy,
)

router = APIRouter(prefix="/teacher", tags=["teacher"])

_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Storage is temporarily unavailable; retry later.",
)
_NOT_CONFIGURED = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="The teacher view is not configured (apply migration 0009).",
)


@router.get("/courses", response_model=TeacherCoursesResponse)
def get_taught_courses(actor: TeachingActor, pool: DbPool) -> TeacherCoursesResponse:
    try:
        with pool.connection() as conn:
            policy = load_teacher_view_policy(conn)
            courses = list_taught_courses(conn, actor, policy)
    except PolicyConfigError as exc:
        raise _NOT_CONFIGURED from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    return TeacherCoursesResponse(min_cohort=policy.min_cohort, courses=courses)


@router.get("/courses/{course_id}/overview", response_model=TeacherCourseOverview)
def get_course_overview(
    course_id: UUID, actor: TeachingActor, pool: DbPool
) -> TeacherCourseOverview:
    try:
        with pool.connection() as conn:
            return course_overview(conn, actor, course_id, load_teacher_view_policy(conn))
    except TeacherCourseNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Course not found."
        ) from exc
    except PolicyConfigError as exc:
        raise _NOT_CONFIGURED from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
