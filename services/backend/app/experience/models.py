"""Student-experience API contracts (architecture §12.1, §13; ADR 0006): skill detail with its
"Why?", the enriched activity feed, feedback and recommendations.

Mirrored in packages/contracts/src/experience.ts. Change both together.

Nothing here carries a model prompt, a policy snapshot, a provider detail or raw model output.
Captured text (previews, spans) is untrusted data: clients render it as plain text only.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.courses.models import AssessmentType, SkillNodeKind, SkillStatus
from app.ingestion.models import MessageRole, SourceProvider
from app.intelligence.contracts import (
    AttributionStatus,
    DebtBand,
    EvidenceActor,
    EvidenceEvent,
    EvidenceType,
    FeedbackAction,
    FeedbackTargetType,
    FeedbackVerdict,
    LearningRelevance,
    LedgerEntry,
    MappingOutcome,
    MasteryState,
    OutcomeSignal,
    RecommendationState,
    RecommendationType,
    SegmentContext,
    SegmentIntent,
    SegmentRoute,
    SkillMappingStatus,
)

# processing_jobs.state (migration 0002).
JobState = Literal["PENDING", "PROCESSING", "COMPLETED", "RETRY_WAIT", "FAILED"]
FactorLevel = Literal["LOW", "MODERATE", "HIGH"]
DebtFactorCode = Literal[
    "DELEGATION_PRESSURE", "EVIDENCE_GAP", "IMPORTANCE", "CONFIDENCE", "VERIFICATION"
]
MasteryGateCode = Literal[
    "ENOUGH_EVIDENCE", "STRONG_RESULTS", "SUSTAINED_EVIDENCE", "INDEPENDENT_APPLICATION"
]

# Which targets each action may concern (mirrors the feedback_action_target check, 0007).
FEEDBACK_TARGETS: dict[str, tuple[str, ...]] = {
    "WRONG_SKILL": ("EVIDENCE_EVENT", "SKILL_MAPPING"),
    "DONT_COUNT": ("EVIDENCE_EVENT", "SKILL_MAPPING", "ACTIVITY_SEGMENT"),
    "EVALUATION": (
        "EVIDENCE_EVENT",
        "SKILL_MAPPING",
        "ACTIVITY_SEGMENT",
        "SKILL",
        "RECOMMENDATION",
    ),
}
FEEDBACK_NOTE_MAX_CHARS = 2000


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Recommendations (Engine 16) ------------------------------------------------------------


class Recommendation(_Out):
    """The current (or a past) next action for one skill."""

    id: UUID
    skill_id: UUID
    canonical_name: str
    type: RecommendationType
    priority: int = Field(ge=0, le=100)
    reason_code: str
    state: RecommendationState
    mastery_state: MasteryState
    # PREREQUISITE: the prerequisite to strengthen first.
    related_skill_id: UUID | None
    related_skill_name: str | None
    debt_band: DebtBand
    # True when actionable debt waits behind higher-priority verifications (burden cap).
    verify_deferred: bool
    course_ids: tuple[UUID, ...]
    created_at: datetime
    updated_at: datetime


class RecommendationsResponse(_Out):
    algorithm_version: str
    recommendations: list[Recommendation]


# --- Feedback (correction loop) ---------------------------------------------------------------


class FeedbackRequest(BaseModel):
    """POST /v1/feedback. Send an Idempotency-Key header: a retry returns the original result."""

    model_config = ConfigDict(extra="forbid")

    action: FeedbackAction
    target_type: FeedbackTargetType
    target_id: UUID
    verdict: FeedbackVerdict | None = None
    note: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, min_length=1, max_length=FEEDBACK_NOTE_MAX_CHARS
            ),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def _consistent(self) -> "FeedbackRequest":
        if self.target_type not in FEEDBACK_TARGETS[self.action]:
            raise ValueError(f"{self.action} cannot target {self.target_type}")
        if self.action != "EVALUATION" and self.verdict is not None:
            raise ValueError("verdict is only for EVALUATION")
        if self.action == "EVALUATION" and self.verdict is None and self.note is None:
            raise ValueError("EVALUATION needs a verdict or a note")
        return self


class FeedbackSummary(_Out):
    id: UUID
    action: FeedbackAction
    target_type: FeedbackTargetType
    target_id: UUID
    skill_id: UUID | None
    mapping_id: UUID | None
    segment_id: UUID | None
    verdict: FeedbackVerdict | None
    note: str | None
    # The evidence this correction excluded (one-way) and the skills it recomputed.
    excluded_evidence_ids: tuple[UUID, ...]
    recomputed_skill_ids: tuple[UUID, ...]
    created_at: datetime


class FeedbackResponse(_Out):
    correlation_id: str
    # False when this is a replay of the same Idempotency-Key or an existing correction.
    created: bool
    feedback: FeedbackSummary
    # The recomputed ledger rows and the refreshed recommendations of the affected skills.
    ledger: list[LedgerEntry]
    recommendations: list[Recommendation]


# --- Skill detail ("Why?") ---------------------------------------------------------------------


class SkillInfo(_Out):
    skill_id: UUID
    slug: str
    canonical_name: str
    description: str
    node_kind: SkillNodeKind
    status: SkillStatus
    difficulty_band: int | None
    assessment_types: list[AssessmentType]
    aliases: list[str]


class SkillCourseContext(_Out):
    course_id: UUID
    name: str
    importance: float = Field(ge=0, le=1)
    topic_id: UUID | None
    topic_name: str | None


class SkillPrerequisite(_Out):
    skill_id: UUID
    canonical_name: str
    mastery_state: MasteryState


class MasteryGate(_Out):
    code: MasteryGateCode
    met: bool
    current: float | None
    required: float | None


class MasteryExplanation(_Out):
    state: MasteryState
    # Stable code for the plain-language reason (e.g. NO_EVIDENCE, INDEPENDENT_EVIDENCE_SUPPORTS).
    explanation_code: str
    support: float = Field(ge=0)
    # Null while UNKNOWN: unknown is not weak.
    mastery_mean: float | None
    evidence_count: int = Field(ge=0)
    performance_evidence_count: int = Field(ge=0)
    excluded_evidence_count: int = Field(ge=0)
    has_independent_application: bool
    gates: list[MasteryGate]
    computed_as_of: datetime | None
    algorithm_version: str


class DebtFactor(_Out):
    code: DebtFactorCode
    level: FactorLevel
    value: float


class DebtExplanation(_Out):
    eligible: bool
    # NONE unless eligible; the qualitative signal the UI leads with.
    band: DebtBand
    actionable: bool
    # NO_DELEGATION | INSUFFICIENT_DELEGATION | ELIGIBLE
    eligibility_code: str
    recent_delegation_count: int = Field(ge=0)
    min_recent_delegations: int = Field(ge=0)
    # UNVERIFIED | RECENTLY_PASSED | FAILED (eligible only).
    verification: str | None
    factors: list[DebtFactor]
    # Internal 0-100 metric: an explanation driver, never the headline (§10.4).
    score: float = Field(ge=0, le=100)


class EvidenceSource(_Out):
    """The captured activity behind an evidence event (plain text preview only)."""

    conversation_id: UUID | None
    raw_message_ids: tuple[UUID, ...]
    captured_at: datetime | None
    learner_message_preview: str | None


class EvidenceTimelineItem(_Out):
    event: EvidenceEvent
    # The attribution's rationale code (why the actor/type was judged), if any.
    reason_code: str | None
    counts_toward_mastery: bool
    counts_toward_debt: bool
    # strength x recency at the ledger's computed_as_of; 0 when it does not count.
    current_weight: float = Field(ge=0)
    source: EvidenceSource | None
    # The learner's correction that excluded it, if any.
    correction: FeedbackSummary | None


class SkillDetailResponse(_Out):
    skill: SkillInfo
    courses: list[SkillCourseContext]
    prerequisites: list[SkillPrerequisite]
    ledger: LedgerEntry
    mastery: MasteryExplanation
    debt: DebtExplanation
    evidence: list[EvidenceTimelineItem]
    recommendation: Recommendation | None
    feedback: list[FeedbackSummary]


# --- Activity (enriched feed) ----------------------------------------------------------------


class ActivityMappedSkill(_Out):
    mapping_id: UUID
    skill_id: UUID
    canonical_name: str
    status: SkillMappingStatus
    confidence: float = Field(ge=0, le=1)
    evidence_span: str | None
    attribution_status: AttributionStatus | None
    actor: EvidenceActor | None
    attribution_confidence: float | None
    # EVIDENCE_CREATED or the abstention reason of the evidence qualification.
    evidence_decision: str | None
    evidence_id: UUID | None
    evidence_type: EvidenceType | None
    outcome_signal: OutcomeSignal | None
    excluded: bool
    exclusion_reason: str | None
    correction: FeedbackSummary | None


class ActivitySegment(_Out):
    segment_id: UUID
    segment_index: int
    segment_count: int
    route: SegmentRoute
    route_reason: str
    learning_relevance: LearningRelevance | None
    intent: SegmentIntent | None
    context: SegmentContext | None
    context_incomplete: bool
    mapping_outcome: MappingOutcome | None
    abstain_reason: str | None
    mappings: list[ActivityMappedSkill]
    evidence_count: int = Field(ge=0)
    excluded_evidence_count: int = Field(ge=0)
    # The learner's DONT_COUNT of this whole task unit, if any.
    correction: FeedbackSummary | None


class ActivityRow(_Out):
    """One captured message, with what SkillMirror derived from its turn."""

    id: UUID
    conversation_id: UUID
    external_conversation_id: str | None
    source_provider: SourceProvider
    role: MessageRole
    message_index: int | None
    revision_index: int
    captured_at: datetime
    received_at: datetime
    context_incomplete: bool
    preview: str
    content_chars: int
    processing_state: JobState | None
    processing_attempts: int | None
    processing_outcome: str | None
    # The message the turn was analysed from (its anchor); null when not analysed yet.
    analyzed_in: UUID | None
    # Only on the anchor message of an analysed turn.
    segments: list[ActivitySegment]


class ActivityResponse(_Out):
    items: list[ActivityRow]
    # Pass as `before` for the next (older) page; null on the last page.
    next_before: datetime | None
