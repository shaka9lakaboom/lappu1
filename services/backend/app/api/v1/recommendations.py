"""Recommendations (architecture §13): GET /v1/recommendations - the learner's action queue.

The queue is a deterministic projection of the ledger (Engine 16, no model call). It is
refreshed before it is read, so it always matches the current ledger; the refresh writes
only when an action changed. Scoped to the authenticated learner.
"""

from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, status

from app.auth.dependencies import CurrentUser
from app.db.pool import DbPool
from app.experience.models import RecommendationsResponse
from app.intelligence.policy import load_policy
from app.intelligence.recommendations.engine import ALGORITHM_VERSION
from app.intelligence.recommendations.service import (
    list_recommendations,
    refresh_recommendations,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("", response_model=RecommendationsResponse)
def get_recommendations(
    user: CurrentUser,
    pool: DbPool,
    course_id: UUID | None = None,
    include_no_action: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> RecommendationsResponse:
    """ACTIVE recommendations, highest priority first. NO_ACTION rows (e.g. UNKNOWN skills:
    no judgement yet) are left out unless `include_no_action` is set."""
    try:
        with pool.connection() as conn:
            if (
                course_id is not None
                and conn.execute(
                    "select 1 from public.course_memberships where course_id = %s and user_id = %s",
                    (course_id, user.id),
                ).fetchone()
                is None
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Course not found."
                )
            refresh_recommendations(conn, user.id, policy=load_policy(conn))
            items = list_recommendations(
                conn,
                user.id,
                course_id=course_id,
                include_no_action=include_no_action,
                limit=limit,
            )
    except psycopg.OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is temporarily unavailable; retry later.",
        ) from exc
    return RecommendationsResponse(algorithm_version=ALGORITHM_VERSION, recommendations=items)
