"""P6 mastery semantics (architecture §10.3, §16 "Evidence"; ADR 0007): VERIFIED is entered through
the frozen gates (recent successful SkillMirror verification + mean >= 0.80 + support >= 4.0) and
held until the verification is stale or materially contradicted (>= 2 later independent failures,
policy). UNKNOWN is still checked first, and a single isolated failure never erases VERIFIED."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.intelligence.mastery.engine import EvidenceRecord, compute_mastery
from app.intelligence.policy import ReverificationPolicy
from tests.fakes import ARCHITECTURE_POLICY, policy

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
POLICY = policy()
MASTERY = POLICY.mastery
REVERIFICATION = POLICY.verification.reverification
SKILL = uuid4()


def ev(
    evidence_type: str = "INDEPENDENT_APPLICATION",
    outcome_signal: str = "CORRECT",
    *,
    days: float,
    strength: float = 0.9,
    source_type: str = "AI_ACTIVITY",
) -> EvidenceRecord:
    outcome = {"CORRECT": 1.0, "PARTIAL": 0.5, "INCORRECT": 0.0}[outcome_signal]
    return EvidenceRecord(
        id=uuid4(),
        skill_id=SKILL,
        source_type=source_type,
        evidence_type=evidence_type,
        actor="STUDENT",
        outcome_signal=outcome_signal,
        outcome=outcome,
        strength=strength,
        evidence_confidence=1.0,
        occurred_at=NOW - timedelta(days=days),
    )


def check(outcome_signal: str = "CORRECT", *, days: float, strength: float = 1.5) -> EvidenceRecord:
    """A SkillMirror verification result as evidence (source VERIFICATION, type VERIFICATION)."""
    return ev(
        "VERIFICATION", outcome_signal, days=days, strength=strength, source_type="VERIFICATION"
    )


def mastery(records, *, reverification: ReverificationPolicy = REVERIFICATION, as_of=NOW):
    return compute_mastery(records, MASTERY, as_of, reverification=reverification)


# Three independent applications (2.7) + a pass (1.5): support 4.2, mean 5.2 / 6.2 = 0.839.
STRONG = [ev(days=10), ev(days=9), ev(days=8)]


def test_a_pass_with_all_gates_is_verified() -> None:
    m = mastery([*STRONG, check(days=5)])
    assert m.state == "VERIFIED" and m.verification_standing == "CURRENT"
    assert m.mastery_mean >= MASTERY.verified_min_mean and m.support >= MASTERY.verified_min_support
    assert m.last_verified_at == NOW - timedelta(days=5)


def test_a_successful_verification_with_insufficient_support_is_not_verified() -> None:
    m = mastery([ev(days=10), check(days=5)])
    assert m.support < MASTERY.verified_min_support
    assert m.state not in ("VERIFIED", "NEEDS_REVERIFICATION")
    assert m.verification_standing == "NONE" and not m.previously_verified
    assert m.recently_verified  # the pass is recorded, it just does not verify by itself


def test_a_successful_verification_with_a_weak_mean_is_not_verified() -> None:
    history = [*STRONG, ev("INDEPENDENT_APPLICATION", "INCORRECT", days=9), ev(days=7)]
    m = mastery([*history, check(days=5)])
    assert m.support >= MASTERY.verified_min_support and m.mastery_mean < MASTERY.verified_min_mean
    assert m.state != "VERIFIED"


def test_later_independent_evidence_can_complete_the_gates_while_the_pass_is_recent() -> None:
    before = mastery([ev(days=10), check(days=9)])
    after = mastery([ev(days=10), check(days=9), ev(days=5), ev(days=4)])
    assert before.state != "VERIFIED" and after.state == "VERIFIED"


def test_stale_verification_needs_reverification() -> None:
    old = [ev(days=210), ev(days=205), ev(days=200), check(days=190)]
    m = mastery(old)
    assert m.state == "NEEDS_REVERIFICATION" and m.verification_standing == "STALE"
    assert m.support >= MASTERY.unknown_min_support
    fresh = mastery(old, as_of=NOW - timedelta(days=20))
    assert fresh.state == "VERIFIED"


def test_unknown_is_still_checked_first_for_a_long_forgotten_verification() -> None:
    m = mastery([ev(days=710), ev(days=705), ev(days=700), check(days=690)])
    assert m.support < MASTERY.unknown_min_support
    assert m.state == "UNKNOWN" and m.previously_verified


def test_one_isolated_later_failure_does_not_erase_verified() -> None:
    """Regression: even when that failure pulls the mean below the VERIFIED entry gate."""
    verified = [*STRONG, check(days=5)]
    after = mastery([*verified, ev("INDEPENDENT_APPLICATION", "INCORRECT", days=3)])
    assert after.mastery_mean < MASTERY.verified_min_mean  # the failure is recorded, not hidden
    assert after.state == "VERIFIED" and after.verification_standing == "CURRENT"
    assert after.contradicting_failures == 1


def test_one_failed_verification_after_verified_does_not_erase_it_either() -> None:
    m = mastery([*STRONG, check(days=5), check("INCORRECT", days=2)])
    assert m.state == "VERIFIED" and m.contradicting_failures == 1


def test_materially_contradicted_verification_needs_reverification() -> None:
    m = mastery(
        [
            *STRONG,
            check(days=5),
            ev("INDEPENDENT_APPLICATION", "INCORRECT", days=3),
            ev("TRANSFER", "INCORRECT", days=2),
        ]
    )
    assert m.state == "NEEDS_REVERIFICATION" and m.verification_standing == "CONTRADICTED"
    assert m.contradicting_failures == 2


def test_the_contradiction_rule_is_policy_configurable() -> None:
    records = [
        *STRONG,
        check(days=5),
        ev("INDEPENDENT_APPLICATION", "INCORRECT", days=3),
        ev("INDEPENDENT_APPLICATION", "INCORRECT", days=2),
    ]
    stricter = ReverificationPolicy.model_validate(
        {**ARCHITECTURE_POLICY["verification"]["reverification"], "min_contradicting_failures": 3}
    )
    assert mastery(records).state == "NEEDS_REVERIFICATION"
    assert mastery(records, reverification=stricter).state == "VERIFIED"


def test_a_single_failure_can_never_be_configured_to_contradict() -> None:
    with pytest.raises(ValueError, match="min_contradicting_failures"):
        ReverificationPolicy.model_validate(
            {
                **ARCHITECTURE_POLICY["verification"]["reverification"],
                "min_contradicting_failures": 1,
            }
        )
    with pytest.raises(ValueError, match="correct result"):
        ReverificationPolicy.model_validate(
            {
                **ARCHITECTURE_POLICY["verification"]["reverification"],
                "contradicting_outcome_signals": ["INCORRECT", "CORRECT"],
            }
        )


def test_partial_results_and_assisted_attempts_do_not_contradict() -> None:
    m = mastery(
        [
            *STRONG,
            check(days=5),
            ev("INDEPENDENT_APPLICATION", "PARTIAL", days=3),
            ev("ASSISTED_ATTEMPT", "INCORRECT", days=2, strength=0.1),
        ]
    )
    assert m.contradicting_failures == 0 and m.state == "VERIFIED"


def test_a_fresh_check_after_contradiction_resolves_reverification() -> None:
    contradicted = [
        *STRONG,
        check(days=5),
        ev("INDEPENDENT_APPLICATION", "INCORRECT", days=4),
        ev("INDEPENDENT_APPLICATION", "INCORRECT", days=3),
    ]
    rechecked = mastery([*contradicted, check(days=1)])
    # The recheck does not restore the VERIFIED gates (mean < 0.80): the regular gates apply.
    assert rechecked.state not in ("VERIFIED", "NEEDS_REVERIFICATION")
    assert rechecked.previously_verified and rechecked.verification_standing == "NONE"
    more = (ev(days=d) for d in (2.9, 2.7, 2.5, 2.3, 2.1, 1.9))
    strong_again = mastery([*contradicted, *more, check(days=1)])
    assert strong_again.state == "VERIFIED"


def test_a_partial_verification_is_not_a_pass() -> None:
    m = mastery([*STRONG, check("PARTIAL", days=5, strength=1.5)])
    assert m.state != "VERIFIED" and not m.recently_verified


def test_a_failed_verification_is_negative_evidence() -> None:
    m = mastery([check("INCORRECT", days=1)])
    assert m.beta > m.alpha and m.state == "EMERGING"


def test_verified_computation_is_order_independent_and_idempotent() -> None:
    records = [*STRONG, check(days=5), ev("INDEPENDENT_APPLICATION", "INCORRECT", days=3)]
    first = mastery(records)
    assert mastery(list(reversed(records))) == first
    assert mastery(records) == first
