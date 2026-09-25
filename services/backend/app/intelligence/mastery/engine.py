"""Mastery model (architecture §10.2, §10.3, Appendix B; ADR 0005, ADR 0007). Deterministic, no model call.

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
UNKNOWN whatever the mean or verification history looks like (§2.2 "unknown is not weak"):

    UNKNOWN               support < unknown_min_support
    VERIFIED              verification standing CURRENT (below)
    NEEDS_REVERIFICATION  verification standing STALE or CONTRADICTED
    EMERGING              mean < emerging_below_mean
    DEMONSTRATED          mean >= demonstrated_min_mean, support >= demonstrated_min_support
                          and at least one successful independent application
    DEVELOPING            otherwise

Verification (P6). Only evidence from a SkillMirror verification counts (VERIFICATION_SOURCES).
§10.3 lists VERIFIED's thresholds as the *initial* gate, and NEEDS_REVERIFICATION as "previously
VERIFIED but verification evidence is stale or materially contradicted by newer evidence". So:

* VERIFIED is ENTERED at a checkpoint (the current instant, or the day of an earlier performance
  evidence event) where a recent successful verification exists (age <= verification_max_age_days)
  AND mastery_mean >= verified_min_mean AND support >= verified_min_support, computed from the
  evidence up to that checkpoint. A pass alone never verifies a skill whose mean or support is
  short of the gates.
* Once entered, VERIFIED HOLDS - a single later failure lowers the mean (it is recorded, never
  hidden) but does not erase the verified history - until either
    - the verification that anchored it is stale (older than verification_max_age_days), or
    - newer evidence materially contradicts it: at least `min_contradicting_failures` (>= 2,
      policy) independent failures after the last verified checkpoint.
  Then the skill NEEDS_REVERIFICATION (standing STALE / CONTRADICTED). A later SkillMirror check
  resolves that need: if it restores the VERIFIED gates the skill is VERIFIED again, otherwise the
  regular evidence gates (EMERGING / DEVELOPING / DEMONSTRATED) apply.

Migration 0008 additionally refuses VERIFIED / NEEDS_REVERIFICATION in skill_ledger without a
passed VERIFICATION EvidenceEvent of the skill.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.intelligence.policy import ZERO_STRENGTH_TYPES, MasteryPolicy, ReverificationPolicy

# ledger/p6-v1: VERIFICATION evidence is active (verification standing, hold, reverification).
# ledger/p8-v1: a copy-guard reclassification (COPIED_FROM_AI) is not a delegation (ADR 0008).
ALGORITHM_VERSION = "ledger/p8-v1"

# Evidence sources that count as SkillMirror-controlled verification (P6).
VERIFICATION_SOURCES: frozenset[str] = frozenset({"VERIFICATION"})
VERIFICATION_EVIDENCE_TYPES = frozenset({"VERIFICATION", "TRANSFER"})

MasteryStateName = Literal[
    "UNKNOWN", "EMERGING", "DEVELOPING", "DEMONSTRATED", "VERIFIED", "NEEDS_REVERIFICATION"
]
# NONE: never verified. CURRENT: verified (entered now, or held). STALE / CONTRADICTED: it was
# verified, and needs a fresh check.
VerificationStanding = Literal["NONE", "CURRENT", "STALE", "CONTRADICTED"]


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
    # The deterministic qualification's reason (e.g. COPIED_FROM_AI), not the model's.
    qualification_reason: str | None = None

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
    # A successful SkillMirror verification within verification_max_age_days.
    recently_verified: bool
    # The skill was VERIFIED at some point (entered at a checkpoint).
    previously_verified: bool
    verification_standing: VerificationStanding = "NONE"
    # When the verification anchoring the (current or last) VERIFIED standing happened.
    last_verified_at: datetime | None = None
    # Independent failures since the last verified checkpoint (the contradiction count).
    contradicting_failures: int = 0


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


def is_verification_pass(record: EvidenceRecord) -> bool:
    return (
        record.performance_bearing
        and is_verification(record)
        and (record.outcome_signal == "CORRECT")
    )


def classify_state(
    mastery_mean: float,
    support: float,
    *,
    has_application: bool,
    verification: VerificationStanding,
    policy: MasteryPolicy,
) -> MasteryStateName:
    if support < policy.unknown_min_support:
        return "UNKNOWN"
    if verification == "CURRENT":
        return "VERIFIED"
    if verification in ("STALE", "CONTRADICTED"):
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


def _weighted(
    performance: Sequence[EvidenceRecord], policy: MasteryPolicy, as_of: datetime
) -> tuple[float, float, float]:
    alpha, beta, support = policy.prior_alpha, policy.prior_beta, 0.0
    for record in performance:
        weight = record.strength * recency_multiplier(
            age_days(as_of, record.occurred_at), policy.recency_half_life_days
        )
        alpha += weight * record.outcome  # type: ignore[operator]  # performance_bearing
        beta += weight * (1.0 - record.outcome)  # type: ignore[operator]
        support += weight
    return alpha, beta, support


def _verified_gates(
    performance: Sequence[EvidenceRecord], policy: MasteryPolicy, as_of: datetime
) -> bool:
    """The §10.3 VERIFIED gates at `as_of`: a recent pass plus the mean and support gates."""
    if not any(
        is_verification_pass(r)
        and age_days(as_of, r.occurred_at) <= policy.verification_max_age_days
        for r in performance
    ):
        return False
    alpha, beta, support = _weighted(performance, policy, as_of)
    return (
        alpha / (alpha + beta) >= policy.verified_min_mean
        and support >= policy.verified_min_support
    )


def _contradicts(record: EvidenceRecord, policy: ReverificationPolicy) -> bool:
    return (
        record.evidence_type in policy.contradicting_evidence_types
        and record.outcome_signal in policy.contradicting_outcome_signals
    )


def verification_standing(
    performance: Sequence[EvidenceRecord],
    *,
    policy: MasteryPolicy,
    reverification: ReverificationPolicy,
    as_of: datetime,
) -> tuple[VerificationStanding, datetime | None, int]:
    """(standing, anchor verification time, contradicting failures) for chronological evidence.

    Checkpoints are the current instant and the day of every performance event at or after the
    first pass, each judged on the evidence up to it. The last checkpoint that met the VERIFIED
    gates anchors the standing to the latest pass before it."""
    first_pass = next((i for i, r in enumerate(performance) if is_verification_pass(r)), None)
    if first_pass is None:
        return "NONE", None, 0

    def anchor(upto: int) -> datetime:
        return max(r.occurred_at for r in performance[: upto + 1] if is_verification_pass(r))

    if _verified_gates(performance, policy, as_of):
        return "CURRENT", anchor(len(performance) - 1), 0
    last_checkpoint = None
    for index in range(len(performance) - 1, first_pass - 1, -1):
        if _verified_gates(performance[: index + 1], policy, performance[index].occurred_at):
            last_checkpoint = index
            break
    if last_checkpoint is None:
        return "NONE", None, 0
    anchored_at = anchor(last_checkpoint)
    later = performance[last_checkpoint + 1 :]
    failures = sum(1 for r in later if _contradicts(r, reverification))
    contradicted = failures >= reverification.min_contradicting_failures
    stale = age_days(as_of, anchored_at) > policy.verification_max_age_days
    if not (contradicted or stale):
        return "CURRENT", anchored_at, failures
    if any(is_verification(r) for r in later):
        # A SkillMirror check after the last verified checkpoint already re-checked the skill
        # (it did not meet the VERIFIED gates, or the skill would have a newer checkpoint):
        # the need for re-verification is resolved and the regular evidence gates apply.
        return "NONE", anchored_at, failures
    return ("CONTRADICTED" if contradicted else "STALE"), anchored_at, failures


def compute_mastery(
    records: Iterable[EvidenceRecord],
    policy: MasteryPolicy,
    as_of: datetime,
    *,
    reverification: ReverificationPolicy,
) -> MasteryResult:
    included = [r for r in records if not r.excluded]
    # Chronological (then by id): checkpoints and "newer evidence" are well defined.
    performance = sorted(
        (r for r in included if r.performance_bearing), key=lambda r: (r.occurred_at, str(r.id))
    )
    alpha, beta, support = _weighted(performance, policy, as_of)
    mean = alpha / (alpha + beta)

    has_application = any(
        r.evidence_type in policy.application_types
        and (r.outcome or 0.0) >= policy.application_min_outcome
        for r in performance
    )
    recently_verified = any(
        is_verification_pass(r)
        and age_days(as_of, r.occurred_at) <= policy.verification_max_age_days
        for r in performance
    )
    standing, verified_at, failures = verification_standing(
        performance, policy=policy, reverification=reverification, as_of=as_of
    )
    state = classify_state(
        mean, support, has_application=has_application, verification=standing, policy=policy
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
        previously_verified=verified_at is not None,
        verification_standing=standing,
        last_verified_at=verified_at,
        contradicting_failures=failures,
    )
