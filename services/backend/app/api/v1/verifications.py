"""Verification (architecture §13): the Verification Center queue, start/resume and submission.

    GET  /v1/verifications               queue + history (runs the deterministic planner first)
    GET  /v1/verifications/{id}          one session; the sanitized challenge once started
    POST /v1/verifications/{id}/start    start or resume
    POST /v1/verifications/{id}/submit   Idempotency-Key required; 202 stored (grading is
                                         asynchronous), 200 replay, 409 key reuse / already
                                         submitted / not started, 422 answer does not fit
    POST /v1/verifications/{id}/abandon  stop a started check (no result, no evidence)

Every endpoint is scoped to the authenticated learner (a foreign session is 404) and makes no
model call: generation and grading run in the worker.
"""

import logging
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Header, HTTPException, Response, status

from app.auth.dependencies import CurrentUser
from app.db.pool import DbPool
from app.experience.models import (
    VerificationDetailResponse,
    VerificationsResponse,
    VerificationSubmissionRequest,
    VerificationSubmissionResponse,
)
from app.experience.verifications import (
    ResponseInvalidError,
    SubmissionConflictError,
    VerificationNotFoundError,
    VerificationStateError,
    abandon_verification,
    get_verification,
    list_verifications,
    start_verification,
    submit_verification,
)
from app.intelligence.policy import load_policy

router = APIRouter(prefix="/verifications", tags=["verifications"])
logger = logging.getLogger("skillmirror.verifications")

_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Storage is temporarily unavailable; retry later.",
)
_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Verification not found.")


@router.get("", response_model=VerificationsResponse)
def get_verifications(user: CurrentUser, pool: DbPool) -> VerificationsResponse:
    try:
        with pool.connection() as conn:
            return list_verifications(conn, user.id, policy=load_policy(conn))
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc


@router.get("/{session_id}", response_model=VerificationDetailResponse)
def get_verification_detail(
    session_id: UUID, user: CurrentUser, pool: DbPool
) -> VerificationDetailResponse:
    try:
        with pool.connection() as conn:
            return get_verification(conn, user.id, session_id, policy=load_policy(conn))
    except VerificationNotFoundError as exc:
        raise _NOT_FOUND from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc


@router.post("/{session_id}/start", response_model=VerificationDetailResponse)
def post_start(session_id: UUID, user: CurrentUser, pool: DbPool) -> VerificationDetailResponse:
    try:
        with pool.connection() as conn:
            detail = start_verification(conn, user.id, session_id, policy=load_policy(conn))
    except VerificationNotFoundError as exc:
        raise _NOT_FOUND from exc
    except VerificationStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    logger.info("verification %s started/resumed learner=%s", session_id, user.id)
    return detail


@router.post(
    "/{session_id}/submit",
    response_model=VerificationSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_submit(
    session_id: UUID,
    request: VerificationSubmissionRequest,
    user: CurrentUser,
    pool: DbPool,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    ],
) -> VerificationSubmissionResponse:
    try:
        with pool.connection() as conn:
            result = submit_verification(
                conn, user.id, session_id, request, idempotency_key, policy=load_policy(conn)
            )
    except VerificationNotFoundError as exc:
        raise _NOT_FOUND from exc
    except (VerificationStateError, SubmissionConflictError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ResponseInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    if not result.created:
        response.status_code = status.HTTP_200_OK
    logger.info(
        "verification %s submission %s correlation_id=%s learner=%s",
        session_id,
        "stored" if result.created else "replayed",
        result.correlation_id,
        user.id,
    )
    return result


@router.post("/{session_id}/abandon", response_model=VerificationDetailResponse)
def post_abandon(session_id: UUID, user: CurrentUser, pool: DbPool) -> VerificationDetailResponse:
    try:
        with pool.connection() as conn:
            detail = abandon_verification(conn, user.id, session_id, policy=load_policy(conn))
    except VerificationNotFoundError as exc:
        raise _NOT_FOUND from exc
    except VerificationStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
    logger.info("verification %s abandoned learner=%s", session_id, user.id)
    return detail
