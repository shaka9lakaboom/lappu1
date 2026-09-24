"""Intelligence policy (architecture §5.2 Rules & Policy Engine, Appendix B).

Every tunable weight and threshold is read from `public.policy_config`. This
module defines and validates the shape; it deliberately carries NO default
values, so a number can only come from the database (seeded by migration
0003) or from an explicit test fixture.
"""

from typing import Any, Literal

from psycopg import Connection
from pydantic import BaseModel, ConfigDict, Field, model_validator

POLICY_KEYS = ("retrieval", "mapping", "qualification", "processing_unit", "skill_graph")


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


class IntelligencePolicy(_Strict):
    retrieval: RetrievalPolicy
    mapping: MappingPolicy
    qualification: QualificationPolicy
    processing_unit: ProcessingUnitPolicy
    skill_graph: SkillGraphPolicy

    def snapshot(self) -> dict[str, Any]:
        """JSON form stored with each decision so it stays reproducible."""
        return self.model_dump(mode="json")


class PolicyConfigError(RuntimeError):
    pass


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
