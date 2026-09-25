"""Engine 16 (recommendations) and the Explanation Service: deterministic rules, no model call."""

import random
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.experience.models import FeedbackRequest
from app.intelligence.explanation import (
    debt_band,
    debt_factors,
    factor_level,
    mastery_explanation_code,
    mastery_gates,
)
from app.intelligence.policy import DebtBands, RecommendationPolicy
from app.intelligence.recommendations.engine import (
    PrerequisiteSignal,
    SkillSignal,
    decide,
    recommend_all,
)
from tests.fakes import policy

POLICY = policy()
MASTERY_STATES = [
    "UNKNOWN",
    "EMERGING",
    "DEVELOPING",
    "DEMONSTRATED",
    "VERIFIED",
    "NEEDS_REVERIFICATION",
]


def sid(n: int) -> UUID:
    return UUID(int=n)


def signal(n: int = 1, state: str = "UNKNOWN", **overrides) -> SkillSignal:
    values = {
        "skill_id": sid(n),
        "mastery_state": state,
        "mastery_mean": None if state == "UNKNOWN" else 0.6,
        "support": 0.0 if state == "UNKNOWN" else 2.0,
        "importance": 0.5,
        "debt_eligible": False,
        "debt_score": 0.0,
        "performance_evidence_count": 0 if state == "UNKNOWN" else 3,
        "ledger_version": None if state == "UNKNOWN" else 1,
        "prerequisites": (),
    }
    values.update(overrides)
    return SkillSignal(**values)


def one(s: SkillSignal, **kw):
    return decide(s, verify=kw.pop("verify", True), verify_deferred=False, policy=POLICY)


# --- Rules ---------------------------------------------------------------------------------


def test_unknown_is_no_judgement_just_gather_more_evidence() -> None:
    d = one(signal(state="UNKNOWN"))
    assert (d.type, d.reason_code, d.priority) == ("NO_ACTION", "NOT_ENOUGH_EVIDENCE", 0)
    assert d.inputs["mastery_mean"] is None and d.related_skill_id is None


def test_emerging_and_developing_get_practice_when_evidence_supports_it() -> None:
    emerging = one(signal(state="EMERGING", mastery_mean=0.3))
    developing = one(signal(state="DEVELOPING", mastery_mean=0.6))
    assert (emerging.type, emerging.reason_code, emerging.priority) == (
        "PRACTICE",
        "EMERGING_NEEDS_PRACTICE",
        45,
    )
    assert (developing.type, developing.reason_code, developing.priority) == (
        "PRACTICE",
        "DEVELOPING_NEEDS_PRACTICE",
        35,
    )
    # Without performance evidence there is nothing to base practice on.
    assert one(signal(state="DEVELOPING", performance_evidence_count=0)).type == "NO_ACTION"


def test_demonstrated_needs_no_action() -> None:
    d = one(signal(state="DEMONSTRATED", mastery_mean=0.8, support=3.2))
    assert (d.type, d.reason_code) == ("NO_ACTION", "INDEPENDENT_EVIDENCE_SUFFICIENT")


def test_actionable_debt_is_a_verification_even_while_unknown() -> None:
    d = one(signal(state="UNKNOWN", debt_eligible=True, debt_score=20.0))
    assert (d.type, d.reason_code, d.priority) == ("VERIFY", "REPEATED_DELEGATION_UNVERIFIED", 64)
    assert d.inputs["debt_band"] == "MODERATE" and d.inputs["debt_actionable"] is True


def test_eligible_but_not_actionable_debt_is_not_a_verification() -> None:
    d = one(signal(state="UNKNOWN", debt_eligible=True, debt_score=9.0))
    assert d.type == "NO_ACTION" and d.inputs["debt_band"] == "LOW"
    # A score without eligibility never counts (AI use is not dependency).
    assert one(signal(state="UNKNOWN", debt_eligible=False, debt_score=50.0)).type == "NO_ACTION"


