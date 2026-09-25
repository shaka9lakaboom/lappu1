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
    """One immutable EvidenceEvent (architecture §10.1) with its provenance, as the learner
    may inspect it (GET /v1/skills/{id}). No prompt, policy snapshot or model output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    learner_id: UUID
    skill_id: UUID
    source_type: EvidenceSourceType
    source_id: UUID
    # Captured-activity provenance (AI_ACTIVITY); null for verification / teacher evidence.
    attribution_id: UUID | None
    mapping_id: UUID | None
    decision_id: UUID | None
    segment_id: UUID | None
    raw_message_ids: tuple[UUID, ...]
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
    qualification_reason: str
    excluded: bool
    exclusion_reason: str | None
    excluded_at: datetime | None
    occurred_at: datetime
    created_at: datetime


# --- P4: mastery ledger + AI Assistance Debt (architecture §10.2-§10.4) ------------------

MasteryState = Literal[
    "UNKNOWN", "EMERGING", "DEVELOPING", "DEMONSTRATED", "VERIFIED", "NEEDS_REVERIFICATION"
]

# --- P5: student experience (feedback + recommendations; migration 0007) -----------------

FeedbackAction = Literal["WRONG_SKILL", "DONT_COUNT", "EVALUATION"]
FeedbackTargetType = Literal[
    "EVIDENCE_EVENT", "SKILL_MAPPING", "ACTIVITY_SEGMENT", "SKILL", "RECOMMENDATION"
]
FeedbackVerdict = Literal["AGREE", "DISAGREE", "UNCLEAR"]
# Engine 16 (§5.1): no-action / practice / verify / prerequisite / reverify.
RecommendationType = Literal["NO_ACTION", "PRACTICE", "VERIFY", "PREREQUISITE", "REVERIFY"]
RecommendationState = Literal["ACTIVE", "SUPERSEDED", "COMPLETED", "DISMISSED"]
# Learner-facing AI Assistance Debt signal. NONE = not eligible (no repeated delegation):
# the internal 0-100 score is never the headline (§10.4).
DebtBand = Literal["NONE", "LOW", "MODERATE", "HIGH"]


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
    debt_band: DebtBand
    evidence_count: int = Field(ge=0)
    performance_evidence_count: int = Field(ge=0)
    recent_delegation_count: int = Field(ge=0)
    last_evidence_at: datetime | None
    ledger_version: int | None
    computed_as_of: datetime | None


# The P5 name of a ledger row as the student experience reads it.
LedgerEntry = SkillLedgerSummary


class LedgerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm_version: str
    skills: list[SkillLedgerSummary]


# --- P6: verification (migration 0008; architecture §11, Appendix A.4, A.5) --------------

VerificationState = Literal[
    "PLANNED", "READY", "IN_PROGRESS", "SUBMITTED", "EVALUATED", "ABANDONED"
]
# Appendix A.4. code / sql stay representable, but V1 issues none (no sandbox grader).
VerificationAssessmentType = Literal["mcq", "numeric", "code", "sql", "short_response", "reasoning"]
VerificationGraderType = Literal["MCQ_EXACT", "NUMERIC_TOLERANCE", "RUBRIC_AI"]
VerificationEvaluatorType = Literal["DETERMINISTIC", "AI_RUBRIC"]
TransferDistance = Literal["near", "medium", "far"]

ChallengeText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]
ChoiceKey = Annotated[str, StringConstraints(pattern=r"^[A-F]$")]


class ChallengeChoice(BaseModel):
    """One MCQ option (additive to Appendix A.4, which carries no option list)."""

    model_config = ConfigDict(extra="forbid")

    key: ChoiceKey
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]


class RubricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)
    ]
    points: int = Field(ge=1, le=10)


class VerificationGenerationOutput(BaseModel):
    """VERIFICATION_GENERATION output (Appendix A.4 plus the MCQ `choices`). Extra fields are
    forbidden; the deterministic validator then checks it against the plan before delivery."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    difficulty: float = Field(ge=0, le=1)
    assessment_type: VerificationAssessmentType
    prompt: ChallengeText
    choices: list[ChallengeChoice] = Field(max_length=6)
    expected_answer: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None
    )
    rubric: list[RubricCriterion] = Field(max_length=10)
    prerequisites_used: list[str] = Field(max_length=10)
    transfer_distance: TransferDistance
    estimated_minutes: int = Field(ge=1, le=120)

    _blank_answer = field_validator("expected_answer", mode="before")(_blank_to_none)


class CriterionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ]
    met: bool
    evidence: Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)]


class VerificationEvaluation(BaseModel):
    """VERIFICATION_EVALUATION output (Appendix A.5) of the rubric grader. Extra fields are
    forbidden; the learner response it judges is untrusted data."""

    # `pass` is a Python keyword: the field keeps its contract name through the alias, both in
    # the provider schema and in the validated output the gateway caches.
    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    score: float = Field(ge=0, le=1)
    passed: bool = Field(alias="pass")
    criterion_results: list[CriterionResult] = Field(max_length=10)
    confidence: float = Field(ge=0, le=1)
    feedback: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]
    needs_review: bool
