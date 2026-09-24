"""Routing after qualification (architecture §9.2, §16).

    LEARNING_RELEVANT + SKILL_BEARING      -> MAP (retrieval + mapping)
    LEARNING_RELEVANT + NOT_SKILL_BEARING  -> METADATA_ONLY
    NON_LEARNING                           -> STOP
    UNCERTAIN                              -> UNCERTAIN (raw + provenance kept, no evidence)

Low confidence is uncertainty. A unit with uncaptured attachment context is
never mapped: SkillMirror does not infer unavailable image/PDF content.
"""

from dataclasses import dataclass

from app.intelligence.contracts import SegmentRoute
from app.intelligence.policy import QualificationPolicy
from app.intelligence.relevance.engine import QualifiedSegment


@dataclass(frozen=True)
class RouteDecision:
    route: SegmentRoute
    reason: str


def route_segment(
    segment: QualifiedSegment, *, context_incomplete: bool, policy: QualificationPolicy
) -> RouteDecision:
    if segment.learning_relevance == "uncertain":
        return RouteDecision("UNCERTAIN", "RELEVANCE_UNCERTAIN")
    if segment.relevance_confidence < policy.min_relevance_confidence:
        return RouteDecision("UNCERTAIN", "LOW_RELEVANCE_CONFIDENCE")
    if segment.learning_relevance not in policy.learning_relevance_levels:
        return RouteDecision("STOP", "NON_LEARNING")
    if not segment.skill_bearing:
        return RouteDecision("METADATA_ONLY", "NOT_SKILL_BEARING")
    if segment.skill_bearing_confidence < policy.min_skill_bearing_confidence:
        return RouteDecision("UNCERTAIN", "LOW_SKILL_BEARING_CONFIDENCE")
    if context_incomplete or segment.reason_code == "MISSING_ATTACHMENT_CONTEXT":
        return RouteDecision("UNCERTAIN", "CONTEXT_INCOMPLETE")
    return RouteDecision("MAP", "LEARNING_SKILL_BEARING")
