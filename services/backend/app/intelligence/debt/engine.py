"""AI Assistance Debt (architecture §2.1, §10.4, Appendix B; ADR 0005). Deterministic, no model call.

AI Assistance Debt is UNVERIFIED SKILL DELEGATION, not an AI-usage count. A
delegation event is a non-excluded EvidenceEvent from captured AI activity where

* the actor is AI or SHARED, and the evidence type is a delegation type
  (OBSERVATION: the AI performed the skill; ASSISTED_ATTEMPT: shared work) -
  receiving an explanation (EXPOSURE) is learning, not delegation;
* the mapping was ACCEPTED, the segment learning-relevant, the evidence
  confidence high (>= min_evidence_confidence);
* it is not a trivial utility use (reason code in trivial_reason_codes);
* it happened within the recent window.

Eligibility needs at least `min_recent_delegations` (>= 2) such events, so one AI
question, one calculator use or one syntax lookup never creates debt. For an
eligible skill:

    DelegationPressure = 1 - exp(-weighted_recent_delegation / tau)
    EvidenceGap        = 1 - mastery_mean * min(1, support / evidence_gap_support_target)
    DebtScore          = 100 * DelegationPressure * EvidenceGap * Importance
                             * Confidence * VerificationFactor

weighted_recent_delegation sums actor_weight * evidence_confidence * recency (its
own half-life); Confidence is the mean evidence confidence of those events;
VerificationFactor is 0.2 recently passed / 0.6 unverified / 1.0 failed (before
P6 every skill is unverified). Strong independent evidence closes the gap, so
heavy AI use together with demonstrated skill yields low debt.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.intelligence.mastery.engine import (
    EvidenceRecord,
    MasteryResult,
    age_days,
    is_verification,
    recency_multiplier,
)
from app.intelligence.policy import DebtPolicy


@dataclass(frozen=True)
class DebtResult:
    score: float
    eligible: bool
    actionable: bool
    recent_delegation_count: int
    components: dict[str, Any]


def is_delegation(record: EvidenceRecord, policy: DebtPolicy, as_of: datetime) -> bool:
    return (
        not record.excluded
        and record.source_type == "AI_ACTIVITY"
        and record.actor in policy.actor_weights
        and record.evidence_type in policy.delegation_evidence_types
        and record.mapping_status == "ACCEPTED"
        and record.learning_relevance in policy.learning_relevance_levels
        and record.evidence_confidence >= policy.min_evidence_confidence
        and (record.rationale_code or "") not in policy.trivial_reason_codes
        and age_days(as_of, record.occurred_at) <= policy.recent_window_days
    )


def verification_factor(
    records: Iterable[EvidenceRecord], policy: DebtPolicy, as_of: datetime
) -> tuple[float, str]:
    recent = sorted(
        (
            r
            for r in records
            if not r.excluded
            and is_verification(r)
            and age_days(as_of, r.occurred_at) <= policy.verification_recent_days
        ),
        key=lambda r: r.occurred_at,
    )
    factors = policy.verification_factor
    if not recent:
        return factors.unverified, "UNVERIFIED"
    if recent[-1].outcome_signal == "CORRECT":
        return factors.recently_passed, "RECENTLY_PASSED"
    return factors.failed_or_due, "FAILED"


def _r(value: float) -> float:
    return round(value, 6)


def compute_debt(
    records: Iterable[EvidenceRecord],
    mastery: MasteryResult,
    *,
    importance: float,
    policy: DebtPolicy,
    as_of: datetime,
) -> DebtResult:
    records = list(records)
    delegations = [r for r in records if is_delegation(r, policy, as_of)]
    count = len(delegations)
    if count < policy.min_recent_delegations:
        return DebtResult(
            score=0.0,
            eligible=False,
            actionable=False,
            recent_delegation_count=count,
            components={
                "eligibility": "NO_DELEGATION" if count == 0 else "INSUFFICIENT_DELEGATION",
                "recent_delegation_count": count,
                "min_recent_delegations": policy.min_recent_delegations,
            },
        )

    weighted = sum(
        policy.actor_weights[r.actor]  # type: ignore[index]  # filtered by is_delegation
        * r.evidence_confidence
        * recency_multiplier(age_days(as_of, r.occurred_at), policy.delegation_half_life_days)
        for r in delegations
    )
    pressure = 1.0 - math.exp(-weighted / policy.tau)
    adjusted = mastery.mastery_mean * min(1.0, mastery.support / policy.evidence_gap_support_target)
    gap = 1.0 - adjusted
    confidence = sum(r.evidence_confidence for r in delegations) / count
    factor, verification = verification_factor(records, policy, as_of)
    score = min(max(100.0 * pressure * gap * importance * confidence * factor, 0.0), 100.0)
    score = _r(score)
    return DebtResult(
        score=score,
        eligible=True,
        actionable=score >= policy.actionable_min_score,
        recent_delegation_count=count,
        components={
            "eligibility": "ELIGIBLE",
            "recent_delegation_count": count,
            "weighted_recent_delegation": _r(weighted),
            "delegation_pressure": _r(pressure),
            "mastery_adjusted_for_support": _r(adjusted),
            "evidence_gap": _r(gap),
            "importance": _r(importance),
            "confidence": _r(confidence),
            "verification_factor": _r(factor),
            "verification": verification,
        },
    )
