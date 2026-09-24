"""P3A persistence: segments, mapping decisions, skill mappings, skill candidates.

One transaction per analysed unit, guarded by a per-anchor advisory lock and
the (anchor, analysis_version, segment_index) unique key, so a replayed or
concurrently recovered job can never write a second analysis. No evidence,
ledger, debt or verification rows are written here (P3B/P4).
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.intelligence.contracts import RetrievalResult
from app.intelligence.mapping.engine import (
    ADJUDICATION_PROMPT_VERSION,
    MAPPER_VERSION,
    MAPPING_PROMPT_VERSION,
    MappingResult,
)
from app.intelligence.processing.turn import ProcessingUnit
from app.intelligence.relevance.engine import QualifiedSegment
from app.intelligence.retrieval.engine import QUERY_INPUT_VERSION
from app.intelligence.retrieval.rerank import PROMPT_VERSION as RERANK_PROMPT_VERSION
from app.intelligence.skill_graph.canonical import skill_key

ANALYSIS_VERSION = "p3a-v1"


@dataclass(frozen=True)
class SegmentAnalysis:
    text: str
    segment: QualifiedSegment | None  # None when qualification abstained
    route: str
    route_reason: str
    reason_code: str
    retrieval: RetrievalResult | None = None
    mapping: MappingResult | None = None


def already_analyzed(conn: Connection, anchor_id: UUID, analysis_version: str) -> bool:
    return (
        conn.execute(
            "select 1 from public.activity_segments "
            "where anchor_message_id = %s and analysis_version = %s limit 1",
            (anchor_id, analysis_version),
        ).fetchone()
        is not None
    )


def _upsert_candidate(
    conn: Connection, mapping: MappingResult, course_id: UUID | None
) -> UUID | None:
    proposed = mapping.new_skill_candidate
    if proposed is None:
        return None
    key = skill_key(proposed.canonical_name)
    if not key:
        return None
    # Already a registry name or alias: not new. It is left for review via the
    # decision's retrieval pool; it is never auto-mapped.
    if conn.execute(
        "select 1 from public.skill_aliases where normalized_alias = %s", (key,)
    ).fetchone():
        return None
    parent_ok = (
        proposed.parent_candidate_id
        and conn.execute(
            "select 1 from public.skill_nodes where id = %s and status = 'ACTIVE'",
            (proposed.parent_candidate_id,),
        ).fetchone()
    )
    (candidate_id,) = conn.execute(
        """
        insert into public.skill_candidates
            (canonical_name, normalized_name, description, parent_candidate_id,
             first_course_id, first_model_run_id)
        values (%s, %s, %s, %s, %s, %s)
        on conflict (normalized_name) where status = 'PENDING_REVIEW'
        do update set occurrences = public.skill_candidates.occurrences + 1
        returning id
        """,
        (
            proposed.canonical_name,
            key,
            proposed.description,
            proposed.parent_candidate_id if parent_ok else None,
            course_id,
            mapping.mapping_run_id,
        ),
    ).fetchone()
    return candidate_id


def persist_analysis(
    conn: Connection,
    *,
    unit: ProcessingUnit,
    analyses: list[SegmentAnalysis],
    course_ids: list[UUID],
    qualification_run_id: UUID | None,
    qualification_prompt_version: str,
    policy_snapshot: dict,
    processing_job_id: UUID | None,
) -> bool:
    """Write the unit's analysis. Returns False if it already existed (no-op)."""
    with conn.transaction():
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"analysis:{unit.anchor.id}",)
        )
        if already_analyzed(conn, unit.anchor.id, ANALYSIS_VERSION):
            return False
        for index, analysis in enumerate(analyses):
            seg = analysis.segment
            (segment_id,) = conn.execute(
                """
                insert into public.activity_segments (
                    learner_id, conversation_id, anchor_message_id, user_message_id,
                    assistant_message_id, source_message_ids, context_message_ids,
                    segment_index, segment_count, text, context, intent, learning_relevance,
                    relevance_confidence, skill_bearing, skill_bearing_confidence, reason_code,
                    route, route_reason, context_incomplete, course_ids,
                    qualification_model_run_id, prompt_version, analysis_version,
                    processing_job_id
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::public.segment_context, %s::public.segment_intent,
                    %s::public.learning_relevance, %s, %s, %s, %s,
                    %s::public.segment_route, %s, %s, %s, %s, %s, %s, %s
                )
                returning id
                """,
                (
                    unit.learner_id,
                    unit.conversation_id,
                    unit.anchor.id,
                    unit.user.id if unit.user else None,
                    unit.assistant.id if unit.assistant else None,
                    unit.source_message_ids,
                    [m.id for m in unit.context],
                    index,
                    len(analyses),
                    analysis.text[:20000],
                    seg.context if seg else None,
                    seg.intent if seg else None,
                    seg.learning_relevance if seg else None,
                    seg.relevance_confidence if seg else None,
                    seg.skill_bearing if seg else None,
                    seg.skill_bearing_confidence if seg else None,
                    analysis.reason_code,
                    analysis.route,
                    analysis.route_reason,
                    unit.context_incomplete,
                    course_ids,
                    qualification_run_id,
                    qualification_prompt_version,
                    ANALYSIS_VERSION,
                    processing_job_id,
                ),
            ).fetchone()

            if analysis.mapping is None or analysis.retrieval is None:
                continue
            mapping, retrieval = analysis.mapping, analysis.retrieval
            candidate_id = _upsert_candidate(
                conn, mapping, course_ids[0] if len(course_ids) == 1 else None
            )
            (decision_id,) = conn.execute(
                """
                insert into public.mapping_decisions (
                    segment_id, learner_id, outcome, abstain_reason, retrieval_candidates,
                    mapper_candidate_ids, rerank_fallback, query_model_run_id,
                    rerank_model_run_id, mapping_model_run_id, adjudication_model_run_id,
                    prompt_versions, policy_snapshot, new_skill_candidate_id, mapper_version
                ) values (
                    %s, %s, %s::public.mapping_outcome, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                returning id
                """,
                (
                    segment_id,
                    unit.learner_id,
                    mapping.outcome,
                    mapping.abstain_reason,
                    Jsonb([c.model_dump(mode="json") for c in retrieval.candidates]),
                    list(retrieval.reranked_ids),
                    retrieval.rerank_fallback,
                    retrieval.query_model_run_id,
                    retrieval.rerank_model_run_id,
                    mapping.mapping_run_id,
                    mapping.adjudication_run_id,
                    Jsonb(
                        {
                            "query_embedding": QUERY_INPUT_VERSION,
                            "rerank": RERANK_PROMPT_VERSION,
                            "mapping": MAPPING_PROMPT_VERSION,
                            "adjudication": ADJUDICATION_PROMPT_VERSION,
                        }
                    ),
                    Jsonb(policy_snapshot),
                    candidate_id,
                    MAPPER_VERSION,
                ),
            ).fetchone()
            for skill in mapping.skills:
                conn.execute(
                    """
                    insert into public.skill_mappings (
                        decision_id, segment_id, learner_id, skill_id, status, confidence,
                        first_pass_confidence, adjudicated, reason_code, status_reason,
                        evidence_span, mapper_version
                    ) values (
                        %s, %s, %s, %s, %s::public.skill_mapping_status, %s, %s, %s, %s, %s,
                        %s, %s
                    )
                    """,
                    (
                        decision_id,
                        segment_id,
                        unit.learner_id,
                        skill.skill_id,
                        skill.status,
                        skill.confidence,
                        skill.first_pass_confidence,
                        skill.adjudicated,
                        skill.reason_code,
                        skill.status_reason,
                        skill.evidence_span,
                        MAPPER_VERSION,
                    ),
                )
    return True
