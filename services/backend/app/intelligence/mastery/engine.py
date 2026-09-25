"""Mastery model (architecture §10.2, §10.3, Appendix B; ADR 0005). Deterministic, no model call.

Recomputed from ALL non-excluded, performance-bearing EvidenceEvents of a skill:

    age_days           = whole UTC days from occurred_at to as_of
    recency_multiplier = exp(-ln(2) * age_days / recency_half_life_days)
    w_i                = strength_i * recency_multiplier_i
    alpha              = prior_alpha + sum(w_i * outcome_i)
    beta               = prior_beta  + sum(w_i * (1 - outcome_i))
    mastery_mean       = alpha / (alpha + beta)
    support            = sum(w_i)

(strength_i already holds base weight x difficulty x independence x confidence.)

States, checked in this order - UNKNOWN first, so insufficient support is
UNKNOWN whatever the mean looks like (§2.2 "unknown is not weak"):

    UNKNOWN               support < unknown_min_support
    VERIFIED              recent successful SkillMirror verification + mean/support gates
    NEEDS_REVERIFICATION  verified before, but that verification is stale
    EMERGING              mean < emerging_below_mean
    DEMONSTRATED          mean >= demonstrated_min_mean, support >= demonstrated_min_support
                          and at least one successful independent application
    DEVELOPING            otherwise

VERIFIED and NEEDS_REVERIFICATION need evidence from a SkillMirror verification
source. Before P6 no source counts as one (VERIFICATION_SOURCES is empty, so even a
VERIFICATION evidence row cannot verify), nothing writes such evidence, and migration
0006 refuses both states in skill_ledger: they are unreachable in P4.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.intelligence.policy import ZERO_STRENGTH_TYPES, MasteryPolicy

ALGORITHM_VERSION = "ledger/p4-v1"

# Evidence sources that count as SkillMirror-controlled verification. P6 adds
# "VERIFICATION" together with the verification system and its migration.
VERIFICATION_SOURCES: frozenset[str] = frozenset()
VERIFICATION_EVIDENCE_TYPES = frozenset({"VERIFICATION", "TRANSFER"})

MasteryStateName = Literal[
    "UNKNOWN", "EMERGING", "DEVELOPING", "DEMONSTRATED", "VERIFIED", "NEEDS_REVERIFICATION"
]


@dataclass(frozen=True)
class EvidenceRecord:
    """One EvidenceEvent as the ledger sees it (plus the provenance debt needs)."""

    id: UUID
    skill_id: UUID
    source_type: str
    evidence_type: str
    actor: str
    outcome_signal: str
    outcome: float | None
    strength: float
    evidence_confidence: float
    occurred_at: datetime
    excluded: bool = False
    rationale_code: str | None = None
    learning_relevance: str | None = None
    mapping_status: str | None = None

    @property
    def performance_bearing(self) -> bool:
        return (
            not self.excluded
            and self.outcome is not None
            and self.strength > 0
            and self.evidence_type not in ZERO_STRENGTH_TYPES
        )


@dataclass(frozen=True)
class MasteryResult:
    alpha: float
    beta: float
    mastery_mean: float
    support: float
    state: MasteryStateName
    evidence_count: int
    performance_evidence_count: int
    last_evidence_at: datetime | None
    has_application: bool
    recently_verified: bool
    previously_verified: bool


def age_days(as_of: datetime, occurred_at: datetime) -> int:
    """Whole UTC calendar days from the activity to `as_of` (never negative).

    Day granularity keeps the ledger constant within a day: a replay or a second
    recomputation on the same day re-derives exactly the same row."""
    return max((as_of.astimezone(UTC).date() - occurred_at.astimezone(UTC).date()).days, 0)


def recency_multiplier(age: float, half_life_days: float) -> float:
    return math.exp(-math.log(2) * age / half_life_days)


def is_verification(record: EvidenceRecord) -> bool:
    return (
        record.source_type in VERIFICATION_SOURCES
        and record.evidence_type in VERIFICATION_EVIDENCE_TYPES
    )


def classify_state(
    mastery_mean: float,
    support: float,
    *,
    has_application: bool,
    recently_verified: bool,
    previously_verified: bool,
    policy: MasteryPolicy,
) -> MasteryStateName:
    if support < policy.unknown_min_support:
        return "UNKNOWN"
    if (
        recently_verified
        and mastery_mean >= policy.verified_min_mean
        and support >= policy.verified_min_support
    ):
        return "VERIFIED"
    if previously_verified and not recently_verified:
        return "NEEDS_REVERIFICATION"
    if mastery_mean < policy.emerging_below_mean:
        return "EMERGING"
    if (
        mastery_mean >= policy.demonstrated_min_mean
        and support >= policy.demonstrated_min_support
        and has_application
    ):
        return "DEMONSTRATED"
    return "DEVELOPING"


def compute_mastery(
    records: Iterable[EvidenceRecord], policy: MasteryPolicy, as_of: datetime
) -> MasteryResult:
    included = [r for r in records if not r.excluded]
    performance = [r for r in included if r.performance_bearing]
    alpha, beta, support = policy.prior_alpha, policy.prior_beta, 0.0
    for record in performance:
        weight = record.strength * recency_multiplier(
            age_days(as_of, record.occurred_at), policy.recency_half_life_days
        )
        alpha += weight * record.outcome  # type: ignore[operator]  # performance_bearing
        beta += weight * (1.0 - record.outcome)  # type: ignore[operator]
        support += weight
    mean = alpha / (alpha + beta)

    has_application = any(
        r.evidence_type in policy.application_types
        and (r.outcome or 0.0) >= policy.application_min_outcome
        for r in performance
    )
    passes = [r for r in performance if is_verification(r) and r.outcome_signal == "CORRECT"]
    recently_verified = any(
        age_days(as_of, r.occurred_at) <= policy.verification_max_age_days for r in passes
    )
    state = classify_state(
        mean,
        support,
        has_application=has_application,
        recently_verified=recently_verified,
        previously_verified=bool(passes),
        policy=policy,
    )
    return MasteryResult(
        alpha=alpha,
        beta=beta,
        mastery_mean=mean,
        support=support,
        state=state,
        evidence_count=len(included),
        performance_evidence_count=len(performance),
        last_evidence_at=max((r.occurred_at for r in included), default=None),
        has_application=has_application,
        recently_verified=recently_verified,
        previously_verified=bool(passes),
    )
