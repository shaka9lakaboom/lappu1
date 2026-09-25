"""PostgreSQL-backed durable job queue (architecture §7.3).

Claim/complete/fail/defer primitives used by the worker loop (app/jobs/worker.py).
Delivery is at-least-once: handlers are idempotent, and a job whose worker died
mid-way is recovered from its stale lock and retried.

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

from app.core.redaction import redact

JOB_PROCESS_RAW_MESSAGE = "PROCESS_RAW_MESSAGE"
JOB_BOOTSTRAP_COURSE_GRAPH = "BOOTSTRAP_COURSE_GRAPH"
# P6 (ADR 0007): both keyed by the verification session (entity_type verification_session).
JOB_GENERATE_VERIFICATION = "GENERATE_VERIFICATION"
JOB_GRADE_VERIFICATION = "GRADE_VERIFICATION"
# P7 (ADR 0008): (re-)embed one registry skill after a candidate approval or merge
# (entity_type skill_node, no learner).
JOB_EMBED_SKILL = "EMBED_SKILL"

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


@dataclass(frozen=True)
class JobResult:
    """What a handler returns. `defer_seconds` reschedules without spending an attempt."""

    outcome: str
    defer_seconds: int | None = None


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


def enqueue_or_rearm_job(
    conn: Connection, *, job_type: str, entity_type: str, entity_id: UUID
) -> UUID:
    """A learner-less job that must run (again) for its entity: create it, or put a finished
    (COMPLETED / FAILED) one back to PENDING with a fresh attempt budget. A job that is still
    PENDING, RETRY_WAIT or PROCESSING is left as it is: it has not read the entity yet, or it
    is reading it now."""
    row = conn.execute(
        """
        insert into public.processing_jobs (job_type, entity_type, entity_id)
        values (%s, %s, %s)
        on conflict on constraint processing_jobs_type_entity_key do update
           set state = 'PENDING', attempts = 0, available_at = now(), last_error = null,
               completed_at = null, outcome = 'REARMED'
         where public.processing_jobs.state in ('COMPLETED', 'FAILED')
        returning id
        """,
        (job_type, entity_type, entity_id),
    ).fetchone()
    if row:
        return row[0]
    (job_id,) = conn.execute(
        "select id from public.processing_jobs where job_type = %s and entity_id = %s",
        (job_type, entity_id),
    ).fetchone()
    return job_id


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


def complete_job(conn: Connection, job_id: UUID, outcome: str | None = None) -> None:
    with conn.transaction():
        conn.execute(
            """
            update public.processing_jobs
               set state = 'COMPLETED', locked_at = null, locked_by = null,
                   completed_at = now(), last_error = null, outcome = %s
             where id = %s and state = 'PROCESSING'
            """,
            (outcome, job_id),
        )


def defer_job(conn: Connection, job_id: UUID, delay: timedelta, outcome: str) -> None:
    """Put a claimed job back for later without counting the claim as an attempt
    (e.g. a user message whose assistant reply has not arrived yet)."""
    with conn.transaction():
        conn.execute(
            """
            update public.processing_jobs
               set state = 'PENDING', locked_at = null, locked_by = null,
                   attempts = greatest(attempts - 1, 0), outcome = %s,
                   available_at = now() + %s
             where id = %s and state = 'PROCESSING'
            """,
            (outcome, delay, job_id),
        )


def recover_stale_jobs(conn: Connection, stale_after: timedelta) -> list[tuple[UUID, str]]:
    """Crash recovery: PROCESSING jobs whose lock is older than `stale_after` belonged
    to a worker that died. Each counts as a failed attempt (RETRY_WAIT or FAILED)."""
    with conn.transaction():
        rows = conn.execute(
            """
            update public.processing_jobs
               set state = case when attempts >= max_attempts then 'FAILED'::public.job_state
                                else 'RETRY_WAIT'::public.job_state end,
                   locked_at = null, locked_by = null,
                   last_error = 'worker lock expired (worker crashed or was stopped)',
                   available_at = now()
             where state = 'PROCESSING' and locked_at < now() - %s
            returning id, state::text
            """,
            (stale_after,),
        ).fetchall()
    return [(row[0], row[1]) for row in rows]


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
            (state, redact(error, _MAX_ERROR_CHARS), now, retry_delay(attempts), job_id),
        )
    return state
