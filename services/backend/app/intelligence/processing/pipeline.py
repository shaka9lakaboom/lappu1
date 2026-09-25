"""PROCESS_RAW_MESSAGE job (architecture §9, §10; ADR 0003, 0004, 0005).

    claim -> pair/prepare turn
          -> P3A: analyse (qualify, route, retrieve, rerank, map/abstain; see
             processing/analysis.py) -> persist analysis (one transaction)
          -> P3B: per MAPPED segment, attribution -> evidence qualification
             -> attributions + evidence (one transaction per segment)
          -> P4: deterministic ledger recompute (mastery + AI Assistance Debt)
          -> complete

The analysis mode is "combined" by default (local retrieval, then one
TURN_ANALYSIS call and at most one adjudication call; ADR 0004) or "staged".
A mapped segment adds exactly one SKILL_ATTRIBUTION call; evidence strength,
mastery and debt make none. Model calls run outside any database transaction.
A job that already has its P3A analysis resumes at the pending attribution, so
a deferral (429/503, budget) never repeats the P3A calls.
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection
from psycopg_pool import ConnectionPool

from app.intelligence.evidence.stage import run_evidence_stage
from app.intelligence.policy import load_policy
from app.intelligence.processing.analysis import AnalysisMode, analyze_unit
from app.intelligence.processing.persist import (
    ANALYSIS_VERSION,
    SegmentAnalysis,
    already_analyzed,
    persist_analysis,
)
from app.intelligence.processing.turn import ProcessingUnit, build_unit, clip, render_context
from app.intelligence.relevance.engine import ProcessingUnitText
from app.jobs.queue import ClaimedJob, JobResult
from app.model_gateway import ModelGateway, RunContext


@dataclass(frozen=True)
class CourseRef:
    id: UUID
    name: str
    subject: str | None
    level: str | None


def resolve_courses(
    conn: Connection, learner_id: UUID, active_course_id: UUID | None
) -> list[CourseRef]:
    """The explicitly selected course if the learner belongs to it, else all their courses."""
    base = """
        select c.id, c.name, c.subject, c.level from public.courses c
          join public.course_memberships m on m.course_id = c.id and m.user_id = %s
         where c.status = 'ACTIVE'
    """
    if active_course_id is not None:
        row = conn.execute(base + " and c.id = %s", (learner_id, active_course_id)).fetchone()
        if row:
            return [CourseRef(*row)]
    rows = conn.execute(base + " order by c.created_at desc limit 5", (learner_id,)).fetchall()
    return [CourseRef(*r) for r in rows]


def course_context_text(courses: list[CourseRef]) -> str:
    if not courses:
        return "(the learner has no course; use the global skill registry)"
    parts = []
    for c in courses:
        details = ", ".join(x for x in (c.level, c.subject) if x)
        parts.append(f"{c.name} ({details})" if details else c.name)
    return "; ".join(parts)


def unit_text(unit: ProcessingUnit, courses: list[CourseRef], max_chars: int, ctx_chars: int):
    half = max_chars // 2
    if unit.attachment_count:
        note = f"{unit.attachment_count} attachment(s); content not captured"
    else:
        note = "none"
    return ProcessingUnitText(
        user_text=clip(unit.user.content_text, half) if unit.user else None,
        assistant_text=clip(unit.assistant.content_text, half) if unit.assistant else None,
        recent_context=render_context(unit.context, ctx_chars),
        course_context=course_context_text(courses),
        context_incomplete=unit.context_incomplete,
        attachment_note=note,
    )


def overall_outcome(analyses: list[SegmentAnalysis]) -> str:
    if any(a.mapping and a.mapping.outcome == "MAPPED" for a in analyses):
        return "MAPPED"
    routes = {a.route for a in analyses}
    for route, outcome in (
        ("MAP", "ABSTAINED"),
        ("UNCERTAIN", "UNCERTAIN"),
        ("METADATA_ONLY", "METADATA_ONLY"),
    ):
        if route in routes:
            return outcome
    return "NON_LEARNING"


def process_raw_message_job(
    pool: ConnectionPool,
    gateway: ModelGateway,
    job: ClaimedJob,
    *,
    mode: AnalysisMode = "combined",
    evidence: bool = True,
) -> JobResult:
    """`evidence=False` runs the P3A stage alone (used by the P3A tests)."""
    with pool.connection() as conn:
        policy = load_policy(conn)
        decision = build_unit(conn, job.entity_id, policy.processing_unit)
        if decision.unit is None:
            return JobResult(decision.outcome or "SKIPPED", decision.defer_seconds)
        unit = decision.unit
        analyzed = already_analyzed(conn, unit.anchor.id, ANALYSIS_VERSION)
        if analyzed and not evidence:
            return JobResult("ALREADY_ANALYZED")
        courses = resolve_courses(conn, unit.learner_id, unit.active_course_id)

    course_ids = [c.id for c in courses]
    context = RunContext(
        trace_id=f"job:{job.id}",
        learner_id=unit.learner_id,
        course_id=course_ids[0] if len(course_ids) == 1 else None,
        processing_job_id=job.id,
    )
    text = unit_text(
        unit,
        courses,
        policy.processing_unit.unit_max_chars,
        policy.processing_unit.recent_context_max_chars,
    )
    outcome = "ALREADY_ANALYZED"
    if not analyzed:
        result = analyze_unit(
            pool, gateway, text, course_ids=course_ids, policy=policy, context=context, mode=mode
        )
        with pool.connection() as conn:
            written = persist_analysis(
                conn,
                unit=unit,
                analyses=result.analyses,
                course_ids=course_ids,
                qualification_run_id=result.qualification_run_id,
                qualification_prompt_version=result.qualification_prompt_version,
                policy_snapshot=policy.snapshot(),
                processing_job_id=job.id,
            )
        if written:
            outcome = overall_outcome(result.analyses)
    if not evidence:
        return JobResult(outcome)

    stage = run_evidence_stage(
        pool,
        gateway,
        unit=unit,
        text=text,
        policy=policy,
        context=context,
        processing_job_id=job.id,
    )
    if outcome == "ALREADY_ANALYZED" and not stage.attributed_segments:
        return JobResult(outcome)  # a replay: nothing new (the ledger was re-derived)
    return JobResult(stage.outcome or outcome)
