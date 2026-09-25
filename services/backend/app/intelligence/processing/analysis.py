"""Analysis of one processing unit, in either execution mode (ADR 0004).

Both modes run the same functional stages - segmentation, relevance, intent,
skill-bearing, retrieval, top-20 -> top-8 rerank, mapping, confidence gate,
second-pass adjudication, abstention - and produce the same persisted
provenance. They differ only in how many generation requests that takes:

combined (default)  local retrieval over the unit -> ONE TURN_ANALYSIS call
                    -> gate -> at most ONE adjudication call for the whole turn
staged              qualification call -> per MAP segment: retrieval, rerank
                    call, mapping call, adjudication call if in the band

Nothing is persisted here; the pipeline writes the result in one transaction.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from psycopg_pool import ConnectionPool

from app.intelligence.contracts import RetrievalResult
from app.intelligence.mapping.engine import (
    TURN_ADJUDICATION_PROMPT_VERSION,
    AdjudicationItem,
    TurnAdjudication,
    adjudicate_turn,
    apply_verdicts,
    gate_proposals,
    map_segment,
    settle_mapping,
)
from app.intelligence.policy import IntelligencePolicy
from app.intelligence.processing.persist import SegmentAnalysis
from app.intelligence.processing.turn_analysis import MAPPER_VERSION as TURN_MAPPER_VERSION
from app.intelligence.processing.turn_analysis import PROMPT_VERSION as TURN_PROMPT_VERSION
from app.intelligence.processing.turn_analysis import analyze_turn
from app.intelligence.relevance.engine import PROMPT_VERSION as QUALIFICATION_PROMPT_VERSION
from app.intelligence.relevance.engine import ProcessingUnitText, qualify_unit, unit_as_text
from app.intelligence.relevance.routing import route_segment
from app.intelligence.retrieval.engine import (
    QUERY_INPUT_VERSION,
    CandidatePool,
    retrieve_candidates,
    retrieve_pool,
)
from app.model_gateway import ModelGateway, RunContext

AnalysisMode = Literal["combined", "staged"]

TURN_PROMPT_VERSIONS = {
    "query_embedding": QUERY_INPUT_VERSION,
    "turn_analysis": TURN_PROMPT_VERSION,
    "adjudication": TURN_ADJUDICATION_PROMPT_VERSION,
}


@dataclass(frozen=True)
class UnitAnalysis:
    analyses: list[SegmentAnalysis]
    qualification_run_id: UUID | None
    qualification_prompt_version: str


def analyze_unit(
    pool: ConnectionPool,
    gateway: ModelGateway,
    unit: ProcessingUnitText,
    *,
    course_ids: list[UUID],
    policy: IntelligencePolicy,
    context: RunContext,
    mode: AnalysisMode = "combined",
) -> UnitAnalysis:
    if mode == "staged":
        return analyze_unit_staged(
            pool, gateway, unit, course_ids=course_ids, policy=policy, context=context
        )
    return analyze_unit_combined(
        pool, gateway, unit, course_ids=course_ids, policy=policy, context=context
    )


def _abstained(unit: ProcessingUnitText, reason: str | None) -> SegmentAnalysis:
    return SegmentAnalysis(
        text=unit_as_text(unit),
        segment=None,
        route="UNCERTAIN",
        route_reason=reason or "MODEL_OUTPUT_INVALID",
        reason_code=reason or "MODEL_OUTPUT_INVALID",
    )


def analyze_unit_combined(
    pool: ConnectionPool,
    gateway: ModelGateway,
    unit: ProcessingUnitText,
    *,
    course_ids: list[UUID],
    policy: IntelligencePolicy,
    context: RunContext,
) -> UnitAnalysis:
    # 1. Local retrieval first (query embedding + SQL, no generation call). A unit with
    #    uncaptured attachment context is never mapped, so it does not retrieve at all.
    candidate_pool: CandidatePool | None = None
    query = "\n\n".join(t for t in (unit.user_text, unit.assistant_text) if t)
    if not unit.context_incomplete and query.strip():
        with pool.connection() as conn:
            candidate_pool = retrieve_pool(
                conn,
                gateway,
                query_text=query,
                course_ids=course_ids,
                policy=policy.retrieval,
                context=context,
            )
    candidates = candidate_pool.candidates if candidate_pool else []

    # 2. One structured call: segmentation + qualification + top-8 + mapping proposals.
    turn = analyze_turn(gateway, unit, candidates, policy, context)
    run_id = turn.run.id if turn.run else None
    if turn.segments is None:
        return UnitAnalysis([_abstained(unit, turn.abstain_reason)], run_id, TURN_PROMPT_VERSION)

    # 3. Route, then gate every MAP segment's proposals; collect the band of the turn.
    by_id = {c.skill_id: c for c in candidates}
    routes = [
        route_segment(
            s.qualified, context_incomplete=unit.context_incomplete, policy=policy.qualification
        )
        for s in turn.segments
    ]
    gated: dict[int, tuple] = {}
    items: list[AdjudicationItem] = []
    for index, (seg, routed) in enumerate(zip(turn.segments, routes, strict=True)):
        if routed.route != "MAP":
            continue
        decided, band = gate_proposals(seg.mappings, policy.mapping)
        gated[index] = (decided, band)
        for proposal in band:
            items.append(
                AdjudicationItem(
                    item_id=f"a{len(items) + 1}",
                    segment_index=index,
                    segment_text=seg.qualified.text,
                    proposal=proposal,
                    candidate=by_id[UUID(proposal.skill_id)],
                )
            )

    # 4. Conditional second pass: at most ONE adjudication call for the whole turn.
    adjudication_run_id: UUID | None = None
    verdicts: dict[str, TurnAdjudication] = {}
    if items:
        adjudication_run_id, verdicts = adjudicate_turn(
            gateway, items=items, course_context=unit.course_context, context=context
        )

    analyses: list[SegmentAnalysis] = []
    for index, (seg, routed) in enumerate(zip(turn.segments, routes, strict=True)):
        retrieval = mapping = None
        if index in gated:
            decided, band = gated[index]
            if band:
                segment_verdicts = {
                    item.proposal.skill_id: verdicts[item.item_id]
                    for item in items
                    if item.segment_index == index and item.item_id in verdicts
                }
                apply_verdicts(band, segment_verdicts, decided, policy.mapping)
            top = [by_id[i] for i in seg.top_candidate_ids]
            mapping = settle_mapping(
                proposals=seg.mappings,
                decided=decided,
                candidates=top,
                new_skill=seg.new_skill_candidate,
                mapping_run_id=run_id,
                adjudication_run_id=adjudication_run_id if band else None,
                mapper_version=TURN_MAPPER_VERSION,
            )
            retrieval = RetrievalResult(
                course_ids=tuple(course_ids),
                candidates=tuple(candidates),
                reranked_ids=tuple(seg.top_candidate_ids),
                rerank_fallback=False,
                query_model_run_id=candidate_pool.query_model_run_id if candidate_pool else None,
                rerank_model_run_id=run_id,
            )
        analyses.append(
            SegmentAnalysis(
                text=seg.qualified.text,
                segment=seg.qualified,
                route=routed.route,
                route_reason=routed.reason,
                reason_code=seg.qualified.reason_code,
                retrieval=retrieval,
                mapping=mapping,
                prompt_versions=TURN_PROMPT_VERSIONS,
            )
        )
    return UnitAnalysis(analyses, run_id, TURN_PROMPT_VERSION)


def analyze_unit_staged(
    pool: ConnectionPool,
    gateway: ModelGateway,
    unit: ProcessingUnitText,
    *,
    course_ids: list[UUID],
    policy: IntelligencePolicy,
    context: RunContext,
) -> UnitAnalysis:
    qualification = qualify_unit(gateway, unit, policy.qualification, context)
    run_id = qualification.run.id if qualification.run else None
    if qualification.segments is None:
        return UnitAnalysis(
            [_abstained(unit, qualification.abstain_reason)], run_id, QUALIFICATION_PROMPT_VERSION
        )
    analyses: list[SegmentAnalysis] = []
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
                    course_context=unit.course_context,
                    policy=policy.retrieval,
                    context=context,
                )
            by_id = {c.skill_id: c for c in retrieval.candidates}
            mapping = map_segment(
                gateway,
                segment_text=segment.text,
                course_context=unit.course_context,
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
    return UnitAnalysis(analyses, run_id, QUALIFICATION_PROMPT_VERSION)
