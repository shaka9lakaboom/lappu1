"""Intelligence policy (architecture §5.2 Rules & Policy Engine, Appendix B).

Every tunable weight and threshold is read from `public.policy_config`. This
module defines and validates the shape; it deliberately carries NO default
values, so a number can only come from the database (seeded by migration
0003) or from an explicit test fixture.
"""

from typing import TYPE_CHECKING, Any, Literal

from psycopg import Connection
from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

POLICY_KEYS = (
    "retrieval",
    "mapping",
    "qualification",
    "processing_unit",
    "skill_graph",
    "attribution",  # migration 0005
    "evidence",  # migration 0005
    "mastery",  # migration 0006
    "debt",  # migration 0006
    "recommendations",  # migration 0007
    "verification",  # migration 0008
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrievalWeights(_Strict):
    semantic: float = Field(ge=0, le=1)
    lexical: float = Field(ge=0, le=1)
    course_prior: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _sum_to_one(self) -> "RetrievalWeights":
        total = self.semantic + self.lexical + self.course_prior
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"retrieval weights must sum to 1.0 (got {total})")
        return self


class RetrievalPolicy(_Strict):
    weights: RetrievalWeights
    pool_size: int = Field(ge=1, le=100)
    rerank_size: int = Field(ge=1, le=50)
    channel_limit: int = Field(ge=1, le=500)
    query_max_chars: int = Field(ge=100, le=20000)

    @model_validator(mode="after")
    def _sizes(self) -> "RetrievalPolicy":
        if self.rerank_size > self.pool_size:
            raise ValueError("rerank_size must not exceed pool_size")
        return self


class MappingPolicy(_Strict):
    accept_threshold: float = Field(gt=0, le=1)
    adjudicate_min: float = Field(gt=0, le=1)
    max_skills_per_segment: int = Field(ge=1, le=20)

    @model_validator(mode="after")
    def _ordered(self) -> "MappingPolicy":
        if not self.adjudicate_min < self.accept_threshold:
            raise ValueError("adjudicate_min must be below accept_threshold")
        return self


RelevanceLevel = Literal["high", "medium", "low", "none", "uncertain"]


class QualificationPolicy(_Strict):
    learning_relevance_levels: tuple[RelevanceLevel, ...] = Field(min_length=1)
    min_relevance_confidence: float = Field(ge=0, le=1)
    min_skill_bearing_confidence: float = Field(ge=0, le=1)
    max_segments: int = Field(ge=1, le=20)


class ProcessingUnitPolicy(_Strict):
    recent_context_messages: int = Field(ge=0, le=20)
    recent_context_max_chars: int = Field(ge=0, le=50000)
    unit_max_chars: int = Field(ge=500, le=100000)
    pairing_window_seconds: int = Field(ge=0, le=3600)
    pairing_retry_seconds: int = Field(ge=1, le=600)


class SkillGraphPolicy(_Strict):
    target_min_skills: int = Field(ge=1, le=500)
    target_max_skills: int = Field(ge=1, le=500)
    hard_min_skills: int = Field(ge=1, le=500)
    hard_max_skills: int = Field(ge=1, le=500)
    default_importance: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _bounds(self) -> "SkillGraphPolicy":
        if not (
            self.hard_min_skills
            <= self.target_min_skills
            <= self.target_max_skills
            <= self.hard_max_skills
        ):
            raise ValueError("skill graph bounds must satisfy hard_min <= min <= max <= hard_max")
        return self


EVIDENCE_TYPES = (
    "EXPOSURE",
    "OBSERVATION",
    "ASSISTED_ATTEMPT",
    "INDEPENDENT_EXPLANATION",
    "INDEPENDENT_APPLICATION",
    "TRANSFER",
    "VERIFICATION",
    "EXECUTION_RESULT",
    "TEACHER_EVIDENCE",
)
# Exposure is not mastery (§2.2): these types never carry strength. The evidence engine
# enforces it whatever the policy says, and the policy may not even claim otherwise.
ZERO_STRENGTH_TYPES = ("EXPOSURE", "OBSERVATION")

