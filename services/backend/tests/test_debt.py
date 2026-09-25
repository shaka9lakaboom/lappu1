"""AI Assistance Debt (architecture §2.1, §10.4; ADR 0005): unverified skill delegation,
never an AI-usage count; one-off AI use never creates debt."""

import math
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.intelligence.debt.engine import compute_debt, is_delegation
from app.intelligence.mastery.engine import compute_mastery
from app.intelligence.policy import DebtPolicy
from tests.fakes import ARCHITECTURE_POLICY, policy
from tests.test_mastery import NOW, record

POLICY = policy()


def delegation(days=1.0, *, actor="AI", confidence=0.9, reason="SOLUTION_REQUESTED", **changes):
    r = record("OBSERVATION", actor=actor, days=days, confidence=confidence)
    return replace(r, rationale_code=reason, **changes)


def debt(records, importance=0.5):
    records = list(records)
    mastery = compute_mastery(
        records, POLICY.mastery, NOW, reverification=POLICY.verification.reverification
    )
    return compute_debt(records, mastery, importance=importance, policy=POLICY.debt, as_of=NOW)


def test_a_single_ai_interaction_never_creates_debt() -> None:
    d = debt([delegation()])
    assert (d.eligible, d.score, d.actionable, d.recent_delegation_count) == (False, 0.0, False, 1)
    assert d.components["eligibility"] == "INSUFFICIENT_DELEGATION"


def test_no_ai_use_means_no_debt() -> None:
    d = debt([record(strength=1.0)])
    assert (d.eligible, d.score) == (False, 0.0)
    assert d.components["eligibility"] == "NO_DELEGATION"


def test_repeated_delegation_becomes_eligible_with_the_architecture_formula() -> None:
    records = [delegation(days=d) for d in (1, 2, 3)]
    d = debt(records, importance=0.8)
    assert d.eligible and d.recent_delegation_count == 3
    weighted = sum(0.9 * math.exp(-math.log(2) * days / 14) for days in (1, 2, 3))
    pressure = 1 - math.exp(-weighted / 2.0)
    gap = 1 - 0.5 * min(1.0, 0.0 / 3.0)  # no independent evidence: the full gap
    expected = 100 * pressure * gap * 0.8 * 0.9 * 0.6  # unverified factor 0.6 before P6
    assert d.score == pytest.approx(expected, abs=1e-5)
    assert d.actionable and d.components["verification"] == "UNVERIFIED"
    assert d.components["evidence_gap"] == 1.0


def test_heavy_ai_use_with_strong_independent_evidence_is_low_debt() -> None:
    heavy = [delegation(days=d) for d in range(1, 9)]
    independent = [record(strength=1.0, days=d) for d in range(1, 6)]
    d = debt([*heavy, *independent])
    assert d.eligible and d.recent_delegation_count == 8
    assert d.components["delegation_pressure"] > 0.9  # lots of AI use ...
    assert d.score < 5 and not d.actionable  # ... but the skill is demonstrated


def test_exposure_only_ai_use_is_learning_not_delegation() -> None:
    explained = [
        replace(record("EXPOSURE", actor="AI", days=d), rationale_code="EXPLANATION_REQUESTED")
        for d in range(10)
    ]
    d = debt(explained)
    assert (d.eligible, d.score, d.recent_delegation_count) == (False, 0.0, 0)


def test_raw_ai_usage_count_alone_never_creates_debt() -> None:
    # Fifty AI interactions that are not accepted, high-confidence, learning-relevant delegation.
    noise = (
        [delegation(confidence=0.7)] * 10
        + [delegation(mapping_status="ABSTAINED")] * 10
        + [delegation(learning_relevance="low")] * 10
        + [delegation(reason="TRIVIAL_UTILITY")] * 10
        + [delegation(excluded=True)] * 10
    )
    d = debt(noise)
    assert (d.eligible, d.score, d.recent_delegation_count) == (False, 0.0, 0)


def test_trivial_utility_use_is_ignored_even_when_repeated() -> None:
    d = debt([delegation(reason="TRIVIAL_UTILITY", days=d) for d in range(5)])
    assert (d.eligible, d.score) == (False, 0.0)


def test_old_delegation_outside_the_window_does_not_count() -> None:
    d = debt([delegation(days=d) for d in (45, 60, 90)])
    assert (d.eligible, d.recent_delegation_count) == (False, 0)


def test_student_actor_is_never_delegation() -> None:
    r = replace(record("ASSISTED_ATTEMPT", strength=0.1), rationale_code="HINT_THEN_COMPLETED")
    assert not is_delegation(r, POLICY.debt, NOW)
    shared = replace(r, actor="SHARED")
    assert is_delegation(shared, POLICY.debt, NOW)


def test_shared_work_weighs_half_of_ai_work() -> None:
    ai = debt([delegation(days=1), delegation(days=2)])
    shared = debt([delegation(days=1, actor="SHARED"), delegation(days=2, actor="SHARED")])
    assert shared.components["weighted_recent_delegation"] == pytest.approx(
        ai.components["weighted_recent_delegation"] / 2
    )


def test_repeated_delegation_with_independent_struggle_is_actionable() -> None:
    struggle = [record(outcome_signal="INCORRECT", strength=0.9, days=d) for d in (2, 4)]
    d = debt([*[delegation(days=d) for d in (1, 3, 5)], *struggle])
    assert d.eligible and d.actionable and d.components["evidence_gap"] > 0.8


@pytest.mark.parametrize(
    "patch",
    [
        {"min_recent_delegations": 1},
        {"delegation_evidence_types": ["EXPOSURE", "OBSERVATION"]},
        {"actor_weights": {"AI": 1.5}},
        {"verification_factor": {"recently_passed": 0.9, "unverified": 0.6, "failed_or_due": 1.0}},
    ],
)
def test_policy_guards_against_one_off_or_exposure_debt(patch) -> None:
    with pytest.raises(ValidationError):
        DebtPolicy.model_validate({**ARCHITECTURE_POLICY["debt"], **patch})
