"""Mastery model (architecture §10.2, §10.3; ADR 0005): weighted Beta evidence, recency,
UNKNOWN before any mean threshold, VERIFIED / NEEDS_REVERIFICATION unreachable before P6."""

import math
import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.intelligence.mastery.engine import (
    VERIFICATION_SOURCES,
    EvidenceRecord,
    classify_state,
    compute_mastery,
)
from app.intelligence.policy import MasteryPolicy
from tests.fakes import ARCHITECTURE_POLICY, policy

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
MASTERY = policy().mastery
SKILL = uuid4()


def record(
    evidence_type="INDEPENDENT_APPLICATION",
    outcome_signal="CORRECT",
    *,
    strength=0.9,
    days=0.0,
    actor="STUDENT",
    source_type="AI_ACTIVITY",
    excluded=False,
    confidence=0.9,
) -> EvidenceRecord:
    outcome = {"CORRECT": 1.0, "PARTIAL": 0.5, "INCORRECT": 0.0, "NOT_APPLICABLE": None}[
        outcome_signal
    ]
    if evidence_type in ("EXPOSURE", "OBSERVATION"):
        outcome, outcome_signal, strength = None, "NOT_APPLICABLE", 0.0
    return EvidenceRecord(
        id=uuid4(),
        skill_id=SKILL,
        source_type=source_type,
        evidence_type=evidence_type,
        actor=actor,
        outcome_signal=outcome_signal,
        outcome=outcome,
        strength=strength,
        evidence_confidence=confidence,
        occurred_at=NOW - timedelta(days=days),
        excluded=excluded,
        rationale_code="STUDENT_WROTE_CODE",
        learning_relevance="high",
        mapping_status="ACCEPTED",
    )


def test_no_evidence_is_unknown_with_the_neutral_prior() -> None:
    m = compute_mastery([], MASTERY, NOW)
    assert (m.state, m.alpha, m.beta, m.mastery_mean, m.support) == ("UNKNOWN", 1.0, 1.0, 0.5, 0.0)
    assert m.evidence_count == 0 and m.last_evidence_at is None


def test_bayesian_alpha_beta_math() -> None:
    records = [
        record(strength=0.9),
        record(strength=0.6),
        record("INDEPENDENT_APPLICATION", "INCORRECT", strength=0.5),
    ]
    m = compute_mastery(records, MASTERY, NOW)
    assert m.alpha == pytest.approx(1 + 0.9 + 0.6)
    assert m.beta == pytest.approx(1 + 0.5)
    assert m.support == pytest.approx(2.0)
    assert m.mastery_mean == pytest.approx(2.5 / 4.0)
    assert m.performance_evidence_count == 3


def test_partial_result_splits_the_weight() -> None:
    m = compute_mastery([record(outcome_signal="PARTIAL", strength=1.0)], MASTERY, NOW)
    assert (m.alpha, m.beta, m.support) == (
        pytest.approx(1.5),
        pytest.approx(1.5),
        pytest.approx(1.0),
    )


def test_incorrect_result_only_raises_beta() -> None:
    m = compute_mastery([record(outcome_signal="INCORRECT", strength=1.2)], MASTERY, NOW)
    assert (m.alpha, m.beta) == (1.0, pytest.approx(2.2))
    assert m.state == "EMERGING"  # real, supported negative evidence: not UNKNOWN, but not hidden


def test_recency_halves_the_weight_every_half_life() -> None:
    fresh = compute_mastery([record(strength=1.0)], MASTERY, NOW)
    old = compute_mastery([record(strength=1.0, days=180)], MASTERY, NOW)
    older = compute_mastery([record(strength=1.0, days=360)], MASTERY, NOW)
    assert fresh.support == pytest.approx(1.0)
    assert old.support == pytest.approx(0.5)
    assert older.support == pytest.approx(0.25)
    assert old.alpha == pytest.approx(1.5)
    assert math.isclose(
        compute_mastery([record(strength=1.0, days=30)], MASTERY, NOW).support,
        math.exp(-math.log(2) * 30 / 180),
    )


def test_unknown_is_checked_before_every_mean_threshold() -> None:
    for mean in (0.01, 0.3, 0.6, 0.95, 0.999):
        assert (
            classify_state(
                mean,
                0.99,
                has_application=True,
                recently_verified=True,
                previously_verified=True,
                policy=MASTERY,
            )
            == "UNKNOWN"
        )


def test_strong_mean_with_insufficient_support_remains_unknown() -> None:
    # A strong prior makes the mean look excellent, but support is below the gate.
    confident = MasteryPolicy.model_validate(
        {**ARCHITECTURE_POLICY["mastery"], "prior_alpha": 20.0}
    )
    m = compute_mastery([record(strength=0.9)], confident, NOW)
    assert m.mastery_mean > 0.9 and m.support < 1.0
    assert m.state == "UNKNOWN"


