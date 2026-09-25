"""Feedback (architecture §13): POST /v1/feedback - wrong skill / do not count / evaluation.

The only way a learner changes derived state. The backend authorizes the target (it must be
the learner's own), applies the one-way evidence exclusion, recomputes the ledger and
refreshes recommendations in one transaction. An Idempotency-Key is required: a retry with
the same key returns the original result (200), the same key with a different request is 409.
"""

import logging
from typing import Annotated

import psycopg
from fastapi import APIRouter, Header, HTTPException, Response, status

from app.auth.dependencies import CurrentUser
from app.db.pool import DbPool
from app.experience.feedback import (
    FeedbackTargetNotFoundError,
    IdempotencyConflictError,
    NotCorrectableError,
    submit_feedback,
)
from app.experience.models import FeedbackRequest, FeedbackResponse
from app.intelligence.policy import load_policy

router = APIRouter(prefix="/feedback", tags=["feedback"])
logger = logging.getLogger("skillmirror.feedback")


@router.post("", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
def post_feedback(
    request: FeedbackRequest,
    user: CurrentUser,
    pool: DbPool,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    ],
) -> FeedbackResponse:
    try:
        with pool.connection() as conn:
            result = submit_feedback(
                conn, user.id, request, idempotency_key, policy=load_policy(conn)
            )
    except FeedbackTargetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Feedback target not found."
        ) from exc
    except NotCorrectableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Idempotency-Key was already used for a different feedback request.",
        ) from exc
    except psycopg.OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is temporarily unavailable; retry later.",
        ) from exc
    if not result.created:
        response.status_code = status.HTTP_200_OK
    logger.info(
        "feedback %s correlation_id=%s learner=%s action=%s target=%s:%s excluded=%d recomputed=%d",
        "recorded" if result.created else "replayed",
        result.correlation_id,
        user.id,
        result.feedback.action,
        result.feedback.target_type,
        result.feedback.target_id,
        len(result.feedback.excluded_evidence_ids),
        len(result.feedback.recomputed_skill_ids),
    )
    return result
