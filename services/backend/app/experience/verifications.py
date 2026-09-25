"""The Verification Center service (architecture §11.4, §12.1, §13; ADR 0007). No model call.

    GET  /v1/verifications             refresh the recommendation queue -> deterministic planner
                                       (PLANNED sessions + generation jobs) -> the learner's queue
    GET  /v1/verifications/{id}        one session; the sanitized challenge once started
    POST /v1/verifications/{id}/start  READY -> IN_PROGRESS; on IN_PROGRESS it resumes (no change)
    POST /v1/verifications/{id}/submit Idempotency-Key: validate -> store the response durably ->
                                       SUBMITTED -> enqueue grading -> return (grading is async)
    POST /v1/verifications/{id}/abandon IN_PROGRESS -> ABANDONED (no result, no evidence)

Every read and write is scoped to the authenticated learner explicitly (never RLS alone): a
foreign session is 404. The answer key and rubric never leave the server; a learner's answer is
untrusted data, stored and returned as plain data only. Model calls happen in the worker, never
in a request.
"""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.experience.models import (
    VerificationBudget,
    VerificationChallenge,
    VerificationChoice,
    VerificationCriterion,
    VerificationDetailResponse,
    VerificationResult,
    VerificationSessionSummary,
    VerificationsResponse,
    VerificationSubmissionRequest,
    VerificationSubmissionResponse,
)
from app.intelligence.policy import IntelligencePolicy
from app.intelligence.recommendations.service import refresh_recommendations
from app.intelligence.verification.graders import (
    ResponseInvalidError,
    answer_key_set,
    challenge_item,
    normalize_response,
)
from app.intelligence.verification.planner import (
    ENTITY_TYPE,
    PLANNER_VERSION,
    plan_verifications,
)
from app.jobs.queue import JOB_GRADE_VERIFICATION, enqueue_job

__all__ = [
    "ResponseInvalidError",
    "SubmissionConflictError",
    "VerificationNotFoundError",
    "VerificationStateError",
    "abandon_verification",
    "get_verification",
    "list_verifications",
    "start_verification",
    "submit_verification",
]

LEARNER_ABANDONED = "LEARNER_ABANDONED"
HISTORY_LIMIT = 50


class VerificationNotFoundError(LookupError):
    pass


class VerificationStateError(ValueError):
    """The session is not in a state that allows this action (409)."""


class SubmissionConflictError(ValueError):
    """The Idempotency-Key was used for another answer, or the check was already submitted (409)."""


_SUMMARY_SQL = """
select s.id, s.skill_id, n.canonical_name, s.course_id, s.state::text, s.trigger_type::text,
       s.reason_code, s.recommendation_id, s.planned_difficulty, i.assessment_type::text,
       i.estimated_minutes, s.failure_code, s.abandon_reason, s.created_at, s.ready_at,
       s.started_at, s.submitted_at, s.evaluated_at, s.abandoned_at,
       r.id, r.score, r.pass, r.outcome_signal::text, r.feedback, r.grading_confidence,
       r.evaluator_type::text, r.evaluation, r.created_at, e.id
  from public.verification_sessions s
  join public.skill_nodes n on n.id = s.skill_id
  left join public.verification_items i on i.session_id = s.id
  left join public.verification_results r on r.item_id = i.id
  left join public.evidence_events e on e.source_type = 'VERIFICATION' and e.source_id = r.id
 where s.learner_id = %(learner)s and (%(session)s::uuid is null or s.id = %(session)s::uuid)
 order by s.created_at desc, s.id
 limit %(limit)s
"""


def status_of(state: str, failure_code: str | None, outcome_signal: str | None) -> str:
    if state == "PLANNED":
        return "NOT_ISSUED" if failure_code else "PREPARING"
    if state == "SUBMITTED":
        return "NEEDS_REVIEW" if failure_code else "EVALUATING"
    if state == "EVALUATED":
        return {"CORRECT": "PASSED", "PARTIAL": "PARTIAL"}.get(outcome_signal or "", "NOT_PASSED")
    return state  # READY, IN_PROGRESS, ABANDONED