def test_one_lucky_correct_answer_is_small_positive_evidence_only() -> None:
    m = compute_mastery([record(strength=0.9)], MASTERY, NOW)
    assert m.alpha > 1.0 and m.state == "UNKNOWN"


@pytest.mark.parametrize(
    ("records", "state"),
    [
        (
            [record(strength=1.0)] * 2 + [record(outcome_signal="INCORRECT", strength=1.0)],
            "DEVELOPING",
        ),
        ([record(outcome_signal="INCORRECT", strength=1.0)] * 3, "EMERGING"),
        ([record(strength=1.0)] * 4, "DEMONSTRATED"),
        # a high mean without an independent application is not DEMONSTRATED
        ([record("INDEPENDENT_EXPLANATION", strength=1.0)] * 5, "DEVELOPING"),
        # enough support but not enough mean
        (
            [record(strength=1.0)] * 3 + [record(outcome_signal="INCORRECT", strength=1.0)] * 1,
            "DEVELOPING",
        ),
    ],
)
def test_state_gates(records, state) -> None:
    assert compute_mastery(records, MASTERY, NOW).state == state


def test_an_isolated_failure_does_not_erase_prior_evidence() -> None:
    history = [record(strength=0.9, days=d) for d in (40, 30, 20, 10, 5)]
    before = compute_mastery(history, MASTERY, NOW)
    after = compute_mastery(
        [*history, record(outcome_signal="INCORRECT", strength=0.9)], MASTERY, NOW
    )
    assert before.state == after.state == "DEMONSTRATED"
    assert after.mastery_mean < before.mastery_mean  # it is recorded, not hidden
    assert after.evidence_count == 6 and after.alpha == pytest.approx(before.alpha)


def test_exposure_and_observation_never_change_mastery() -> None:
    passive = [record("EXPOSURE", actor="AI") for _ in range(20)] + [
        record("OBSERVATION", actor="AI") for _ in range(20)
    ]
    m = compute_mastery(passive, MASTERY, NOW)
    assert (m.alpha, m.beta, m.support, m.state) == (1.0, 1.0, 0.0, "UNKNOWN")
    assert m.evidence_count == 40 and m.performance_evidence_count == 0


def test_excluded_evidence_is_ignored() -> None:
    m = compute_mastery([record(strength=1.0, excluded=True)] * 5, MASTERY, NOW)
    assert (m.support, m.evidence_count, m.state) == (0.0, 0, "UNKNOWN")


def test_recomputation_is_idempotent_and_order_independent() -> None:
    records = [record(strength=random.Random(i).uniform(0.1, 1.2), days=i * 7) for i in range(12)]
    first = compute_mastery(records, MASTERY, NOW)
    assert compute_mastery(records, MASTERY, NOW) == first
    shuffled = records[:]
    random.Random(3).shuffle(shuffled)
    again = compute_mastery(shuffled, MASTERY, NOW)
    assert again.state == first.state
    assert again.alpha == pytest.approx(first.alpha) and again.support == pytest.approx(
        first.support
    )


def test_no_verification_source_exists_before_p6() -> None:
    assert VERIFICATION_SOURCES == frozenset()


@pytest.mark.parametrize("seed", range(40))
def test_verified_and_needs_reverification_are_unreachable_before_p6(seed) -> None:
    rng = random.Random(seed)
    types = [
        "EXPOSURE",
        "OBSERVATION",
        "ASSISTED_ATTEMPT",
        "INDEPENDENT_EXPLANATION",
        "INDEPENDENT_APPLICATION",
        "TRANSFER",
        "VERIFICATION",
        "EXECUTION_RESULT",
    ]
    records = [
        record(
            rng.choice(types),
            rng.choice(["CORRECT", "CORRECT", "PARTIAL", "INCORRECT"]),
            strength=rng.uniform(0.5, 3.0),
            days=rng.uniform(0, 400),
            # Even evidence claiming to come from a verification source cannot verify yet.
            source_type=rng.choice(["AI_ACTIVITY", "VERIFICATION", "ASSESSMENT", "TEACHER"]),
        )
        for _ in range(rng.randint(0, 40))
    ]
    state = compute_mastery(records, MASTERY, NOW).state
    assert state not in ("VERIFIED", "NEEDS_REVERIFICATION")


def test_perfect_transfer_evidence_is_at_most_demonstrated() -> None:
    records = [record("TRANSFER", strength=1.25) for _ in range(30)] + [
        record("VERIFICATION", strength=1.5, source_type="VERIFICATION") for _ in range(30)
    ]
    m = compute_mastery(records, MASTERY, NOW)
    assert m.mastery_mean > 0.95 and m.support > 40
    assert m.state == "DEMONSTRATED" and not m.recently_verified and not m.previously_verified
