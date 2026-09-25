"""The P6 worker jobs (architecture §7.3, §11; ADR 0007), on the existing processing_jobs queue.

GENERATE_VERIFICATION (entity: the PLANNED session)

    for attempt in (done + 1 .. generation.max_attempts):       # initial + 2 regenerations
        ONE VERIFICATION_GENERATION request (input: session, attempt, earlier rejections, history)
        deterministic validation
        valid   -> item + READY (one transaction)                 -> VERIFICATION_READY
        invalid -> record the rejection codes, regenerate
    all rejected -> failure recorded on the session, no challenge -> VERIFICATION_NOT_ISSUED

GRADE_VERIFICATION (entity: the SUBMITTED session)

    deterministic grader (MCQ / numeric / exact short answer), else ONE rubric evaluation
    trusted grade -> result + VERIFICATION evidence + EVALUATED (one transaction)
                  -> recompute_ledger(...) -> refresh_recommendations(...)  -> VERIFICATION_EVALUATED
    untrusted     -> failure recorded, the answer stays SUBMITTED, no evidence -> VERIFICATION_NOT_GRADED

A 429/503 or a spent budget propagates from the gateway: the worker defers the job with 0
attempts spent (ADR 0004) and the learner's submission is untouched. Every step is replay-safe:
a finished session is detected first (0 model requests), an attempt is replayed with the same
input (served by the exact result cache), and the persistence writes are idempotent.
"""

import logging

from psycopg_pool import ConnectionPool

from app.intelligence.mastery.ledger import recompute_ledger
from app.intelligence.policy import IntelligencePolicy, load_policy
from app.intelligence.recommendations.service import refresh_recommendations
from app.intelligence.verification.evaluator import evaluate_with_rubric
from app.intelligence.verification.generator import GenerationRequest, generate_challenge
from app.intelligence.verification.graders import grade_deterministically
from app.intelligence.verification.persist import (
    GenerationPlan,
    Submission,
    complete_grading,
    issue_challenge,
    load_generation_plan,
    load_submission,
    mark_failed,
    record_rejection,
    repair_grading,
)
from app.intelligence.verification.validator import ValidationContext, validate_challenge
from app.jobs.queue import ClaimedJob, JobResult
from app.model_gateway import ModelGateway, RunContext

logger = logging.getLogger("skillmirror.verification")

DETERMINISTIC_TYPES = ("mcq", "numeric")


def allowed_types(skill_types: tuple[str, ...], policy: IntelligencePolicy) -> tuple[str, ...]:
    """Every supported type, the skill's own types first, deterministic graders first in each."""
    supported = policy.verification.generation.supported_assessment_types
    return tuple(
        sorted(
            supported,
            key=lambda t: (t not in skill_types, t not in DETERMINISTIC_TYPES, supported.index(t)),
        )
    )


def _context(job: ClaimedJob, learner_id, course_id) -> RunContext:
    return RunContext(
        trace_id=f"job:{job.id}",
        learner_id=learner_id,
        course_id=course_id,
        processing_job_id=job.id,
    )


def run_generation_job(pool: ConnectionPool, gateway: ModelGateway, job: ClaimedJob) -> JobResult:
    with pool.connection() as conn:
        policy = load_policy(conn)
        plan = load_generation_plan(
            conn, job.entity_id, history_limit=policy.verification.generation.history_limit
        )
    if plan is None:
        return JobResult("SESSION_NOT_FOUND")
    if plan.state != "PLANNED":
        return JobResult("ALREADY_ISSUED")
    if plan.failure_code is not None:
        return JobResult("VERIFICATION_NOT_ISSUED")

    generation = policy.verification.generation
    types = allowed_types(plan.skill_assessment_types, policy)
    validation = ValidationContext(
        skill_id=str(plan.skill_id),
        difficulty_min=plan.difficulty_min,
        difficulty_max=plan.difficulty_max,
        allowed_types=types,
        known_prerequisites=frozenset(str(p.skill_id) for p in plan.prerequisites),
        history_prompts=plan.history_prompts,
        source_texts=plan.source_texts,
    )
    context = _context(job, plan.learner_id, plan.course_id)
    rejections = list(plan.rejections)
    for attempt in range(plan.generation_attempts + 1, generation.max_attempts + 1):
        result = generate_challenge(gateway, _request(plan, types, attempt, rejections), context)
        if result.output is None:
            reasons: tuple[str, ...] = ("MODEL_OUTPUT_INVALID",)
        else:
            report = validate_challenge(result.output, validation, generation)
            if report.accepted and result.run is not None:
                with pool.connection() as conn:
                    issue_challenge(
                        conn,
                        plan,
                        result.output,
                        report,
                        attempt=attempt,
                        model_run_id=result.run.id,
                    )
                logger.info(
                    "verification %s READY (attempt %d, %s)",
                    plan.session_id,
                    attempt,
                    result.output.assessment_type,
                )
                return JobResult("VERIFICATION_READY")
            reasons = report.reasons
        logger.info("verification %s attempt %d rejected: %s", plan.session_id, attempt, reasons)
        with pool.connection() as conn:
            record_rejection(
                conn, plan.session_id, attempt, reasons, result.run.id if result.run else None
            )
        rejections.append(reasons)
    with pool.connection() as conn:
        mark_failed(conn, plan.session_id, "PLANNED", "GENERATION_REJECTED")
    logger.warning(
        "verification %s not issued: every generation attempt was rejected", plan.session_id
    )
    return JobResult("VERIFICATION_NOT_ISSUED")


