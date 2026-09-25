"""AI Assistance Debt with real verification evidence (architecture §10.4, §11.1; ADR 0007).

VerificationFactor = 0.2 recently passed | 0.6 unverified | 1.0 failed (policy values). A
verification result only reaches debt as a VERIFICATION EvidenceEvent; it never creates debt
without the delegation eligibility guards, and a single AI interaction still creates none."""

import pytest

from tests.test_debt import POLICY, debt, delegation
from tests.test_mastery import record


def verification(outcome_signal: str = "CORRECT", *, days: float = 0.0, strength: float = 1.5):
    return record(
        "VERIFICATION", outcome_signal, strength=strength, days=days, source_type="VERIFICATION"
    )


DELEGATED = [delegation(days=d) for d in (1, 2, 3)]


def test_the_policy_values_are_the_architecture_factors() -> None:
    factors = POLICY.debt.verification_factor
    assert (factors.recently_passed, factors.unverified, factors.failed_or_due) == (0.2, 0.6, 1.0)


def test_no_verification_keeps_the_unverified_factor() -> None:
    d = debt(DELEGATED, importance=0.8)
    assert d.components["verification"] == "UNVERIFIED"
    assert d.components["verification_factor"] == 0.6


def test_a_passed_verification_sharply_reduces_debt() -> None:
    before = debt(DELEGATED, importance=0.8)
    after = debt([*DELEGATED, verification()], importance=0.8)
    assert before.actionable
    assert after.components["verification"] == "RECENTLY_PASSED"
    assert after.components["verification_factor"] == 0.2
    # The factor alone divides the score by 3; the pass also narrows the evidence gap.
    assert after.score < before.score / 3
    assert not after.actionable


def test_a_failed_verification_raises_the_factor() -> None:
    before = debt(DELEGATED, importance=0.8)
    after = debt([*DELEGATED, verification("INCORRECT")], importance=0.8)
    assert after.components["verification"] == "FAILED"
    assert after.components["verification_factor"] == 1.0
    assert after.score > before.score


def test_the_latest_recent_verification_decides_the_factor() -> None:
    failed_then_passed = debt([*DELEGATED, verification("INCORRECT", days=3), verification(days=1)])
    passed_then_failed = debt([*DELEGATED, verification(days=3), verification("INCORRECT", days=1)])
    assert failed_then_passed.components["verification"] == "RECENTLY_PASSED"
    assert passed_then_failed.components["verification"] == "FAILED"


def test_an_old_verification_no_longer_counts_as_recent() -> None:
    old = debt([*DELEGATED, verification(days=POLICY.debt.verification_recent_days + 1)])
    assert old.components["verification"] == "UNVERIFIED"


@pytest.mark.parametrize("outcome", ["CORRECT", "INCORRECT", "PARTIAL"])
def test_verification_never_creates_debt_without_delegation_eligibility(outcome) -> None:
    d = debt([verification(outcome)] * 3, importance=1.0)
    assert (d.eligible, d.score, d.actionable) == (False, 0.0, False)
    assert d.components["eligibility"] == "NO_DELEGATION"


def test_a_single_ai_use_plus_a_failed_verification_is_still_no_debt() -> None:
    d = debt([delegation(), verification("INCORRECT")], importance=1.0)
    assert (d.eligible, d.score) == (False, 0.0)
    assert d.components["eligibility"] == "INSUFFICIENT_DELEGATION"


def test_verification_evidence_is_never_counted_as_delegation() -> None:
    from app.intelligence.debt.engine import is_delegation
    from tests.test_mastery import NOW

    assert not is_delegation(verification("INCORRECT"), POLICY.debt, NOW)
