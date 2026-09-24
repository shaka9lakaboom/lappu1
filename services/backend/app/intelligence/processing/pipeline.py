"""PROCESS_RAW_MESSAGE job, P3A scope (architecture §9.1-§9.4).

    claim -> pair/prepare turn -> qualify -> route -> retrieve -> map/abstain
          -> persist analysis -> complete

Model calls run outside any database transaction; the analysis is written in
one transaction at the end. The pipeline stops after mapping/abstention:
attribution and EvidenceEvents are P3B.
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection
from psycopg_pool import ConnectionPool

from app.intelligence.mapping.engine import map_segment
from app.intelligence.policy import load_policy
from app.intelligence.processing.persist import (
    ANALYSIS_VERSION,
    SegmentAnalysis,
    already_analyzed,
    persist_analysis,
)
from app.intelligence.processing.turn import ProcessingUnit, build_unit, clip, render_context
from app.intelligence.relevance.engine import (
    PROMPT_VERSION as QUALIFICATION_PROMPT_VERSION,
)
from app.intelligence.relevance.engine import (
    ProcessingUnitText,
    qualify_unit,
    unit_as_text,
)
from app.intelligence.relevance.routing import route_segment
from app.intelligence.retrieval.engine import retrieve_candidates
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
    pool: ConnectionPool, gateway: ModelGateway, job: ClaimedJob
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
    qualification = qualify_unit(gateway, text, policy.qualification, context)

    analyses: list[SegmentAnalysis] = []
    if qualification.segments is None:
        analyses.append(
            SegmentAnalysis(
                text=unit_as_text(text),
                segment=None,
                route="UNCERTAIN",
                route_reason=qualification.abstain_reason or "MODEL_OUTPUT_INVALID",
                reason_code=qualification.abstain_reason or "MODEL_OUTPUT_INVALID",
            )
        )
    else:
        for segment in qualification.segments:
            routed = route_segment(
                segment, context_incomplete=unit.context_incomplete, policy=policy.qualification
            )
            retrieval = mapping = None
            if routed.route == "MAP":
                with pool.connection() as conn:
                    retrieval = retrieve_candidates(
                        conn,
                        gateway,
                        query_text=segment.text,
                        course_ids=course_ids,
                        course_context=text.course_context,
                        policy=policy.retrieval,
                        context=context,
                    )
                by_id = {c.skill_id: c for c in retrieval.candidates}
                mapping = map_segment(
                    gateway,
                    segment_text=segment.text,
                    course_context=text.course_context,
                    candidates=[by_id[i] for i in retrieval.reranked_ids],
                    policy=policy.mapping,
                    context=context,
                )
            analyses.append(
                SegmentAnalysis(
                    text=segment.text,
                    segment=segment,
                    route=routed.route,
                    route_reason=routed.reason,
                    reason_code=segment.reason_code,
                    retrieval=retrieval,
                    mapping=mapping,
                )
            )

    with pool.connection() as conn:
        written = persist_analysis(
            conn,
            unit=unit,
            analyses=analyses,
            course_ids=course_ids,
            qualification_run_id=qualification.run.id if qualification.run else None,
            qualification_prompt_version=QUALIFICATION_PROMPT_VERSION,
            policy_snapshot=policy.snapshot(),
            processing_job_id=job.id,
        )
    if not written:
        return JobResult("ALREADY_ANALYZED")
    return JobResult(overall_outcome(analyses))
