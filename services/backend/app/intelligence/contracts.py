"""Intelligence contracts shared across engines and APIs.

Mirrored in packages/contracts/src/intelligence.ts. Change both together.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SegmentContext = Literal[
    "academic", "professional", "personal", "entertainment", "administrative", "unknown"
]
SegmentIntent = Literal[
    "learn",
    "understand",
    "practice",
    "solve",
    "delegate",
    "lookup",
    "create",
    "transform",
    "communicate",
    "other",
]
LearningRelevance = Literal["high", "medium", "low", "none", "uncertain"]
SegmentRoute = Literal["MAP", "METADATA_ONLY", "STOP", "UNCERTAIN"]
MappingOutcome = Literal["MAPPED", "ABSTAINED"]
SkillMappingStatus = Literal["ACCEPTED", "ABSTAINED", "REJECTED"]
SkillCandidateStatus = Literal["PENDING_REVIEW", "APPROVED", "MERGED", "REJECTED"]


class SkillCandidate(BaseModel):
    """One retrieval candidate with its per-channel scores (architecture §9.3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: UUID
    canonical_name: str
    description: str
    node_kind: Literal["SKILL", "SUBSKILL"]
    in_course: bool
    parent_ids: tuple[UUID, ...] = ()
    topic_names: tuple[str, ...] = ()
    semantic_similarity: float = Field(ge=0, le=1)
    lexical_score: float = Field(ge=0, le=1)
    course_context_prior: float = Field(ge=0, le=1)
    candidate_score: float = Field(ge=0, le=1)
    rank: int = Field(ge=1)


class RetrievalResult(BaseModel):
    """Top-N pool and the reranked top-K handed to the mapper."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    course_ids: tuple[UUID, ...]
    candidates: tuple[SkillCandidate, ...]
    reranked_ids: tuple[UUID, ...]
    rerank_fallback: bool
    query_model_run_id: UUID | None
    rerank_model_run_id: UUID | None


class NewSkillCandidate(BaseModel):
    """NEW_SKILL_CANDIDATE (Appendix A.2): a concept missing from the registry.

    Stored for review; never an active skill until canonicalization approves it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: str = Field(min_length=2, max_length=160)
    parent_candidate_id: UUID | None = None
    description: str | None = Field(default=None, max_length=600)
