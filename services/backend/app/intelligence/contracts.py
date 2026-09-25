"""Intelligence contracts shared across engines and APIs.

Mirrored in packages/contracts/src/intelligence.ts. Change both together.
"""

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

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


def normalize_reason_code(value: object) -> object:
    """Fold a model's free-form reason code to UPPER_SNAKE_CASE (e.g. "concept question").

    Casing is not a semantic error worth a repair call; anything that is not a string
    is left for strict validation to reject."""
    if not isinstance(value, str):
        return value
    code = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()[:64]
    if not code:
        return "UNSPECIFIED"
    return code if code[0].isalpha() else f"R_{code}"[:64]


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


# --- P3B: attribution + evidence (architecture §9.5, §9.6, §10.1, Appendix A.3) ---------

EvidenceActor = Literal["STUDENT", "AI", "SHARED", "UNKNOWN"]
EvidenceType = Literal[
    "EXPOSURE",
    "OBSERVATION",
    "ASSISTED_ATTEMPT",
    "INDEPENDENT_EXPLANATION",
    "INDEPENDENT_APPLICATION",
    "TRANSFER",
    "VERIFICATION",
    "EXECUTION_RESULT",
    "TEACHER_EVIDENCE",
]
# Appendix A.3: what the attribution model may propose. VERIFICATION, EXECUTION_RESULT
# and TEACHER_EVIDENCE come from graders and teachers, never from captured AI activity;
# OTHER means "no evidence".
AttributionEvidenceType = Literal[
    "EXPOSURE",
    "OBSERVATION",
    "ASSISTED_ATTEMPT",
    "INDEPENDENT_EXPLANATION",
    "INDEPENDENT_APPLICATION",
    "TRANSFER",
    "OTHER",
]
OutcomeSignal = Literal["CORRECT", "INCORRECT", "PARTIAL", "NOT_APPLICABLE"]
EvidenceSourceType = Literal["AI_ACTIVITY", "VERIFICATION", "ASSESSMENT", "TEACHER"]
AttributionStatus = Literal["ATTRIBUTED", "ABSTAINED"]

ReasonCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")]
EvidenceSpan = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


class AttributionItem(BaseModel):
    """Appendix A.3 for one accepted skill mapping, plus the outcome signal (ADR 0005)."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    actor: EvidenceActor
    confidence: float = Field(ge=0, le=1)
    student_evidence_span: EvidenceSpan | None
    ai_evidence_span: EvidenceSpan | None
    evidence_type: AttributionEvidenceType
    outcome_signal: OutcomeSignal
    reason_code: ReasonCode

    _fold_reason_code = field_validator("reason_code", mode="before")(normalize_reason_code)
    _blank_spans = field_validator("student_evidence_span", "ai_evidence_span", mode="before")(
        _blank_to_none
    )


class AttributionOutput(BaseModel):
    """SKILL_ATTRIBUTION output: exactly one item per supplied accepted skill."""

    model_config = ConfigDict(extra="forbid")

    attributions: list[AttributionItem] = Field(max_length=20)


class EvidenceEvent(BaseModel):
    """One immutable EvidenceEvent as stored (architecture §10.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    learner_id: UUID
    skill_id: UUID
    source_type: EvidenceSourceType
    source_id: UUID
    attribution_id: UUID | None
    evidence_type: EvidenceType
    actor: EvidenceActor
    outcome_signal: OutcomeSignal
    outcome: float | None = Field(ge=0, le=1)
    difficulty: float = Field(ge=0, le=1)
    independence: float = Field(ge=0, le=1)
    strength: float = Field(ge=0)
    mapping_confidence: float = Field(ge=0, le=1)
    attribution_confidence: float = Field(ge=0, le=1)
    # Set only for graded evidence (P6 verification); None for captured AI activity.
    grading_confidence: float | None = Field(ge=0, le=1)
    evidence_confidence: float = Field(ge=0, le=1)
    evidence_span: dict[str, str | None]
    model_run_ids: tuple[UUID, ...]
    excluded: bool
    occurred_at: datetime
    created_at: datetime


# --- P4: mastery ledger + AI Assistance Debt (architecture §10.2-§10.4) ------------------

MasteryState = Literal[
    "UNKNOWN", "EMERGING", "DEVELOPING", "DEMONSTRATED", "VERIFIED", "NEEDS_REVERIFICATION"
]


class SkillLedgerSummary(BaseModel):
    """One skill of GET /v1/ledger: canonical skill + course overlay + derived ledger state.

    A skill with no evidence is UNKNOWN (never weak): its mastery_mean is null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: UUID
    slug: str
    canonical_name: str
    description: str
    node_kind: Literal["SKILL", "SUBSKILL"]
    difficulty_band: int | None
    course_ids: tuple[UUID, ...]
    importance: float = Field(ge=0, le=1)
    mastery_state: MasteryState
    mastery_mean: float | None = Field(ge=0, le=1)
    alpha: float
    beta: float
    support: float = Field(ge=0)
    debt_score: float = Field(ge=0, le=100)
    debt_eligible: bool
    debt_actionable: bool
    evidence_count: int = Field(ge=0)
    performance_evidence_count: int = Field(ge=0)
    recent_delegation_count: int = Field(ge=0)
    last_evidence_at: datetime | None
    ledger_version: int | None
    computed_as_of: datetime | None


class LedgerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm_version: str
    skills: list[SkillLedgerSummary]
