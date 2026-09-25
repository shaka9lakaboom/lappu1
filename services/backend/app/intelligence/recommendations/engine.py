"""Engine 16 - Recommendations (architecture §4 steps 12/14, §5.1, §11.1; ADR 0006).

Deterministic rules - no model call - over the ledger (mastery + AI Assistance Debt), the
course importance, the skill's prerequisites and the available evidence. One next action per
skill; the first matching rule wins:

    1. NEEDS_REVERIFICATION                        -> REVERIFY      VERIFICATION_STALE
    2. actionable debt (eligible, score >= the actionable threshold), and among the
       max_active_verify highest debts             -> VERIFY        REPEATED_DELEGATION_UNVERIFIED
    3. EMERGING / DEVELOPING with a prerequisite
       in a gap state (EMERGING)                   -> PREREQUISITE  PREREQUISITE_GAP
    4. EMERGING / DEVELOPING (performance evidence
       exists)                                     -> PRACTICE      EMERGING_NEEDS_PRACTICE /
                                                                    DEVELOPING_NEEDS_PRACTICE
    5. otherwise                                    -> NO_ACTION
         UNKNOWN       NOT_ENOUGH_EVIDENCE   (no judgement: gather more evidence)
         DEMONSTRATED  INDEPENDENT_EVIDENCE_SUFFICIENT
         VERIFIED      RECENTLY_VERIFIED

Invariants:
* Unknown is not weak. An UNKNOWN skill never gets PRACTICE or PREREQUISITE, and an
  UNKNOWN prerequisite is never a gap. Only repeated, unverified delegation (actionable
  debt) can make an UNKNOWN skill a VERIFY recommendation - that is exactly the case the
  architecture sends to verification (§16 "Repeated JOIN delegation + no independent
  evidence").
* AI use is not dependency. VERIFY needs actionable debt, which needs the debt engine's
  eligibility guards (>= 2 recent accepted high-confidence delegations).
* Learner burden (§11.1, Appendix B): at most max_active_verify VERIFY recommendations at a
  time; further actionable skills fall through to the next rule, marked verify_deferred.

VERIFIED and NEEDS_REVERIFICATION are unreachable before P6 (ADR 0005 §19); the rules
already handle them so P6 only has to produce the states.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.intelligence.explanation import debt_band
from app.intelligence.policy import IntelligencePolicy

ALGORITHM_VERSION = "recommendations/p5-v1"

RecommendationTypeName = Literal["NO_ACTION", "PRACTICE", "VERIFY", "PREREQUISITE", "REVERIFY"]

PRACTICE_STATES = ("EMERGING", "DEVELOPING")


@dataclass(frozen=True)
class PrerequisiteSignal:
    skill_id: UUID
    mastery_state: str
    mastery_mean: float | None


@dataclass(frozen=True)
class SkillSignal:
    """What the rules read for one skill (UNKNOWN / zeros when it has no ledger row)."""

    skill_id: UUID
    mastery_state: str
    mastery_mean: float | None
    support: float
    importance: float
    debt_eligible: bool
    debt_score: float
    performance_evidence_count: int
    ledger_version: int | None
    prerequisites: tuple[PrerequisiteSignal, ...] = ()


@dataclass(frozen=True)
class RecommendationDecision:
    skill_id: UUID
    type: RecommendationTypeName
    priority: int
    reason_code: str
    related_skill_id: UUID | None
    mastery_state: str
    inputs: dict[str, Any]

    @property
    def identity(self) -> tuple[str, str, UUID | None]:
        """What makes it the same recommendation across refreshes."""
        return (self.type, self.reason_code, self.related_skill_id)


def _scaled(base: int, span: int, fraction: float) -> int:
    """base + round-half-up(span * fraction), fraction clamped to [0, 1]."""
    return base + int(span * min(max(fraction, 0.0), 1.0) + 0.5)


def is_actionable(signal: SkillSignal, policy: IntelligencePolicy) -> bool:
    return signal.debt_eligible and signal.debt_score >= policy.debt.actionable_min_score


def prerequisite_gap(signal: SkillSignal, policy: IntelligencePolicy) -> PrerequisiteSignal | None:
    """The weakest prerequisite in a gap state (never UNKNOWN), or None."""
    gaps = [
        p
        for p in signal.prerequisites
        if p.mastery_state in policy.recommendations.prerequisite_gap_states
        and p.skill_id != signal.skill_id
    ]
    if not gaps:
        return None
    return min(
        gaps,
        key=lambda p: (p.mastery_mean if p.mastery_mean is not None else 1.0, str(p.skill_id)),
    )


def decide(
    signal: SkillSignal,
    *,
    verify: bool,
    verify_deferred: bool,
    policy: IntelligencePolicy,
) -> RecommendationDecision:
    state = signal.mastery_state
    importance = signal.importance
    gap = prerequisite_gap(signal, policy) if state in PRACTICE_STATES else None
    actionable = is_actionable(signal, policy)
    inputs: dict[str, Any] = {
        "mastery_state": state,
        # Unknown is not weak: no mean is recorded for an UNKNOWN skill.
        "mastery_mean": (
            round(signal.mastery_mean, 6)
            if signal.mastery_mean is not None and state != "UNKNOWN"
            else None
        ),
        "support": round(signal.support, 6),
        "performance_evidence_count": signal.performance_evidence_count,
        "importance": round(importance, 6),
        "debt_eligible": signal.debt_eligible,
        "debt_actionable": actionable,
        "debt_band": debt_band(
            signal.debt_eligible, signal.debt_score, policy.recommendations.debt_bands
        ),
        "debt_score": round(signal.debt_score, 6),
        "verify_deferred": verify_deferred,
        "prerequisite_gap": (
            {"skill_id": str(gap.skill_id), "mastery_state": gap.mastery_state} if gap else None
        ),
        "ledger_version": signal.ledger_version,
    }

    def make(
        kind: RecommendationTypeName, priority: int, reason: str, related: UUID | None = None
    ) -> RecommendationDecision:
        return RecommendationDecision(
            signal.skill_id, kind, priority, reason, related, state, inputs
        )

    if state == "NEEDS_REVERIFICATION":
        return make("REVERIFY", _scaled(80, 10, importance), "VERIFICATION_STALE")
    if verify and actionable:
        return make(
            "VERIFY", _scaled(60, 19, signal.debt_score / 100), "REPEATED_DELEGATION_UNVERIFIED"
        )
    if state in PRACTICE_STATES and signal.performance_evidence_count > 0:
        if gap is not None:
            return make(
                "PREREQUISITE", _scaled(50, 10, importance), "PREREQUISITE_GAP", gap.skill_id
            )
        if state == "EMERGING":
            return make("PRACTICE", _scaled(40, 10, importance), "EMERGING_NEEDS_PRACTICE")
        return make("PRACTICE", _scaled(30, 10, importance), "DEVELOPING_NEEDS_PRACTICE")
    if state == "DEMONSTRATED":
        return make("NO_ACTION", 0, "INDEPENDENT_EVIDENCE_SUFFICIENT")
    if state == "VERIFIED":
        return make("NO_ACTION", 0, "RECENTLY_VERIFIED")
    return make("NO_ACTION", 0, "NOT_ENOUGH_EVIDENCE")


def recommend_all(
    signals: Iterable[SkillSignal], *, policy: IntelligencePolicy
) -> list[RecommendationDecision]:
    """One decision per skill, in skill-id order. Deterministic for the same inputs."""
    ordered = sorted(signals, key=lambda s: str(s.skill_id))
    candidates = [
        s for s in ordered if is_actionable(s, policy) and s.mastery_state != "NEEDS_REVERIFICATION"
    ]
    ranked = sorted(candidates, key=lambda s: (-s.debt_score, -s.importance, str(s.skill_id)))
    selected = {s.skill_id for s in ranked[: policy.recommendations.max_active_verify]}
    deferred = {s.skill_id for s in candidates} - selected
    return [
        decide(
            s,
            verify=s.skill_id in selected,
            verify_deferred=s.skill_id in deferred,
            policy=policy,
        )
        for s in ordered
    ]
