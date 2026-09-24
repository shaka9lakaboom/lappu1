"""Deterministic candidate scoring (architecture §9.3).

    candidate_score = w_semantic * semantic_similarity
                    + w_lexical  * lexical_score
                    + w_prior    * course_context_prior

The weights come from policy_config (`retrieval.weights`); nothing here has a
default. Inputs are clamped to [0, 1]:

* semantic_similarity: cosine similarity of the query and skill embeddings.
* lexical_score: the skill's full-text rank divided by the best rank in the
  same candidate set (relative score fusion); 0 when nothing matched lexically.
* course_context_prior: the skill's importance in the active course scope, 0
  for skills found only in the global registry.
"""

from dataclasses import dataclass
from uuid import UUID

from app.intelligence.policy import RetrievalWeights


@dataclass(frozen=True)
class RawCandidate:
    skill_id: UUID
    canonical_name: str
    description: str
    node_kind: str
    lexical_raw: float
    semantic: float | None
    course_importance: float | None
    parent_ids: tuple[UUID, ...] = ()
    topic_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScoredCandidate:
    raw: RawCandidate
    semantic_similarity: float
    lexical_score: float
    course_context_prior: float
    candidate_score: float

    @property
    def in_course(self) -> bool:
        return self.raw.course_importance is not None


def clamp01(value: float | None) -> float:
    if value is None or value != value:  # None or NaN
        return 0.0
    return max(0.0, min(1.0, float(value)))


def candidate_score(
    semantic_similarity: float,
    lexical_score: float,
    course_context_prior: float,
    weights: RetrievalWeights,
) -> float:
    return round(
        weights.semantic * clamp01(semantic_similarity)
        + weights.lexical * clamp01(lexical_score)
        + weights.course_prior * clamp01(course_context_prior),
        6,
    )


def score_candidates(
    raw: list[RawCandidate], weights: RetrievalWeights, pool_size: int
) -> list[ScoredCandidate]:
    """Score, order (score desc, in-course first, name, id) and keep the top `pool_size`."""
    best_lexical = max((max(c.lexical_raw, 0.0) for c in raw), default=0.0)
    scored = []
    for c in raw:
        lexical = (max(c.lexical_raw, 0.0) / best_lexical) if best_lexical > 0 else 0.0
        semantic = clamp01(c.semantic)
        prior = clamp01(c.course_importance)
        scored.append(
            ScoredCandidate(
                raw=c,
                semantic_similarity=semantic,
                lexical_score=clamp01(lexical),
                course_context_prior=prior,
                candidate_score=candidate_score(semantic, lexical, prior, weights),
            )
        )
    scored.sort(
        key=lambda s: (
            -s.candidate_score,
            not s.in_course,
            s.raw.canonical_name.lower(),
            str(s.raw.skill_id),
        )
    )
    return scored[:pool_size]