def _summary(r: tuple) -> VerificationSessionSummary:
    result = None
    if r[19] is not None:
        evaluation = r[26] or {}
        result = VerificationResult(
            id=r[19],
            score=r[20],
            passed=r[21],
            outcome_signal=r[22],
            feedback=r[23],
            grading_confidence=round(float(r[24]), 6),
            evaluator_type=r[25],
            criteria=[
                VerificationCriterion(criterion=c["criterion"], met=bool(c["met"]))
                for c in evaluation.get("criterion_results", [])
            ],
            evidence_id=r[28],
            created_at=r[27],
        )
    return VerificationSessionSummary(
        id=r[0],
        skill_id=r[1],
        canonical_name=r[2],
        course_id=r[3],
        state=r[4],
        status=status_of(r[4], r[11], r[22]),
        trigger_type=r[5],
        reason_code=r[6],
        recommendation_id=r[7],
        planned_difficulty=r[8],
        assessment_type=r[9],
        estimated_minutes=r[10],
        failure_code=r[11],
        abandon_reason=r[12],
        created_at=r[13],
        ready_at=r[14],
        started_at=r[15],
        submitted_at=r[16],
        evaluated_at=r[17],
        abandoned_at=r[18],
        result=result,
    )


def _sessions(
    conn: Connection, learner_id: UUID, session_id: UUID | None = None, limit: int = HISTORY_LIMIT
) -> list[VerificationSessionSummary]:
    rows = conn.execute(
        _SUMMARY_SQL, {"learner": learner_id, "session": session_id, "limit": limit}
    ).fetchall()
    return [_summary(r) for r in rows]


def list_verifications(
    conn: Connection, learner_id: UUID, *, policy: IntelligencePolicy, now: datetime | None = None
) -> VerificationsResponse:
    """Refresh the queue and plan (deterministic, idempotent), then the learner's sessions."""
    refresh_recommendations(conn, learner_id, policy=policy)
    report = plan_verifications(conn, learner_id, policy=policy, now=now or datetime.now(UTC))
    groups: dict[str, list[VerificationSessionSummary]] = {
        "preparing": [],
        "ready": [],
        "in_progress": [],
        "pending": [],
        "completed": [],
        "closed": [],
    }
    group_of = {
        "PREPARING": "preparing",
        "READY": "ready",
        "IN_PROGRESS": "in_progress",
        "EVALUATING": "pending",
        "PASSED": "completed",
        "PARTIAL": "completed",
        "NOT_PASSED": "completed",
    }
    for session in _sessions(conn, learner_id):
        groups[group_of.get(session.status, "closed")].append(session)
    return VerificationsResponse(
        planner_version=PLANNER_VERSION,
        budget=VerificationBudget(
            day=report.day.isoformat(),
            timezone=report.timezone,
            daily_limit=report.daily_limit,
            planned_today=report.planned_today,
            remaining_today=report.remaining_today,
        ),
        **groups,
    )


def _max_chars(assessment_type: str, policy: IntelligencePolicy) -> int:
    grading = policy.verification.grading
    if assessment_type == "numeric":
        return grading.numeric_max_chars
    if assessment_type == "short_response":
        return grading.short_response_max_chars
    return grading.max_response_chars


def _item_row(conn: Connection, session_id: UUID) -> tuple | None:
    return conn.execute(
        """
        select i.id, i.assessment_type::text, i.grader_type::text, i.prompt, i.choices,
               i.expected_answer, i.rubric, i.estimated_minutes, n.canonical_name, n.description,
               i.skill_id
          from public.verification_items i
          join public.skill_nodes n on n.id = i.skill_id
         where i.session_id = %s
        """,
        (session_id,),
    ).fetchone()


def get_verification(
    conn: Connection, learner_id: UUID, session_id: UUID, *, policy: IntelligencePolicy
) -> VerificationDetailResponse:
    found = _sessions(conn, learner_id, session_id, limit=1)
    if not found:
        raise VerificationNotFoundError(session_id)
    session = found[0]
    challenge = None
    if session.state in ("IN_PROGRESS", "SUBMITTED", "EVALUATED"):
        row = _item_row(conn, session_id)
        if row is not None:
            item = challenge_item(row[:7])
            challenge = VerificationChallenge(
                session_id=session_id,
                item_id=item.id,
                skill_id=row[10],
                canonical_name=row[8],
                skill_description=row[9],
                assessment_type=item.assessment_type,
                prompt=item.prompt,
                choices=[VerificationChoice(key=k, text=t) for k, t in item.choices],
                multiple_select=item.assessment_type == "mcq"
                and len(answer_key_set(item.expected_answer)) > 1,
                estimated_minutes=row[7],
                max_response_chars=_max_chars(item.assessment_type, policy),
            )
    response = None
    if session.state in ("SUBMITTED", "EVALUATED"):
        stored = conn.execute(
            "select submitted_response from public.verification_sessions "
            "where id = %s and learner_id = %s",
            (session_id, learner_id),
        ).fetchone()
        response = stored[0] if stored else None
    return VerificationDetailResponse(session=session, challenge=challenge, response=response)


