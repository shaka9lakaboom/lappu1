"""PostgreSQL-backed durable job queue (architecture §7.3).

P1 creates jobs and provides the claim/complete/fail primitives that the P3
worker loop will use. No worker runs yet, and no job is processed.

    PENDING -> PROCESSING -> COMPLETED
                   | failure
               RETRY_WAIT -> (claimable again after backoff)
                   | attempts exhausted
                 FAILED
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from psycopg import Connection

JOB_PROCESS_RAW_MESSAGE = "PROCESS_RAW_MESSAGE"

_BASE_BACKOFF = timedelta(seconds=30)
_MAX_BACKOFF = timedelta(minutes=30)
_MAX_ERROR_CHARS = 2000


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    job_type: str
    entity_type: str
    entity_id: UUID
    learner_id: UUID | None
    attempts: int
    max_attempts: int


def enqueue_job(
    conn: Connection,
    *,
    job_type: str,
    entity_type: str,
    entity_id: UUID,
    learner_id: UUID | None,
) -> UUID | None:
    """Create a job once per (job_type, entity). Returns None if it already existed."""
    row = conn.execute(
        """
        insert into public.processing_jobs (job_type, entity_type, entity_id, learner_id)
        values (%s, %s, %s, %s)
        on conflict on constraint processing_jobs_type_entity_key do nothing
        returning id
        """,
        (job_type, entity_type, entity_id, learner_id),
    ).fetchone()
    return row[0] if row else None


def claim_jobs(
    conn: Connection, *, job_types: list[str], worker_id: str, limit: int = 10
) -> list[ClaimedJob]:
    """Atomically claim up to `limit` runnable jobs. Concurrent workers never share a job."""
    with conn.transaction():
        rows = conn.execute(
            """
            update public.processing_jobs as j
               set state = 'PROCESSING', locked_at = now(), locked_by = %s,
                   attempts = j.attempts + 1
             where j.id in (
                   select id from public.processing_jobs
                    where state in ('PENDING', 'RETRY_WAIT')
                      and available_at <= now()
                      and job_type = any(%s)
                    order by available_at, created_at
                    limit %s
                    for update skip locked)
            returning j.id, j.job_type, j.entity_type, j.entity_id, j.learner_id,
                      j.attempts, j.max_attempts
            """,
            (worker_id, job_types, limit),
        ).fetchall()
    return [ClaimedJob(*row) for row in rows]


def complete_job(conn: Connection, job_id: UUID) -> None:
    with conn.transaction():
        conn.execute(
            """
            update public.processing_jobs
               set state = 'COMPLETED', locked_at = null, locked_by = null,
                   completed_at = now(), last_error = null
             where id = %s and state = 'PROCESSING'
            """,
            (job_id,),
        )


def retry_delay(attempts: int) -> timedelta:
    exponent = min(max(attempts - 1, 0), 16)
    return min(_BASE_BACKOFF * (2**exponent), _MAX_BACKOFF)


def fail_job(conn: Connection, job_id: UUID, error: str, *, now: datetime | None = None) -> str:
    """Record a failure: RETRY_WAIT with backoff, or FAILED once attempts are exhausted."""
    with conn.transaction():
        row = conn.execute(
            "select attempts, max_attempts from public.processing_jobs where id = %s for update",
            (job_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"job {job_id} not found")
        attempts, max_attempts = row
        state = "FAILED" if attempts >= max_attempts else "RETRY_WAIT"
        conn.execute(
            """
            update public.processing_jobs
               set state = %s::public.job_state, locked_at = null, locked_by = null,
                   last_error = %s,
                   available_at = coalesce(%s, now()) + %s
             where id = %s
            """,
            (state, error[:_MAX_ERROR_CHARS], now, retry_delay(attempts), job_id),
        )
    return state
