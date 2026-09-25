"""PROCESS_RAW_MESSAGE job, P3A scope (architecture §9.1-§9.4).

    claim -> pair/prepare turn -> analyse (qualify, route, retrieve, rerank,
          map/abstain; see processing/analysis.py) -> persist analysis -> complete

The analysis mode is "combined" by default (local retrieval, then one
TURN_ANALYSIS call and at most one adjudication call; ADR 0004) or "staged".
Model calls run outside any database transaction; the analysis is written in
one transaction at the end. The pipeline stops after mapping/abstention:
attribution and EvidenceEvents are P3B.
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection
from psycopg_pool import ConnectionPool

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
) -> JobResult:
    with pool.connection() as conn:
        policy = load_policy(conn)
        decision = build_unit(conn, job.entity_id, policy.processing_unit)
        if decision.unit is None:
            return JobResult(decision.outcome or "SKIPPED", decision.defer_seconds)
        unit = decision.unit
        if already_analyzed(conn, unit.anchor.id, ANALYSIS_VERSION):
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
    if not written:
        return JobResult("ALREADY_ANALYZED")
    return JobResult(overall_outcome(result.analyses))