def test_prerequisite_gap_needs_a_demonstrated_weakness_never_unknown() -> None:
    weak = PrerequisiteSignal(sid(9), "EMERGING", 0.3)
    weaker = PrerequisiteSignal(sid(8), "EMERGING", 0.2)
    unknown = PrerequisiteSignal(sid(7), "UNKNOWN", None)
    d = one(signal(state="DEVELOPING", prerequisites=(weak, weaker, unknown)))
    assert (d.type, d.reason_code, d.related_skill_id, d.priority) == (
        "PREREQUISITE",
        "PREREQUISITE_GAP",
        sid(8),
        55,
    )
    assert d.inputs["prerequisite_gap"] == {"skill_id": str(sid(8)), "mastery_state": "EMERGING"}
    # Unknown is not weak: an UNKNOWN prerequisite is never a gap.
    assert one(signal(state="DEVELOPING", prerequisites=(unknown,))).type == "PRACTICE"
    # And an UNKNOWN skill gets no prerequisite judgement either.
    assert one(signal(state="UNKNOWN", prerequisites=(weak,))).type == "NO_ACTION"
    # A DEMONSTRATED skill with a weak prerequisite needs nothing.
    assert one(signal(state="DEMONSTRATED", prerequisites=(weak,))).type == "NO_ACTION"


def test_future_p6_states_are_already_supported() -> None:
    reverify = one(
        signal(state="NEEDS_REVERIFICATION", importance=0.8, debt_eligible=True, debt_score=40)
    )
    assert (reverify.type, reverify.reason_code, reverify.priority) == (
        "REVERIFY",
        "VERIFICATION_STALE",
        88,
    )
    verified = one(signal(state="VERIFIED", mastery_mean=0.9, support=4.5))
    assert (verified.type, verified.reason_code) == ("NO_ACTION", "RECENTLY_VERIFIED")


def test_at_most_max_active_verify_verifications_the_rest_fall_through() -> None:
    signals = [
        signal(1, "UNKNOWN", debt_eligible=True, debt_score=18.0),
        signal(2, "DEVELOPING", debt_eligible=True, debt_score=29.0),
        signal(3, "UNKNOWN", debt_eligible=True, debt_score=22.0),
        signal(4, "EMERGING", mastery_mean=0.3),
    ]
    decisions = {d.skill_id: d for d in recommend_all(signals, policy=POLICY)}
    assert decisions[sid(2)].type == "VERIFY" and decisions[sid(3)].type == "VERIFY"
    deferred = decisions[sid(1)]
    assert deferred.type == "NO_ACTION" and deferred.inputs["verify_deferred"] is True
    assert (
        decisions[sid(4)].type == "PRACTICE"
        and decisions[sid(4)].inputs["verify_deferred"] is False
    )
    none_allowed = policy(recommendations={"max_active_verify": 0})
    assert {d.type for d in recommend_all(signals, policy=none_allowed)} == {
        "NO_ACTION",
        "PRACTICE",
    }


def test_recommendations_are_deterministic() -> None:
    rng = random.Random(5)
    signals = [
        signal(
            n,
            state := rng.choice(MASTERY_STATES),
            mastery_mean=None if state == "UNKNOWN" else rng.random(),
            debt_eligible=(eligible := rng.random() < 0.4),
            debt_score=rng.uniform(0, 40) if eligible else 0.0,
            importance=rng.choice([0.3, 0.5, 0.9]),
            prerequisites=tuple(
                PrerequisiteSignal(sid(1000 + k), rng.choice(MASTERY_STATES), rng.random())
                for k in range(rng.randint(0, 2))
            ),
        )
        for n in range(1, 60)
    ]
    first = recommend_all(signals, policy=POLICY)
    shuffled = signals[:]
    rng.shuffle(shuffled)
    assert recommend_all(shuffled, policy=POLICY) == first


