"""Verification persistence (architecture §7.1, §7.2, §10.1; ADR 0007). No model call.

* Issuing a challenge is ONE transaction: the validated item + PLANNED -> READY.
* Completing a grade is ONE transaction: the immutable verification_results row + exactly one
  VERIFICATION EvidenceEvent (source_id = the result) + SUBMITTED -> EVALUATED (+ the
  recommendation it answered becomes COMPLETED). No contradictory partial state can exist, and a
  replay finds the result and writes nothing new. The ledger and the recommendation queue are
  re-derived afterwards (rebuildable projections), never written from the result.

The migration 0008 triggers re-check every link (item -> PLANNED session in the band with known
prerequisites; result -> SUBMITTED session and its exact response; evidence -> its result).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.intelligence.contracts import VerificationGenerationOutput
from app.intelligence.policy import IntelligencePolicy
from app.intelligence.verification.evidence import QUALIFIER_VERSION, verification_evidence
from app.intelligence.verification.generator import (
    GENERATOR_VERSION,
    PROMPT_VERSION,
    Prerequisite,
    fingerprint,
)
from app.intelligence.verification.graders import ChallengeItem, Grade, challenge_item
from app.intelligence.verification.validator import VALIDATOR_VERSION, ValidationReport

GRADER_FOR = {"mcq": "MCQ_EXACT", "numeric": "NUMERIC_TOLERANCE"}
SPAN_PREVIEW_CHARS = 280


@dataclass(frozen=True)
class GenerationPlan:
    session_id: UUID
    learner_id: UUID
    course_id: UUID | None
    skill_id: UUID
    state: str
    failure_code: str | None
    generation_attempts: int
    rejections: tuple[tuple[str, ...], ...]
    planned_difficulty: float
    difficulty_min: float
    difficulty_max: float
    canonical_name: str
    description: str
    skill_assessment_types: tuple[str, ...]
    course_context: str
    prerequisites: tuple[Prerequisite, ...]
    history_prompts: tuple[str, ...]
    source_texts: tuple[str, ...]


def load_generation_plan(
    conn: Connection, session_id: UUID, *, history_limit: int
) -> GenerationPlan | None:
    row = conn.execute(
        """
        select s.id, s.learner_id, s.course_id, s.skill_id, s.state::text, s.failure_code,
               s.generation_attempts, s.generation_rejections, s.planned_difficulty,
               s.difficulty_min, s.difficulty_max, n.canonical_name, n.description,
               n.assessment_types, c.name, c.subject, c.level
          from public.verification_sessions s
          join public.skill_nodes n on n.id = s.skill_id
          left join public.courses c on c.id = s.course_id
         where s.id = %s
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    learner_id, skill_id = row[1], row[3]
    prerequisites = tuple(
        Prerequisite(r[0], r[1])
        for r in conn.execute(
            """
            select p.id, p.canonical_name from public.skill_edges e
              join public.skill_nodes p on p.id = e.from_skill_id and p.status = 'ACTIVE'
             where e.edge_type = 'PREREQUISITE' and e.to_skill_id = %s
             order by p.canonical_name, p.id
            """,
            (skill_id,),
        ).fetchall()
    )
    # The learner's earlier challenges for the skill, newest first (bounded history).
    history = tuple(
        r[0]
        for r in conn.execute(
            """
            select i.prompt from public.verification_items i
             where i.learner_id = %s and i.skill_id = %s and i.session_id <> %s
             order by i.created_at desc, i.id
             limit %s
            """,
            (learner_id, skill_id, session_id, history_limit),
        ).fetchall()
    )
    # The captured activity that led to this verification (never sent to the model; the
    # validator refuses a challenge that merely repeats it).
    sources = tuple(
        r[0]
        for r in conn.execute(
            """
            select s.text from public.evidence_events e
              join public.activity_segments s on s.id = e.segment_id
             where e.learner_id = %s and e.skill_id = %s and e.source_type = 'AI_ACTIVITY'
             order by e.occurred_at desc, e.id
             limit %s
            """,
            (learner_id, skill_id, max(history_limit, 1)),
        ).fetchall()
    )
    name, subject, level = row[14], row[15], row[16]
    course = ", ".join(v for v in (name, subject, level) if v) or "(no course context)"
    return GenerationPlan(
        session_id=row[0],
        learner_id=learner_id,
        course_id=row[2],
        skill_id=skill_id,
        state=row[4],
        failure_code=row[5],
        generation_attempts=row[6],
        rejections=tuple(tuple(r.get("reasons", ())) for r in row[7]),
        planned_difficulty=row[8],
        difficulty_min=row[9],
        difficulty_max=row[10],
        canonical_name=row[11],
        description=row[12],
        skill_assessment_types=tuple(row[13]),
        course_context=course,
        prerequisites=prerequisites,
        history_prompts=history,
        source_texts=sources,
    )