EvidenceTypeName = Literal[
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


class AttributionPolicy(_Strict):
    min_confidence: float = Field(gt=0, le=1)
    copy_guard_min_chars: int = Field(ge=8, le=2000)


class OutcomeValues(_Strict):
    CORRECT: float
    PARTIAL: float
    INCORRECT: float

    @model_validator(mode="after")
    def _definitional(self) -> "OutcomeValues":
        if self.CORRECT != 1.0 or self.INCORRECT != 0.0 or not 0 < self.PARTIAL < 1:
            raise ValueError("outcome values must be CORRECT 1.0, INCORRECT 0.0, 0 < PARTIAL < 1")
        return self


class DifficultyPolicy(_Strict):
    default: float = Field(ge=0, le=1)
    multiplier_base: float = Field(gt=0, le=5)
    multiplier_slope: float = Field(ge=0, le=5)


class EvidencePolicy(_Strict):
    base_weights: dict[EvidenceTypeName, float]
    independence: dict[EvidenceTypeName, float]
    outcome_values: OutcomeValues
    difficulty: DifficultyPolicy

    @model_validator(mode="after")
    def _complete_and_guarded(self) -> "EvidencePolicy":
        for name, table in (
            ("base_weights", self.base_weights),
            ("independence", self.independence),
        ):
            missing = [t for t in EVIDENCE_TYPES if t not in table]
            if missing:
                raise ValueError(f"evidence.{name} is missing {missing}")
            if any(v < 0 for v in table.values()):
                raise ValueError(f"evidence.{name} values must be >= 0")
            if any(table[t] != 0 for t in ZERO_STRENGTH_TYPES):
                raise ValueError(f"evidence.{name} must be 0 for {ZERO_STRENGTH_TYPES}")
        if any(v > 1 for v in self.independence.values()):
            raise ValueError("evidence.independence values must be <= 1")
        return self


class MasteryPolicy(_Strict):
    prior_alpha: float = Field(gt=0, le=100)
    prior_beta: float = Field(gt=0, le=100)
    recency_half_life_days: float = Field(gt=0, le=3650)
    unknown_min_support: float = Field(gt=0)
    emerging_below_mean: float = Field(gt=0, lt=1)
    demonstrated_min_mean: float = Field(gt=0, lt=1)
    demonstrated_min_support: float = Field(gt=0)
    application_types: tuple[EvidenceTypeName, ...] = Field(min_length=1)
    application_min_outcome: float = Field(gt=0, le=1)
    verified_min_mean: float = Field(gt=0, le=1)
    verified_min_support: float = Field(gt=0)
    verification_max_age_days: float = Field(gt=0, le=3650)

    @model_validator(mode="after")
    def _ordered(self) -> "MasteryPolicy":
        if not self.emerging_below_mean <= self.demonstrated_min_mean <= self.verified_min_mean:
            raise ValueError("mastery means must satisfy emerging <= demonstrated <= verified")
        if not (
            self.unknown_min_support <= self.demonstrated_min_support <= self.verified_min_support
        ):
            raise ValueError("mastery supports must satisfy unknown <= demonstrated <= verified")
        if set(self.application_types) & set(ZERO_STRENGTH_TYPES):
            raise ValueError("exposure/observation can never count as an application")
        return self


class VerificationFactors(_Strict):
    recently_passed: float = Field(ge=0, le=1)
    unverified: float = Field(ge=0, le=1)
    failed_or_due: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> "VerificationFactors":
        if not self.recently_passed <= self.unverified <= self.failed_or_due:
            raise ValueError("verification factors must satisfy passed <= unverified <= failed")
        return self


class DebtPolicy(_Strict):
    # At least 2: a single AI interaction can never create debt (§10.4, Appendix B).
    min_recent_delegations: int = Field(ge=2, le=100)
    recent_window_days: float = Field(gt=0, le=365)
    delegation_half_life_days: float = Field(gt=0, le=365)
    tau: float = Field(gt=0, le=100)
    actor_weights: dict[Literal["AI", "SHARED"], float]
    delegation_evidence_types: tuple[EvidenceTypeName, ...] = Field(min_length=1)
    min_evidence_confidence: float = Field(gt=0, le=1)
    learning_relevance_levels: tuple[RelevanceLevel, ...] = Field(min_length=1)
    trivial_reason_codes: tuple[str, ...]
    evidence_gap_support_target: float = Field(gt=0)
    verification_factor: VerificationFactors
    verification_recent_days: float = Field(gt=0, le=3650)
    actionable_min_score: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def _weights(self) -> "DebtPolicy":
        if not self.actor_weights or any(not 0 < v <= 1 for v in self.actor_weights.values()):
            raise ValueError("debt.actor_weights must be in (0, 1]")
        if "EXPOSURE" in self.delegation_evidence_types:
            raise ValueError("receiving an explanation (EXPOSURE) is not delegation")
        return self


class DebtBands(_Strict):
    """Learner-facing qualitative debt bands; the 0-100 score is never the headline (§10.4)."""

    moderate_min: float = Field(gt=0, le=100)
    high_min: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def _ordered(self) -> "DebtBands":
        if not self.moderate_min < self.high_min:
            raise ValueError("debt bands must satisfy moderate_min < high_min")
        return self


class RecommendationPolicy(_Strict):
    # Appendix B: at most 2 verification recommendations per learner (learner burden).
    max_active_verify: int = Field(ge=0, le=20)
    # Mastery states of a prerequisite that make a PREREQUISITE recommendation. UNKNOWN can
    # never be one: unknown is not weak.
    prerequisite_gap_states: tuple[Literal["EMERGING", "DEVELOPING"], ...] = Field(min_length=1)
    debt_bands: DebtBands


# --- P6 verification (migration 0008; engineering defaults in ADR 0007) -----------------

VerificationAssessmentTypeName = Literal[
    "mcq", "numeric", "code", "sql", "short_response", "reasoning"
]
# Executable challenges need the deterministic sandbox (post-V1). An LLM rubric is not an
# equivalent grader, so the policy may not even claim them as supported.
SANDBOX_ASSESSMENT_TYPES = ("code", "sql")
OutcomeSignalName = Literal["CORRECT", "INCORRECT", "PARTIAL"]


class VerificationPlannerPolicy(_Strict):
    # Appendix B: at most 2 unsolicited verification recommendations per learner per day.
    max_daily_unsolicited: int = Field(ge=0, le=20)
    # §11.1: a recently passed verification creates a cooldown.
    cooldown_after_pass_days: float = Field(ge=0, le=365)
    cooldown_after_fail_days: float = Field(ge=0, le=365)
    # An abandoned check is not failure evidence, and is not re-issued at once either.
    cooldown_after_abandon_days: float = Field(ge=0, le=365)
    retry_after_generation_failure_hours: float = Field(ge=0, le=24 * 365)
    # A started check left untouched this long is abandoned (never failure evidence).
    abandon_in_progress_after_days: float = Field(gt=0, le=365)


class VerificationDifficultyPolicy(_Strict):
    """The planned band around the skill's difficulty (Appendix B.1: only valid in the band)."""

    band_half_width: float = Field(ge=0, le=0.5)
    min: float = Field(ge=0, le=1)
    max: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> "VerificationDifficultyPolicy":
        if not self.min <= self.max:
            raise ValueError("verification difficulty must satisfy min <= max")
        return self


class VerificationGenerationPolicy(_Strict):
    # Initial generation + regenerations (§11.3: "regenerate (max 2 attempts)").
    max_attempts: int = Field(ge=1, le=5)
    supported_assessment_types: tuple[VerificationAssessmentTypeName, ...] = Field(min_length=1)
    # Bounded history of earlier prompts (learner + skill) shown to the generator.
    history_limit: int = Field(ge=0, le=20)
    # Token-set similarity above which a challenge is a material duplicate.
    duplicate_max_similarity: float = Field(gt=0, le=1)
    min_prompt_chars: int = Field(ge=20, le=4000)
    max_prompt_chars: int = Field(ge=20, le=4000)
    min_estimated_minutes: int = Field(ge=1, le=120)
    max_estimated_minutes: int = Field(ge=1, le=120)

    @model_validator(mode="after")
    def _supported(self) -> "VerificationGenerationPolicy":
        sandbox = set(self.supported_assessment_types) & set(SANDBOX_ASSESSMENT_TYPES)
        if sandbox:
            raise ValueError(f"{sorted(sandbox)} need the sandbox grader, which V1 does not have")
        if not self.min_prompt_chars <= self.max_prompt_chars:
            raise ValueError("min_prompt_chars must not exceed max_prompt_chars")
        if not self.min_estimated_minutes <= self.max_estimated_minutes:
            raise ValueError("min_estimated_minutes must not exceed max_estimated_minutes")
        return self


class VerificationGradingPolicy(_Strict):
    numeric_relative_tolerance: float = Field(ge=0, le=0.5)
    numeric_absolute_tolerance: float = Field(ge=0, le=1)
    max_response_chars: int = Field(ge=100, le=20000)
    short_response_max_chars: int = Field(ge=20, le=20000)
    numeric_max_chars: int = Field(ge=8, le=200)
    # Rubric grading: pass from this fraction of the rubric points.
    rubric_pass_min_score: float = Field(gt=0, le=1)
    # Below this evaluator confidence the grade is not trusted: no result, no evidence.
    min_ai_grading_confidence: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _limits(self) -> "VerificationGradingPolicy":
        if self.short_response_max_chars > self.max_response_chars:
            raise ValueError("short_response_max_chars must not exceed max_response_chars")
        return self


class ReverificationPolicy(_Strict):
    """When newer evidence materially contradicts a verification (§10.3 NEEDS_REVERIFICATION).

    Conservative on purpose: a single isolated later failure is never enough (§16 "One
    isolated failure ... does not erase strong history"), so the minimum is 2."""

    min_contradicting_failures: int = Field(ge=2, le=20)
    contradicting_evidence_types: tuple[EvidenceTypeName, ...] = Field(min_length=1)
    contradicting_outcome_signals: tuple[OutcomeSignalName, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _only_failures(self) -> "ReverificationPolicy":
        if "CORRECT" in self.contradicting_outcome_signals:
            raise ValueError("a correct result never contradicts a verification")
        if set(self.contradicting_evidence_types) & set(ZERO_STRENGTH_TYPES):
            raise ValueError("exposure/observation carry no performance: they cannot contradict")
        return self


class VerificationPolicy(_Strict):
    planner: VerificationPlannerPolicy
    difficulty: VerificationDifficultyPolicy
    generation: VerificationGenerationPolicy
    grading: VerificationGradingPolicy
    reverification: ReverificationPolicy


class IntelligencePolicy(_Strict):
    retrieval: RetrievalPolicy
    mapping: MappingPolicy
    qualification: QualificationPolicy
    processing_unit: ProcessingUnitPolicy
    skill_graph: SkillGraphPolicy
    attribution: AttributionPolicy
    evidence: EvidencePolicy
    mastery: MasteryPolicy
    debt: DebtPolicy
    recommendations: RecommendationPolicy
    verification: VerificationPolicy

    def snapshot(self) -> dict[str, Any]:
        """JSON form stored with each decision so it stays reproducible."""
        return self.model_dump(mode="json")


class PolicyConfigError(RuntimeError):
    pass


def verify_policy(pool: "ConnectionPool") -> IntelligencePolicy:
    """Fail fast before a worker starts: the P3B-P6 keys come from migrations 0005-0008."""
    with pool.connection() as conn:
        return load_policy(conn)


def load_policy(conn: Connection) -> IntelligencePolicy:
    """Global policy from policy_config. Fails loudly if a key is missing or invalid."""
    rows = conn.execute(
        "select key, value from public.policy_config where scope_type = 'global' and key = any(%s)",
        (list(POLICY_KEYS),),
    ).fetchall()
    values = {key: value for key, value in rows}
    missing = [key for key in POLICY_KEYS if key not in values]
    if missing:
        raise PolicyConfigError(f"policy_config is missing global keys: {', '.join(missing)}")
    try:
        return IntelligencePolicy.model_validate(values)
    except ValueError as exc:
        raise PolicyConfigError(f"policy_config is invalid: {exc}") from exc