@pytest.mark.parametrize("seed", range(20))
def test_invariants_hold_for_random_ledgers(seed) -> None:
    rng = random.Random(seed)
    signals = []
    for n in range(1, 40):
        state = rng.choice(MASTERY_STATES)
        eligible = rng.random() < 0.5
        signals.append(
            signal(
                n,
                state,
                mastery_mean=None if state == "UNKNOWN" else rng.random(),
                performance_evidence_count=0 if state == "UNKNOWN" else rng.randint(0, 5),
                debt_eligible=eligible,
                debt_score=rng.uniform(0, 60) if eligible else 0.0,
                prerequisites=tuple(
                    PrerequisiteSignal(sid(500 + k), rng.choice(MASTERY_STATES), rng.random())
                    for k in range(rng.randint(0, 3))
                ),
            )
        )
    decisions = recommend_all(signals, policy=POLICY)
    assert sum(d.type == "VERIFY" for d in decisions) <= POLICY.recommendations.max_active_verify
    for d, s in zip(decisions, sorted(signals, key=lambda x: str(x.skill_id)), strict=True):
        if d.mastery_state == "UNKNOWN":
            assert d.type in ("NO_ACTION", "VERIFY")  # never practice/prerequisite: not weak
        if d.type == "VERIFY":
            assert s.debt_eligible and s.debt_score >= POLICY.debt.actionable_min_score
        if d.type == "NO_ACTION":
            assert d.priority == 0
        assert (d.type == "PREREQUISITE") == (d.related_skill_id is not None)
        if d.type == "REVERIFY":
            assert d.mastery_state == "NEEDS_REVERIFICATION"
        assert 0 <= d.priority <= 100


# --- Explanation Service -------------------------------------------------------------------


def test_debt_bands_are_qualitative_and_need_eligibility() -> None:
    bands = POLICY.recommendations.debt_bands
    assert debt_band(False, 80.0, bands) == "NONE"
    assert [debt_band(True, s, bands) for s in (0.0, 14.99, 15.0, 24.99, 25.0, 100.0)] == [
        "LOW",
        "LOW",
        "MODERATE",
        "MODERATE",
        "HIGH",
        "HIGH",
    ]
    assert [factor_level(v) for v in (0.0, 0.33, 0.34, 0.66, 0.67, 1.0)] == [
        "LOW",
        "LOW",
        "MODERATE",
        "MODERATE",
        "HIGH",
        "HIGH",
    ]


def test_debt_factors_only_for_eligible_debt() -> None:
    assert (
        debt_factors({"eligibility": "INSUFFICIENT_DELEGATION", "recent_delegation_count": 1}) == []
    )
    factors = debt_factors(
        {
            "eligibility": "ELIGIBLE",
            "delegation_pressure": 0.74,
            "evidence_gap": 1.0,
            "importance": 0.5,
            "confidence": 0.9,
            "verification_factor": 0.6,
            "verification": "UNVERIFIED",
        }
    )
    assert [(f["code"], f["level"]) for f in factors] == [
        ("DELEGATION_PRESSURE", "HIGH"),
        ("EVIDENCE_GAP", "HIGH"),
        ("IMPORTANCE", "MODERATE"),
        ("CONFIDENCE", "HIGH"),
        ("VERIFICATION", "MODERATE"),
    ]


