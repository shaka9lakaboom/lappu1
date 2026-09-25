"""The P3B + P4 stage of PROCESS_RAW_MESSAGE (ADR 0005).

    committed P3A analysis -> per MAPPED segment: ONE attribution call
      -> deterministic evidence qualification -> attributions + evidence (one tx)
    -> deterministic ledger recompute (mastery + AI Assistance Debt)

Only ACCEPTED mappings of MAP-routed segments reach attribution. The model call
runs outside any transaction. A transient gateway error (429/503, spent budget)
propagates, so the worker defers the job without spending an attempt; the P3A rows
are already committed, and the retried job resumes at the pending segment
without repeating the P3A calls. The ledger is recomputed after evidence commits:
it is a rebuildable cache, so a crash in between is repaired by the replay. The
recommendation queue (P5, Engine 16) is refreshed from the new ledger in the same way.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg_pool import ConnectionPool

from app.intelligence.attribution.engine import AttributionRequest, attribute_segment
from app.intelligence.evidence.engine import Qualification, qualify_attribution, reused_ai_text
from app.intelligence.evidence.persist import (
    pending_segments,
    persist_attribution,
    unit_evidence_skills,
    unit_is_mapped,
)
from app.intelligence.mastery.ledger import recompute_ledger
from app.intelligence.policy import IntelligencePolicy
from app.intelligence.processing.turn import ProcessingUnit, earlier_assistant_texts
from app.intelligence.recommendations.service import refresh_recommendations
from app.intelligence.relevance.engine import ProcessingUnitText
from app.model_gateway import ModelGateway, RunContext

# How many earlier assistant messages of the conversation the copy guard searches.
COPY_GUARD_HISTORY_MESSAGES = 50


@dataclass(frozen=True)
class EvidenceStageResult:
    attributed_segments: int
    evidence_skills: int
    mapped: bool

    @property
    def outcome(self) -> str | None:
        """Job outcome for a mapped unit; None when the unit was not mapped."""
        if not self.mapped:
            return None
        return "EVIDENCE_RECORDED" if self.evidence_skills else "MAPPED_NO_EVIDENCE"


def run_evidence_stage(
    pool: ConnectionPool,
    gateway: ModelGateway,
    *,
    unit: ProcessingUnit,
    text: ProcessingUnitText,
    policy: IntelligencePolicy,
    context: RunContext,
    processing_job_id: UUID | None,
    as_of: datetime | None = None,
) -> EvidenceStageResult:
    with pool.connection() as conn:
        pending = pending_segments(conn, unit.anchor.id)
        # The copy guard's history: the conversation's earlier assistant messages (bounded),
        # a superset of the recent context window (ADR 0008 H8).
        prior_assistant = (
            earlier_assistant_texts(conn, unit.anchor, COPY_GUARD_HISTORY_MESSAGES)
            if pending
            else []
        )

    snapshot = {
        "attribution": policy.attribution.model_dump(mode="json"),
        "evidence": policy.evidence.model_dump(mode="json"),
    }
    reused = reused_ai_text(
        text.user_text, prior_assistant, policy.attribution.copy_guard_min_chars
    )
    attributed = 0
    for segment in pending:
        request = AttributionRequest(
            segment_text=segment.text,
            segment_index=segment.segment_index,
            segment_count=segment.segment_count,
            learner_text=text.user_text,
            assistant_text=text.assistant_text,
            recent_context=text.recent_context,
            course_context=text.course_context,
            skills=segment.skills,
            reused_ai_text=reused,
            prior_assistant_texts=tuple(prior_assistant),
            copy_guard_min_chars=policy.attribution.copy_guard_min_chars,
        )
        result = attribute_segment(gateway, request, context)
        qualifications: dict[str, Qualification] = {}
        if result.items is not None:
            for skill in segment.skills:
                key = str(skill.skill_id)
                qualifications[key] = qualify_attribution(
                    result.items[key],
                    mapping_confidence=skill.mapping_confidence,
                    difficulty_band=skill.difficulty_band,
                    learner_text=text.user_text,
                    prior_assistant_texts=prior_assistant,
                    attribution_policy=policy.attribution,
                    evidence_policy=policy.evidence,
                )
        with pool.connection() as conn:
            if persist_attribution(
                conn,
                segment,
                result,
                qualifications,
                policy_snapshot=snapshot,
                processing_job_id=processing_job_id,
            ):
                attributed += 1

    with pool.connection() as conn:
        skills = unit_evidence_skills(conn, unit.anchor.id)
        if skills:
            recompute_ledger(
                conn, unit.learner_id, skills, policy=policy, as_of=as_of or datetime.now(UTC)
            )
            refresh_recommendations(conn, unit.learner_id, policy=policy)
        mapped = unit_is_mapped(conn, unit.anchor.id)
    return EvidenceStageResult(attributed, len(skills), mapped)
