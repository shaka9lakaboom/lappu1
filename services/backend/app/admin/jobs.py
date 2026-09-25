"""Admin processing-job operations (architecture §7.3, §13; ADR 0008). No model call.

    RETRY               a FAILED job -> PENDING with a fresh attempt budget
    RESUME_ATTRIBUTION  a COMPLETED raw-message job whose MAPPED task unit still has accepted
                        mappings without attribution (e.g. turns analysed before migration 0005)
                        -> PENDING; the worker skips P3A (already analysed) and attributes only.
                        The job is the one that builds the unit: the assistant message's for a
                        paired turn (its user message's job deferred to it), else the anchor's.

Safety rules:
* at most MAX_MANUAL_RETRIES per job (also a database check);
* the state change is conditional on the state that was inspected (row lock + `where state`),
  so a retry never races the worker;
* BOOTSTRAP_COURSE_GRAPH restores the graph stage first: a canonicalized graph resumes at
  EMBEDDING (0 generation requests, no graph v2);
* a verification job whose session was closed honestly (failure recorded, P6) is not retried:
  the failure is final for that session and the planner plans a new one after its delay;
* the retry and its audit event are one transaction, keyed by the Idempotency-Key.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection

from app.admin.audit import begin_request, record, request_hash
from app.admin.models import (
    ERROR_PREVIEW_CHARS,
    MAX_MANUAL_RETRIES,
    AdminJob,
    AdminJobsResponse,
    JobRetryMode,
)
from app.auth.roles import Actor
from app.core.redaction import redact
from app.intelligence.skill_graph.stages import prepare_bootstrap_retry
from app.intelligence.versions import ANALYSIS_VERSION, ATTRIBUTION_VERSION
from app.jobs.queue import (
    JOB_BOOTSTRAP_COURSE_GRAPH,
    JOB_EMBED_SKILL,
    JOB_GENERATE_VERIFICATION,
    JOB_GRADE_VERIFICATION,
    JOB_PROCESS_RAW_MESSAGE,
)

RETRYABLE_JOB_TYPES = frozenset(
    {
        JOB_BOOTSTRAP_COURSE_GRAPH,
        JOB_PROCESS_RAW_MESSAGE,
        JOB_GENERATE_VERIFICATION,
        JOB_GRADE_VERIFICATION,
        JOB_EMBED_SKILL,
    }
)
# The session state a verification job works on while its session is still open.
VERIFICATION_JOB_STATES = {
    JOB_GENERATE_VERIFICATION: "PLANNED",
    JOB_GRADE_VERIFICATION: "SUBMITTED",
}


class JobNotFoundError(LookupError):
    pass


class JobNotRetryableError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def retry_options(
    *,
    state: str,
    job_type: str,
    manual_retry_count: int,
    resumable: bool,
    session_state: str | None,
    session_failure: str | None,
) -> tuple[JobRetryMode | None, str | None]:
    """(the manual action the job allows now, or the code of why it is blocked)."""
    if state == "FAILED":
        if manual_retry_count >= MAX_MANUAL_RETRIES:
            return None, "RETRY_LIMIT_REACHED"
        if job_type not in RETRYABLE_JOB_TYPES:
            return None, "UNSUPPORTED_JOB_TYPE"
        if job_type in VERIFICATION_JOB_STATES and (
            session_state != VERIFICATION_JOB_STATES[job_type] or session_failure is not None
        ):
            return None, "VERIFICATION_CLOSED"
        return "RETRY", None
    if state == "COMPLETED" and job_type == JOB_PROCESS_RAW_MESSAGE and resumable:
        if manual_retry_count >= MAX_MANUAL_RETRIES:
            return None, "RETRY_LIMIT_REACHED"
        return "RESUME_ATTRIBUTION", None
    return None, None


_JOB_SELECT = """
select j.id, j.job_type, j.entity_type, j.entity_id, j.learner_id, j.state::text, j.attempts,
       j.max_attempts, j.outcome, j.last_error, j.available_at, j.completed_at, j.created_at,
       j.updated_at, j.manual_retry_count, j.last_manual_retry_at,
       (j.job_type = 'PROCESS_RAW_MESSAGE' and j.state = 'COMPLETED' and exists (
            select 1 from public.activity_segments s
              join public.mapping_decisions d on d.segment_id = s.id and d.outcome = 'MAPPED'
              join public.skill_mappings sm on sm.segment_id = s.id and sm.status = 'ACCEPTED'
             where s.source_message_ids @> array[j.entity_id]
               and coalesce(s.assistant_message_id, s.anchor_message_id) = j.entity_id
               and s.analysis_version = %(analysis)s
               and not exists (select 1 from public.attributions a
                                where a.segment_id = s.id
                                  and a.attribution_version = %(attribution)s))),
       vs.state::text, vs.failure_code
  from public.processing_jobs j
  left join public.verification_sessions vs
    on j.entity_type = 'verification_session' and vs.id = j.entity_id