@pytest.mark.parametrize(
    ("state", "kwargs", "code"),
    [
        ("UNKNOWN", {"evidence_count": 0, "performance_evidence_count": 0}, "NO_EVIDENCE"),
        (
            "UNKNOWN",
            {"evidence_count": 3, "performance_evidence_count": 0},
            "NO_INDEPENDENT_PERFORMANCE",
        ),
        ("UNKNOWN", {"evidence_count": 1, "performance_evidence_count": 1}, "NOT_ENOUGH_SUPPORT"),
        ("EMERGING", {"mastery_mean": 0.3}, "EARLY_DIFFICULTY"),
        ("DEVELOPING", {"mastery_mean": 0.6}, "MIXED_RESULTS"),
        ("DEVELOPING", {"mastery_mean": 0.8, "support": 2.0}, "NEEDS_MORE_EVIDENCE"),
        ("DEVELOPING", {"mastery_mean": 0.8, "support": 3.5}, "NEEDS_INDEPENDENT_APPLICATION"),
        ("DEMONSTRATED", {"has_application": True}, "INDEPENDENT_EVIDENCE_SUPPORTS"),
        ("VERIFIED", {"recently_verified": True, "support": 4.5}, "RECENT_VERIFICATION"),
        # Held (P6): entered through the gates at a recent check; a later isolated result
        # lowered the estimate below the entry gate without undoing the check.
        (
            "VERIFIED",
            {"recently_verified": True, "mastery_mean": 0.75, "support": 4.5},
            "VERIFICATION_HELD",
        ),
        ("NEEDS_REVERIFICATION", {"verification_standing": "STALE"}, "VERIFICATION_STALE"),
        (
            "NEEDS_REVERIFICATION",
            {"verification_standing": "CONTRADICTED"},
            "VERIFICATION_CONTRADICTED",
        ),
    ],
)
def test_mastery_explanation_codes(state, kwargs, code) -> None:
    values = {
        "evidence_count": 4,
        "performance_evidence_count": 4,
        "mastery_mean": 0.8,
        "support": 3.5,
        "has_application": False,
    } | kwargs
    assert mastery_explanation_code(state, policy=POLICY.mastery, **values) == code


def test_gates_never_judge_a_mean_while_unknown() -> None:
    unknown = mastery_gates(
        "UNKNOWN", mastery_mean=0.9, support=0.8, has_application=True, policy=POLICY.mastery
    )
    assert unknown == [{"code": "ENOUGH_EVIDENCE", "met": False, "current": 0.8, "required": 1.0}]
    known = mastery_gates(
        "DEVELOPING", mastery_mean=0.8, support=2.0, has_application=False, policy=POLICY.mastery
    )
    assert {g["code"]: g["met"] for g in known} == {
        "ENOUGH_EVIDENCE": True,
        "STRONG_RESULTS": True,
        "SUSTAINED_EVIDENCE": False,
        "INDEPENDENT_APPLICATION": False,
    }


# --- Policy and request contracts -----------------------------------------------------------


def test_recommendation_policy_refuses_unknown_as_a_gap_and_disordered_bands() -> None:
    with pytest.raises(ValidationError):
        RecommendationPolicy(
            max_active_verify=2,
            prerequisite_gap_states=("UNKNOWN",),
            debt_bands={"moderate_min": 15, "high_min": 25},
        )
    with pytest.raises(ValidationError):
        DebtBands(moderate_min=25, high_min=15)


@pytest.mark.parametrize(
    ("body", "ok"),
    [
        ({"action": "DONT_COUNT", "target_type": "EVIDENCE_EVENT"}, True),
        ({"action": "DONT_COUNT", "target_type": "ACTIVITY_SEGMENT"}, True),
        ({"action": "DONT_COUNT", "target_type": "SKILL"}, False),
        ({"action": "WRONG_SKILL", "target_type": "SKILL_MAPPING"}, True),
        ({"action": "WRONG_SKILL", "target_type": "ACTIVITY_SEGMENT"}, False),
        ({"action": "WRONG_SKILL", "target_type": "EVIDENCE_EVENT", "verdict": "AGREE"}, False),
        ({"action": "EVALUATION", "target_type": "RECOMMENDATION", "verdict": "UNCLEAR"}, True),
        ({"action": "EVALUATION", "target_type": "SKILL", "note": "why?"}, True),
        ({"action": "EVALUATION", "target_type": "SKILL"}, False),
        ({"action": "EVALUATION", "target_type": "SKILL", "note": "   "}, False),
        ({"action": "EVALUATION", "target_type": "SKILL", "note": "x" * 2001}, False),
        ({"action": "DONT_COUNT", "target_type": "EVIDENCE_EVENT", "extra": 1}, False),
    ],
)
def test_feedback_request_contract(body, ok) -> None:
    body = {"target_id": str(sid(1)), **body}
    if ok:
        FeedbackRequest.model_validate(body)
    else:
        with pytest.raises(ValidationError):
            FeedbackRequest.model_validate(body)
