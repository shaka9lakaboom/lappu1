"""Ingestion endpoints (architecture §13): POST /v1/events/batch, GET /v1/events/sync-status."""

import logging
import time
from typing import Annotated
from uuid import uuid4

import psycopg
from fastapi import APIRouter, Header, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.db.pool import DbPool
from app.ingestion.models import (
    EventBatchRequest,
    EventBatchResponse,
    IngestResult,
    SyncStatusResponse,
)
from app.ingestion.service import UnknownLearnerError, ingest_batch, sync_status

router = APIRouter(prefix="/events", tags=["ingestion"])
logger = logging.getLogger("skillmirror.ingestion")


@router.post("/batch", response_model=EventBatchResponse)
def post_events_batch(
    batch: EventBatchRequest,
    user: CurrentUser,
    pool: DbPool,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    ] = None,
) -> EventBatchResponse:
    """Store a batch durably, then acknowledge it.

    Safe to retry: every event is deduplicated by the database, and a resent
    event is acknowledged as `duplicate` with the id of the stored record. The
    response is sent only after the transaction (raw rows + processing jobs)
    has committed.
    """
    # Identity comes from the verified token. A body naming anyone else is refused.
    if any(event.learner_id != user.id for event in batch.events):
        logger.warning("ingest rejected: learner_id mismatch for user %s", user.id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="learner_id does not match the authenticated learner.",
        )

    correlation_id = idempotency_key or uuid4().hex
    started = time.perf_counter()
    try:
        with pool.connection() as conn:
            stored = ingest_batch(conn, user.id, batch.events, batch.client)
    except UnknownLearnerError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No SkillMirror profile exists for this account.",
        ) from exc
    except psycopg.OperationalError as exc:
        logger.error("ingest failed: database unavailable (%s)", correlation_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is temporarily unavailable; retry later.",
        ) from exc

    results = [
        IngestResult(
            event_id=s.event_id,
            status=s.status,  # type: ignore[arg-type]
            raw_message_id=s.raw_message_id,
            conversation_id=s.conversation_id,
            revision_index=s.revision_index,
        )
        for s in stored
    ]
    accepted = sum(1 for r in results if r.status == "accepted")
    logger.info(
        "ingest ok correlation_id=%s learner=%s events=%d accepted=%d duplicate=%d ms=%.1f",
        correlation_id,
        user.id,
        len(results),
        accepted,
        len(results) - accepted,
        (time.perf_counter() - started) * 1000,
    )
    return EventBatchResponse(
        correlation_id=correlation_id,
        accepted=accepted,
        duplicates=len(results) - accepted,
        results=results,
    )


@router.get("/sync-status", response_model=SyncStatusResponse)
def get_sync_status(user: CurrentUser, pool: DbPool) -> SyncStatusResponse:
    try:
        with pool.connection() as conn:
            count, last_received, pending = sync_status(conn, user.id)
    except psycopg.OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is temporarily unavailable; retry later.",
        ) from exc
    return SyncStatusResponse(
        raw_message_count=count, last_received_at=last_received, pending_jobs=pending
    )
