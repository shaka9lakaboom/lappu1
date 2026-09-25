"""Ledger endpoint (architecture §13): GET /v1/ledger - the current learner's ledger summary.

Read-only. Every value is derived server-side from EvidenceEvents; a skill without
evidence is returned as UNKNOWN with no mastery mean (unknown is not weak).
"""

from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.db.pool import DbPool
from app.intelligence.contracts import LedgerResponse
from app.intelligence.mastery.ledger import CourseNotFoundError, read_ledger
from app.intelligence.policy import load_policy

router = APIRouter(prefix="/ledger", tags=["ledger"])

_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Storage is temporarily unavailable; retry later.",
)


@router.get("", response_model=LedgerResponse)
def get_ledger(user: CurrentUser, pool: DbPool, course_id: UUID | None = None) -> LedgerResponse:
    """The learner's course skills joined with their derived mastery and AI Assistance Debt.

    `course_id` restricts the result to one of the learner's courses (404 otherwise)."""
    try:
        with pool.connection() as conn:
            policy = load_policy(conn)
            return read_ledger(conn, user.id, policy=policy, course_id=course_id)
    except CourseNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Course not found."
        ) from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