def record_rejection(
    conn: Connection, session_id: UUID, attempt: int, reasons: tuple[str, ...], run_id: UUID | None
) -> None:
    """One rejected attempt (codes only). Guarded by the attempt count, so a replay of the same
    attempt records it once."""
    conn.execute(
        """
        update public.verification_sessions
           set generation_attempts = %s,
               generation_rejections = generation_rejections || %s::jsonb
         where id = %s and state = 'PLANNED' and failure_code is null
           and generation_attempts = %s
        """,
        (
            attempt,
            Jsonb(
                [
                    {
                        "attempt": attempt,
                        "reasons": list(reasons),
                        "model_run_id": str(run_id) if run_id else None,
                    }
                ]
            ),
            session_id,
            attempt - 1,
        ),
    )


def issue_challenge(
    conn: Connection,
    plan: GenerationPlan,
    output: VerificationGenerationOutput,
    report: ValidationReport,
    *,
    attempt: int,
    model_run_id: UUID,
) -> bool:
    """The validated item and PLANNED -> READY, in one transaction. False if already issued."""
    with conn.transaction():
        row = conn.execute(
            "select state::text, failure_code from public.verification_sessions where id = %s "
            "for update",
            (plan.session_id,),
        ).fetchone()
        if row is None or row[0] != "PLANNED" or row[1] is not None:
            return False
        conn.execute(
            """
            insert into public.verification_items (
                session_id, learner_id, skill_id, assessment_type, grader_type, prompt, choices,
                expected_answer, rubric, difficulty, prerequisites_used, transfer_distance,
                estimated_minutes, generator_version, prompt_version, generation_model_run_id,
                generation_attempt, prompt_fingerprint, history_fingerprints, validator_version,
                validation
            ) values (%s, %s, %s, %s::public.verification_assessment_type,
                      %s::public.verification_grader_type, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                plan.session_id,
                plan.learner_id,
                plan.skill_id,
                output.assessment_type,
                GRADER_FOR.get(output.assessment_type, "RUBRIC_AI"),
                output.prompt.strip(),
                Jsonb([{"key": c.key, "text": c.text} for c in output.choices]),
                output.expected_answer,
                Jsonb([{"criterion": c.criterion, "points": c.points} for c in output.rubric]),
                output.difficulty,
                [UUID(p) for p in output.prerequisites_used],
                output.transfer_distance,
                output.estimated_minutes,
                GENERATOR_VERSION,
                PROMPT_VERSION,
                model_run_id,
                attempt,
                fingerprint(output.prompt),
                [fingerprint(p) for p in plan.history_prompts],
                VALIDATOR_VERSION,
                Jsonb(report.as_json()),
            ),
        )
        conn.execute(
            """
            update public.verification_sessions
               set state = 'READY', ready_at = now(), generation_attempts = %s
             where id = %s
            """,
            (attempt, plan.session_id),
        )
    return True


def mark_failed(conn: Connection, session_id: UUID, state: str, failure_code: str) -> bool:
    """Record an honest terminal pipeline failure (never a learner result)."""
    row = conn.execute(
        """
        update public.verification_sessions set failure_code = %s, failed_at = now()
         where id = %s and state = %s::public.verification_state and failure_code is null
        returning id
        """,
        (failure_code, session_id, state),
    ).fetchone()
    return row is not None


@dataclass(frozen=True)
class Submission:
    session_id: UUID
    learner_id: UUID
    course_id: UUID | None
    skill_id: UUID
    recommendation_id: UUID | None
    state: str
    failure_code: str | None
    submitted_at: datetime | None
    response: dict[str, Any] | None
    item: ChallengeItem
    difficulty: float
    generation_model_run_id: UUID
    result_id: UUID | None


def load_submission(conn: Connection, session_id: UUID) -> Submission | None:
    row = conn.execute(
        """
        select s.id, s.learner_id, s.course_id, s.skill_id, s.recommendation_id, s.state::text,
               s.failure_code, s.submitted_at, s.submitted_response,
               i.id, i.assessment_type::text, i.grader_type::text, i.prompt, i.choices,
               i.expected_answer, i.rubric, i.difficulty, i.generation_model_run_id, r.id
          from public.verification_sessions s
          join public.verification_items i on i.session_id = s.id
          left join public.verification_results r on r.item_id = i.id
         where s.id = %s
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return Submission(
        session_id=row[0],
        learner_id=row[1],
        course_id=row[2],
        skill_id=row[3],
        recommendation_id=row[4],
        state=row[5],
        failure_code=row[6],
        submitted_at=row[7],
        response=row[8],
        item=challenge_item(row[9:16]),
        difficulty=row[16],
        generation_model_run_id=row[17],
        result_id=row[18],
    )


def _preview(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SPAN_PREVIEW_CHARS else text[: SPAN_PREVIEW_CHARS - 1] + "…"


def _response_text(response: dict[str, Any]) -> str:
    if "selected" in response:
        return ", ".join(response["selected"])
    return str(response.get("answer", ""))


def _insert_evidence(
    conn: Connection,
    sub: Submission,
    result_id: UUID,
    *,
    outcome_signal: str,
    outcome: float,
    grading_confidence: float,
    model_run_ids: list[UUID],
    policy: IntelligencePolicy,
    processing_job_id: UUID | None,
) -> None:
    ev = verification_evidence(
        outcome_signal=outcome_signal,
        outcome=outcome,
        difficulty=sub.difficulty,
        grading_confidence=grading_confidence,
        policy=policy.evidence,
    )
    conn.execute(
        """
        insert into public.evidence_events (
            learner_id, skill_id, source_type, source_id, raw_message_ids, evidence_type, actor,
            outcome_signal, outcome, difficulty, difficulty_multiplier, independence, base_weight,
            strength, mapping_confidence, attribution_confidence, grading_confidence,
            evidence_confidence, evidence_span, model_run_ids, qualification_reason,
            qualifier_version, policy_snapshot, occurred_at, processing_job_id
        ) values (%s, %s, 'VERIFICATION', %s, '{}', 'VERIFICATION', 'STUDENT',
                  %s::public.outcome_signal, %s, %s, %s, %s, %s, %s, 1, 1, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s)
        on conflict on constraint evidence_events_source_key do nothing
        """,
        (
            sub.learner_id,
            sub.skill_id,
            result_id,
            ev.outcome_signal,
            ev.outcome,
            ev.difficulty,
            ev.difficulty_multiplier,
            ev.independence,
            ev.base_weight,
            ev.strength,
            ev.grading_confidence,
            ev.evidence_confidence,
            Jsonb(
                {
                    "student": _preview(_response_text(sub.response or {})),
                    "challenge": _preview(sub.item.prompt),
                }
            ),
            list(dict.fromkeys(model_run_ids)),
            ev.qualification_reason,
            QUALIFIER_VERSION,
            Jsonb(
                {
                    "evidence": policy.evidence.model_dump(mode="json"),
                    "grading": policy.verification.grading.model_dump(mode="json"),
                }
            ),
            sub.submitted_at,
            processing_job_id,
        ),
    )


def complete_grading(
    conn: Connection,
    sub: Submission,
    grade: Grade,
    *,
    policy: IntelligencePolicy,
    processing_job_id: UUID | None,
) -> UUID:
    """Result + VERIFICATION evidence + EVALUATED (+ recommendation COMPLETED), atomically.

    Idempotent: an existing result is reused and nothing is written twice."""
    with conn.transaction():
        conn.execute(
            "select 1 from public.verification_sessions where id = %s for update", (sub.session_id,)
        )
        existing = conn.execute(
            "select id from public.verification_results where item_id = %s", (sub.item.id,)
        ).fetchone()
        if existing is not None:
            finalize_existing(conn, sub, existing[0], policy=policy)
            return existing[0]
        (result_id,) = conn.execute(
            """
            insert into public.verification_results (
                item_id, session_id, learner_id, skill_id, response, score, pass, outcome_signal,
                outcome, evaluation, feedback, grading_confidence, evaluator_type,
                evaluator_version, evaluator_model_run_id, evaluator_prompt_version,
                policy_snapshot, processing_job_id
            ) values (%s, %s, %s, %s, %s, %s, %s, %s::public.outcome_signal, %s, %s, %s, %s,
                      %s::public.verification_evaluator_type, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                sub.item.id,
                sub.session_id,
                sub.learner_id,
                sub.skill_id,
                Jsonb(sub.response),
                grade.score,
                grade.passed,
                grade.outcome_signal,
                grade.outcome,
                Jsonb(grade.evaluation),
                grade.feedback,
                grade.grading_confidence,
                grade.evaluator_type,
                grade.evaluator_version,
                grade.evaluator_model_run_id,
                grade.evaluator_prompt_version,
                Jsonb({"grading": policy.verification.grading.model_dump(mode="json")}),
                processing_job_id,
            ),
        ).fetchone()
        _insert_evidence(
            conn,
            sub,
            result_id,
            outcome_signal=grade.outcome_signal,
            outcome=grade.outcome,
            grading_confidence=grade.grading_confidence,
            model_run_ids=[sub.generation_model_run_id, *grade.model_run_ids],
            policy=policy,
            processing_job_id=processing_job_id,
        )
        _evaluated(conn, sub)
    return result_id


def repair_grading(conn: Connection, sub: Submission, *, policy: IntelligencePolicy) -> None:
    """Replay of a graded session: its evidence exists and the session is EVALUATED (no-op when
    everything is already in place)."""
    if sub.result_id is None:
        raise ValueError(f"session {sub.session_id} has no graded result to repair")
    with conn.transaction():
        conn.execute(
            "select 1 from public.verification_sessions where id = %s for update", (sub.session_id,)
        )
        finalize_existing(conn, sub, sub.result_id, policy=policy)


def finalize_existing(
    conn: Connection, sub: Submission, result_id: UUID, *, policy: IntelligencePolicy
) -> None:
    """The result's evidence exists and the session is EVALUATED (idempotent)."""
    row = conn.execute(
        """
        select outcome_signal::text, outcome, grading_confidence, evaluator_model_run_id,
               processing_job_id
          from public.verification_results where id = %s
        """,
        (result_id,),
    ).fetchone()
    runs = [sub.generation_model_run_id] + ([row[3]] if row[3] else [])
    _insert_evidence(
        conn,
        sub,
        result_id,
        outcome_signal=row[0],
        outcome=row[1],
        grading_confidence=row[2],
        model_run_ids=runs,
        policy=policy,
        processing_job_id=row[4],
    )
    _evaluated(conn, sub)


def _evaluated(conn: Connection, sub: Submission) -> None:
    conn.execute(
        "update public.verification_sessions set state = 'EVALUATED', evaluated_at = now() "
        "where id = %s and state = 'SUBMITTED'",
        (sub.session_id,),
    )
    if sub.recommendation_id is not None:
        # The recommendation was acted on; the refresh derives the next action from the ledger.
        conn.execute(
            "update public.recommendations set state = 'COMPLETED', resolved_at = now() "
            "where id = %s and learner_id = %s and state = 'ACTIVE'",
            (sub.recommendation_id, sub.learner_id),
        )