def _lock(conn: Connection, learner_id: UUID, session_id: UUID) -> tuple:
    row = conn.execute(
        """
        select state::text, failure_code, submission_idempotency_key, submission_request_hash
          from public.verification_sessions
         where id = %s and learner_id = %s
           for update
        """,
        (session_id, learner_id),
    ).fetchone()
    if row is None:
        raise VerificationNotFoundError(session_id)
    return row


def start_verification(
    conn: Connection, learner_id: UUID, session_id: UUID, *, policy: IntelligencePolicy
) -> VerificationDetailResponse:
    """READY -> IN_PROGRESS. Calling it again on IN_PROGRESS resumes the same challenge."""
    with conn.transaction():
        state, *_ = _lock(conn, learner_id, session_id)
        if state == "READY":
            conn.execute(
                "update public.verification_sessions set state = 'IN_PROGRESS', started_at = now() "
                "where id = %s",
                (session_id,),
            )
        elif state != "IN_PROGRESS":
            raise VerificationStateError(f"This check cannot be started: it is {state}.")
    return get_verification(conn, learner_id, session_id, policy=policy)


def request_hash(session_id: UUID, request: VerificationSubmissionRequest) -> str:
    canonical = json.dumps(
        {
            "session_id": str(session_id),
            "selected": sorted(k.upper() for k in request.selected)
            if request.selected is not None
            else None,
            "answer": request.answer.strip() if request.answer is not None else None,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def submit_verification(
    conn: Connection,
    learner_id: UUID,
    session_id: UUID,
    request: VerificationSubmissionRequest,
    idempotency_key: str,
    *,
    policy: IntelligencePolicy,
) -> VerificationSubmissionResponse:
    """Store the answer durably, SUBMITTED, enqueue grading - one transaction, no model call."""
    digest = request_hash(session_id, request)
    created = False
    with conn.transaction():
        state, _failure, stored_key, stored_hash = _lock(conn, learner_id, session_id)
        if state in ("SUBMITTED", "EVALUATED"):
            if stored_key == idempotency_key and stored_hash == digest:
                return _submission(conn, learner_id, session_id, idempotency_key, created=False)
            if stored_key == idempotency_key:
                raise SubmissionConflictError(
                    "This Idempotency-Key was already used with a different answer."
                )
            raise SubmissionConflictError("This check was already submitted.")
        if state != "IN_PROGRESS":
            raise VerificationStateError(
                "Start the check before submitting."
                if state == "READY"
                else f"This check cannot be submitted: it is {state}."
            )
        if conn.execute(
            "select 1 from public.verification_sessions "
            "where learner_id = %s and submission_idempotency_key = %s",
            (learner_id, idempotency_key),
        ).fetchone():
            raise SubmissionConflictError(
                "This Idempotency-Key was already used for another check."
            )
        row = _item_row(conn, session_id)
        if row is None:  # pragma: no cover - a started session always has its item (0008 guard)
            raise VerificationStateError("This check has no challenge.")
        response = normalize_response(
            challenge_item(row[:7]),
            answer=request.answer,
            selected=request.selected,
            policy=policy.verification.grading,
        )
        conn.execute(
            """
            update public.verification_sessions
               set state = 'SUBMITTED', submitted_at = now(), submitted_response = %s,
                   submission_idempotency_key = %s, submission_request_hash = %s
             where id = %s
            """,
            (Jsonb(response), idempotency_key, digest, session_id),
        )
        enqueue_job(
            conn,
            job_type=JOB_GRADE_VERIFICATION,
            entity_type=ENTITY_TYPE,
            entity_id=session_id,
            learner_id=learner_id,
        )
        created = True
    return _submission(conn, learner_id, session_id, idempotency_key, created=created)


def _submission(
    conn: Connection, learner_id: UUID, session_id: UUID, key: str, *, created: bool
) -> VerificationSubmissionResponse:
    return VerificationSubmissionResponse(
        correlation_id=key,
        created=created,
        session=_sessions(conn, learner_id, session_id, limit=1)[0],
    )


def abandon_verification(
    conn: Connection,
    learner_id: UUID,
    session_id: UUID,
    *,
    policy: IntelligencePolicy,
    reason: str = LEARNER_ABANDONED,
) -> VerificationDetailResponse:
    """IN_PROGRESS -> ABANDONED: no result and no evidence (incomplete is not failure)."""
    with conn.transaction():
        state, *_ = _lock(conn, learner_id, session_id)
        if state != "IN_PROGRESS":
            raise VerificationStateError(f"Only a started check can be abandoned (it is {state}).")
        conn.execute(
            "update public.verification_sessions set state = 'ABANDONED', abandoned_at = now(), "
            "abandon_reason = %s where id = %s",
            (reason, session_id),
        )
    return get_verification(conn, learner_id, session_id, policy=policy)
