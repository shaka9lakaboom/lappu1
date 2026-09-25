"""The deterministic verification planner, pure parts (architecture §11.1, Appendix B; ADR 0007).
The database behaviour (recommendation -> PLANNED session + job, idempotency) is in
test_verification_db.py."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.intelligence.verification.planner import (
    Candidate,
    SessionHistory,
    blocking_reason,
    learner_timezone,
    plan_difficulty,
    select_candidates,
)
from tests.fakes import policy

POLICY = policy()
NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


def candidate(priority=65, importance=0.5, skill=None, kind="VERIFY") -> Candidate:
    return Candidate(
        recommendation_id=uuid4(),
        skill_id=skill or uuid4(),
        type=kind,
        reason_code="REPEATED_DELEGATION_UNVERIFIED",
        priority=priority,
        mastery_state="UNKNOWN",
        debt_band="MODERATE",
        difficulty_band=3,
        course_id=uuid4(),
        importance=importance,
    )


def history(state, *, days=0.0, passed=None, failure=None) -> SessionHistory:
    at = NOW - timedelta(days=days)
    return SessionHistory(
        skill_id=uuid4(),
        state=state,
        failure_code=failure,
        failed_at=at if failure else None,
        evaluated_at=at if state == "EVALUATED" else None,
        abandoned_at=at if state == "ABANDONED" else None,
        passed=passed,
    )


def test_the_daily_budget_and_order() -> None:
    low, high, tie_a, tie_b = candidate(61), candidate(79), candidate(70, 0.9), candidate(70, 0.4)
    chosen = select_candidates([low, high, tie_a, tie_b], blocked={}, remaining=2)
    assert chosen == [high, tie_a]  # priority first, then course importance
    assert select_candidates([low, high], blocked={}, remaining=0) == []


def test_blocked_skills_are_skipped_not_counted() -> None:
    a, b, c = candidate(80), candidate(70), candidate(60)
    chosen = select_candidates([a, b, c], blocked={a.skill_id: "ACTIVE_SESSION"}, remaining=2)
    assert chosen == [b, c]


@pytest.mark.parametrize(
    ("sessions", "reason"),
    [
        ([], None),
        ([history("READY")], "ACTIVE_SESSION"),
        ([history("SUBMITTED")], "ACTIVE_SESSION"),
        ([history("EVALUATED", days=2, passed=True)], "COOLDOWN_AFTER_PASS"),
        ([history("EVALUATED", days=8, passed=True)], None),
        ([history("EVALUATED", days=0.5, passed=False)], "COOLDOWN_AFTER_FAIL"),
        ([history("EVALUATED", days=2, passed=False)], None),
        ([history("ABANDONED", days=0.5)], "COOLDOWN_AFTER_ABANDON"),
        ([history("PLANNED", days=0.5, failure="GENERATION_REJECTED")], "RETRY_AFTER_FAILURE"),
        ([history("PLANNED", days=2, failure="GENERATION_REJECTED")], None),
        ([history("SUBMITTED", days=2, failure="EVALUATION_NEEDS_REVIEW")], None),
    ],
)
def test_active_sessions_and_cooldowns_block_a_skill(sessions, reason) -> None:
    assert blocking_reason(sessions, now=NOW, policy=POLICY) == reason


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Europe/Helsinki", "Europe/Helsinki"), ("UTC", "UTC"), ("Not/AZone", "UTC"), (None, "UTC")],
)
def test_the_learner_day_uses_a_valid_profile_timezone_else_utc(name, expected) -> None:
    assert learner_timezone(name)[1] == expected


def test_the_planned_difficulty_band_follows_the_skill_band_within_bounds() -> None:
    evidence, bounds = POLICY.evidence, POLICY.verification.difficulty
    assert plan_difficulty(3, evidence=evidence, policy=bounds) == (0.5, 0.35, 0.65)
    assert plan_difficulty(None, evidence=evidence, policy=bounds) == (0.5, 0.35, 0.65)
    assert plan_difficulty(1, evidence=evidence, policy=bounds) == (0.1, 0.1, 0.25)
    assert plan_difficulty(5, evidence=evidence, policy=bounds) == (0.9, 0.75, 0.9)


def test_the_policy_refuses_code_and_sql_and_keeps_the_budget_bounded() -> None:
    from app.intelligence.policy import VerificationGenerationPolicy
    from tests.fakes import ARCHITECTURE_POLICY

    base = ARCHITECTURE_POLICY["verification"]["generation"]
    with pytest.raises(ValueError, match="sandbox"):
        VerificationGenerationPolicy.model_validate(
            {**base, "supported_assessment_types": ["mcq", "code"]}
        )
    assert POLICY.verification.planner.max_daily_unsolicited == 2  # Appendix B
    assert POLICY.verification.generation.max_attempts == 3  # initial + 2 regenerations (§11.3)