"""


def _params(**extra: object) -> dict[str, object]:
    return {"analysis": ANALYSIS_VERSION, "attribution": ATTRIBUTION_VERSION, **extra}


def _job(row: tuple) -> AdminJob:
    mode, blocked = retry_options(
        state=row[5],
        job_type=row[1],
        manual_retry_count=row[14],
        resumable=bool(row[16]),
        session_state=row[17],
        session_failure=row[18],
    )
    return AdminJob(
        id=row[0],
        job_type=row[1],
        entity_type=row[2],
        entity_id=row[3],
        learner_id=row[4],
        state=row[5],
        attempts=row[6],
        max_attempts=row[7],
        outcome=row[8],
        last_error=redact(row[9], ERROR_PREVIEW_CHARS),
        available_at=row[10],
        completed_at=row[11],
        created_at=row[12],
        updated_at=row[13],
        manual_retry_count=row[14],
        last_manual_retry_at=row[15],
        retry_mode=mode,
        retry_blocked=blocked,
    )


def get_job(conn: Connection, job_id: UUID) -> AdminJob | None:
    row = conn.execute(_JOB_SELECT + " where j.id = %(id)s", _params(id=job_id)).fetchone()
    return _job(row) if row else None


def job_counts(conn: Connection) -> dict[str, int]:
    return dict(
        conn.execute(
            "select state::text, count(*) from public.processing_jobs group by state"
        ).fetchall()
    )


def list_jobs(
    conn: Connection,
    *,
    state: str | None = None,
    job_type: str | None = None,
    entity_id: UUID | None = None,
    before: datetime | None = None,
    limit: int = 50,
) -> AdminJobsResponse:
    rows = conn.execute(
        _JOB_SELECT
        + """
         where (%(state)s::text is null or j.state::text = %(state)s)
           and (%(type)s::text is null or j.job_type = %(type)s)
           and (%(entity)s::uuid is null or j.entity_id = %(entity)s)
           and (%(before)s::timestamptz is null or j.created_at < %(before)s)
         order by j.created_at desc, j.id desc
         limit %(limit)s
        """,
        _params(state=state, type=job_type, entity=entity_id, before=before, limit=limit),
    ).fetchall()
    jobs = [_job(r) for r in rows]
    return AdminJobsResponse(
        counts=job_counts(conn),
        jobs=jobs,
        next_before=jobs[-1].created_at if len(jobs) == limit else None,
    )


@dataclass(frozen=True)
class RetryOutcome:
    job: AdminJob
    mode: JobRetryMode
    stage_restored: str | None
    replayed: bool
    audit_event_id: UUID


def retry_job(
    conn: Connection, actor: Actor, job_id: UUID, mode: JobRetryMode, key: str
) -> RetryOutcome:
    digest = request_hash(f"POST /v1/admin/jobs/{job_id}/retry", {"mode": mode})
    with conn.transaction():
        replay = begin_request(conn, actor, key, digest)
        if replay is not None:
            job = get_job(conn, job_id)
            if job is None:  # pragma: no cover - the audited job was deleted since (learner gone)
                raise JobNotFoundError(job_id)
            return RetryOutcome(job, mode, replay.metadata.get("stage_restored"), True, replay.id)

        row = conn.execute(
            _JOB_SELECT + " where j.id = %(id)s for update of j", _params(id=job_id)
        ).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        job = _job(row)
        if job.retry_mode != mode:
            if mode == "RETRY" and job.state != "FAILED":
                raise JobNotRetryableError(
                    "NOT_FAILED", f"Only a FAILED job is retried (this one is {job.state})."
                )
            if mode == "RESUME_ATTRIBUTION" and job.retry_blocked is None:
                raise JobNotRetryableError(
                    "NOTHING_TO_RESUME", "This job has no accepted mapping waiting for attribution."
                )
            raise JobNotRetryableError(
                job.retry_blocked or "NOT_RETRYABLE",
                _BLOCKED.get(job.retry_blocked or "", "This job cannot be retried."),
            )

        stage = None
        if job.job_type == JOB_BOOTSTRAP_COURSE_GRAPH:
            stage = prepare_bootstrap_retry(conn, job.entity_id)
        updated = conn.execute(
            """
            update public.processing_jobs
               set state = 'PENDING', attempts = 0, available_at = now(), locked_at = null,
                   locked_by = null, last_error = null, completed_at = null, outcome = %s,
                   manual_retry_count = manual_retry_count + 1, last_manual_retry_at = now()
             where id = %s and state = %s::public.job_state
            returning manual_retry_count
            """,
            ("MANUAL_RETRY" if mode == "RETRY" else "MANUAL_RESUME", job_id, job.state),
        ).fetchone()
        if updated is None:  # pragma: no cover - the row lock makes this unreachable
            raise JobNotRetryableError("CHANGED", "The job changed while it was being retried.")
        audit = record(
            conn,
            actor=actor,
            action="JOB_RETRY" if mode == "RETRY" else "JOB_RESUME_ATTRIBUTION",
            entity_type="processing_job",
            entity_id=job_id,
            metadata={
                "job_type": job.job_type,
                "mode": mode,
                "previous_state": job.state,
                "previous_attempts": job.attempts,
                "previous_outcome": job.outcome,
                "manual_retry_count": updated[0],
                "stage_restored": stage,
            },
            key=key,
            request_digest=digest,
        )
        refreshed = get_job(conn, job_id)
    if refreshed is None:  # pragma: no cover - read back in the same transaction
        raise JobNotFoundError(job_id)
    return RetryOutcome(refreshed, mode, stage, False, audit.id)


_BLOCKED = {
    "RETRY_LIMIT_REACHED": f"This job was already retried manually {MAX_MANUAL_RETRIES} times.",
    "UNSUPPORTED_JOB_TYPE": "This job type has no manual retry.",
    "VERIFICATION_CLOSED": (
        "The verification session was closed with its failure recorded; the planner plans a "
        "new one after its retry delay."
    ),
}