def _request(
    plan: GenerationPlan, types: tuple[str, ...], attempt: int, rejections: list[tuple[str, ...]]
) -> GenerationRequest:
    return GenerationRequest(
        session_id=plan.session_id,
        skill_id=plan.skill_id,
        canonical_name=plan.canonical_name,
        description=plan.description,
        course_context=plan.course_context,
        difficulty_min=plan.difficulty_min,
        difficulty_max=plan.difficulty_max,
        planned_difficulty=plan.planned_difficulty,
        allowed_types=types,
        prerequisites=plan.prerequisites,
        history_prompts=plan.history_prompts,
        attempt=attempt,
        previous_rejections=tuple(rejections),
    )


def _project(pool: ConnectionPool, sub: Submission, policy: IntelligencePolicy) -> None:
    """The ledger and the queue are re-derived from evidence (never from the result)."""
    with pool.connection() as conn:
        recompute_ledger(conn, sub.learner_id, [sub.skill_id], policy=policy)
        refresh_recommendations(conn, sub.learner_id, policy=policy)


def run_grading_job(pool: ConnectionPool, gateway: ModelGateway, job: ClaimedJob) -> JobResult:
    with pool.connection() as conn:
        policy = load_policy(conn)
        sub = load_submission(conn, job.entity_id)
    if sub is None:
        return JobResult("SESSION_NOT_FOUND")
    if sub.result_id is not None:
        # Replay / crash repair: the grade exists; make sure its evidence and the projections do.
        with pool.connection() as conn:
            repair_grading(conn, sub, policy=policy)
        _project(pool, sub, policy)
        return JobResult("ALREADY_EVALUATED")
    if sub.state != "SUBMITTED" or sub.response is None:
        return JobResult("NOT_SUBMITTED")
    if sub.failure_code is not None:
        return JobResult("VERIFICATION_NOT_GRADED")

    grading = policy.verification.grading
    grade = grade_deterministically(sub.item, sub.response, grading)
    if grade is None:
        outcome = evaluate_with_rubric(
            gateway,
            sub.item,
            str(sub.response.get("answer", "")),
            grading,
            _context(job, sub.learner_id, sub.course_id),
        )
        if outcome.grade is None:
            with pool.connection() as conn:
                mark_failed(conn, sub.session_id, "SUBMITTED", outcome.failure_code or "NOT_GRADED")
            logger.warning(
                "verification %s not graded (%s): no evidence", sub.session_id, outcome.failure_code
            )
            return JobResult("VERIFICATION_NOT_GRADED")
        grade = outcome.grade
    with pool.connection() as conn:
        complete_grading(conn, sub, grade, policy=policy, processing_job_id=job.id)
    _project(pool, sub, policy)
    logger.info(
        "verification %s EVALUATED %s (score %.2f, %s)",
        sub.session_id,
        grade.outcome_signal,
        grade.score,
        grade.evaluator_type,
    )
    return JobResult("VERIFICATION_EVALUATED")


def mark_generation_failed(pool: ConnectionPool, job: ClaimedJob, error: str, final: bool) -> None:
    """Once the job has no attempts left (a non-transient error), close the plan honestly."""
    if final:
        with pool.connection() as conn:
            mark_failed(conn, job.entity_id, "PLANNED", "GENERATION_FAILED")


def mark_grading_failed(pool: ConnectionPool, job: ClaimedJob, error: str, final: bool) -> None:
    """Once the job has no attempts left: the answer stays SUBMITTED, recorded as not graded."""
    if final:
        with pool.connection() as conn:
            mark_failed(conn, job.entity_id, "SUBMITTED", "GRADING_FAILED")
