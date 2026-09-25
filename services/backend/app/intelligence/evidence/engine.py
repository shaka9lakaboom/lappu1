"""Deterministic evidence qualification (architecture §2.2, §9.6, §10.1, Appendix B; ADR 0005).

No model call. One attribution item of an ACCEPTED mapping either becomes one
EvidenceEvent draft or an explicit abstention. The rules, in order:

1. Uncertainty abstains; it never becomes weak evidence:
   UNKNOWN actor, attribution confidence below the policy gate, evidence type
   OTHER, a performance claim without a learner span or with a span that is not
   in the learner's message, a performance claim whose outcome is undetermined.
2. Consistency is enforced, whatever the model said:
   an AI actor is never learner performance (-> OBSERVATION, or EXPOSURE);
   a SHARED actor is never independent (-> ASSISTED_ATTEMPT);
   a learner span copied from earlier assistant output is the AI's work
   (-> actor AI, OBSERVATION).
3. Hard guard: EXPOSURE and OBSERVATION have strength 0 and no outcome.
4. Strength (Appendix B, B.1, B.2):
       evidence_confidence = min(mapping_confidence, attribution_confidence)
       difficulty_mul      = multiplier_base + multiplier_slope * difficulty
       strength            = base_weight * difficulty_mul * independence * evidence_confidence
   Every number comes from policy_config.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.intelligence.contracts import AttributionItem
from app.intelligence.policy import ZERO_STRENGTH_TYPES, AttributionPolicy, EvidencePolicy

QUALIFIER_VERSION = "evidence/p3b-v1"
EVIDENCE_CREATED = "EVIDENCE_CREATED"

# Learner performance: needs a grounded learner span and a determined outcome.
PERFORMANCE_TYPES = frozenset(
    {"ASSISTED_ATTEMPT", "INDEPENDENT_EXPLANATION", "INDEPENDENT_APPLICATION", "TRANSFER"}
)
INDEPENDENT_TYPES = frozenset({"INDEPENDENT_EXPLANATION", "INDEPENDENT_APPLICATION", "TRANSFER"})
ZERO_TYPES = frozenset(ZERO_STRENGTH_TYPES)

_QUOTES = re.compile(r"[`\"'‘’“”]")
_ELLIPSIS = re.compile(r"\.\.\.|…")


@dataclass(frozen=True)
class QualifiedEvidence:
    evidence_type: str
    actor: str
    outcome_signal: str
    outcome: float | None
    difficulty: float
    difficulty_multiplier: float
    independence: float
    base_weight: float
    strength: float
    mapping_confidence: float
    attribution_confidence: float
    evidence_confidence: float
    qualification_reason: str


@dataclass(frozen=True)
class Qualification:
    decision: str  # EVIDENCE_CREATED, or the abstention reason
    evidence: QualifiedEvidence | None = None


def normalize_span_text(text: str) -> str:
    """Casefolded, quotes/backticks removed, whitespace collapsed (tolerant span matching)."""
    return " ".join(_QUOTES.sub("", text).casefold().split())


def span_grounded(span: str, text: str) -> bool:
    """True if the span occurs in the text (an ellipsis may elide parts, kept in order)."""
    haystack = normalize_span_text(text)
    pieces = [normalize_span_text(p) for p in _ELLIPSIS.split(span)]
    pieces = [p for p in pieces if p]
    if not pieces:
        return False
    position = 0
    for piece in pieces:
        found = haystack.find(piece, position)
        if found < 0:
            return False
        position = found + len(piece)
    return True


def copied_from_ai(span: str, prior_assistant_texts: Sequence[str], min_chars: int) -> bool:
    """A learner span of at least `min_chars` that already appeared in earlier assistant output."""
    needle = normalize_span_text(span)
    if len(needle) < min_chars:
        return False
    return any(needle in normalize_span_text(t) for t in prior_assistant_texts)


def difficulty_for(difficulty_band: int | None, policy: EvidencePolicy) -> float:
    """The skill's 1-5 difficulty band normalized to [0, 1]; the policy default when absent."""
    if difficulty_band is None:
        return policy.difficulty.default
    return (min(max(difficulty_band, 1), 5) - 1) / 4


def qualify_attribution(
    item: AttributionItem,
    *,
    mapping_confidence: float,
    difficulty_band: int | None,
    learner_text: str | None,
    prior_assistant_texts: Sequence[str],
    attribution_policy: AttributionPolicy,
    evidence_policy: EvidencePolicy,
) -> Qualification:
    # 1. Uncertainty causes abstention (§2.2): no evidence, never weak evidence.
    if item.actor == "UNKNOWN":
        return Qualification("ACTOR_UNKNOWN")
    if item.confidence < attribution_policy.min_confidence:
        return Qualification("LOW_ATTRIBUTION_CONFIDENCE")
    if item.evidence_type == "OTHER":
        return Qualification("EVIDENCE_TYPE_OTHER")

    actor: str = item.actor
    evidence_type: str = item.evidence_type
    reason = "QUALIFIED"

    # 2. Consistency between actor and evidence type.
    if actor == "AI" and evidence_type not in ZERO_TYPES:
        evidence_type, reason = "OBSERVATION", "AI_ACTOR_NOT_PERFORMANCE"
    elif actor == "SHARED" and evidence_type in INDEPENDENT_TYPES:
        evidence_type, reason = "ASSISTED_ATTEMPT", "SHARED_NOT_INDEPENDENT"

    if evidence_type in PERFORMANCE_TYPES:
        span = item.student_evidence_span
        if not span:
            return Qualification("STUDENT_SPAN_MISSING")
        if not span_grounded(span, learner_text or ""):
            return Qualification("STUDENT_SPAN_NOT_GROUNDED")
        if copied_from_ai(span, prior_assistant_texts, attribution_policy.copy_guard_min_chars):
            actor, evidence_type, reason = "AI", "OBSERVATION", "COPIED_FROM_AI"

    # 3. Hard guard: exposure/observation never carry an outcome or strength.
    if evidence_type in ZERO_TYPES:
        outcome_signal, outcome = "NOT_APPLICABLE", None
    elif item.outcome_signal == "NOT_APPLICABLE":
        return Qualification("OUTCOME_UNDETERMINED")
    else:
        outcome_signal = item.outcome_signal
        outcome = getattr(evidence_policy.outcome_values, outcome_signal)

    # 4. Strength (Appendix B).
    evidence_confidence = min(mapping_confidence, item.confidence)
    difficulty = difficulty_for(difficulty_band, evidence_policy)
    multiplier = (
        evidence_policy.difficulty.multiplier_base
        + evidence_policy.difficulty.multiplier_slope * difficulty
    )
    independence = evidence_policy.independence[evidence_type]
    base_weight = evidence_policy.base_weights[evidence_type]
    if evidence_type in ZERO_TYPES:
        strength = 0.0
    else:
        strength = base_weight * multiplier * independence * evidence_confidence
    return Qualification(
        EVIDENCE_CREATED,
        QualifiedEvidence(
            evidence_type=evidence_type,
            actor=actor,
            outcome_signal=outcome_signal,
            outcome=outcome,
            difficulty=difficulty,
            difficulty_multiplier=multiplier,
            independence=independence,
            base_weight=base_weight,
            strength=strength,
            mapping_confidence=mapping_confidence,
            attribution_confidence=item.confidence,
            evidence_confidence=evidence_confidence,
            qualification_reason=reason,
        ),
    )
