"""Activity feed (architecture §13): GET /v1/activity - captured, ignored and mapped activity.

Read-only and scoped to the authenticated learner. Captured text is a short preview that
clients render as plain text.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, status

from app.auth.dependencies import CurrentUser
from app.db.pool import DbPool
from app.experience.activity import list_activity
from app.experience.models import ActivityResponse

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=ActivityResponse)
def get_activity(
    user: CurrentUser,
    pool: DbPool,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: datetime | None = None,
    raw_message_id: Annotated[list[UUID] | None, Query(max_length=10)] = None,
) -> ActivityResponse:
    """Newest first. `before` continues from a previous page's `next_before`;
    `raw_message_id` (repeatable) returns exactly those messages, e.g. an evidence's source."""
    try:
        with pool.connection() as conn:
            return list_activity(
                conn, user.id, limit=limit, before=before, raw_message_ids=raw_message_id
            )
    except psycopg.OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is temporarily unavailable; retry later.",
        ) from exc
