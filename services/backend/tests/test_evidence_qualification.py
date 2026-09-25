"""Deterministic evidence qualification (architecture §2.2, §9.6, Appendix B; ADR 0005).

Uncertainty abstains (never weak evidence); EXPOSURE/OBSERVATION never carry strength
or an outcome, whatever the model returned; strength follows Appendix B exactly."""

import pytest
from pydantic import ValidationError

from app.intelligence.contracts import AttributionItem
from app.intelligence.evidence.engine import (
    EVIDENCE_CREATED,
    copied_from_ai,
    difficulty_for,
    qualify_attribution,
    span_grounded,
)
from app.intelligence.policy import EvidencePolicy
from tests.fakes import ARCHITECTURE_POLICY, attribution, policy

POLICY = policy()
LEARNER = "My query: SELECT c.name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id"
SPAN = "LEFT JOIN orders o ON o.customer_id = c.id"


def item(actor="STUDENT", **fields) -> AttributionItem:
    fields.setdefault("student_span", SPAN)
    return AttributionItem.model_validate(attribution("s1", actor, **fields))


def qualify(it: AttributionItem, *, mapping_confidence=0.9, band=3, learner=LEARNER, prior=()):
    return qualify_attribution(
        it,
        mapping_confidence=mapping_confidence,
        difficulty_band=band,
        learner_text=learner,
        prior_assistant_texts=list(prior),
        attribution_policy=POLICY.attribution,
        evidence_policy=POLICY.evidence,
    )


def test_student_independent_application_follows_the_appendix_b_formula() -> None:
    q = qualify(item(confidence=0.85), mapping_confidence=0.95, band=5)
    e = q.evidence
    assert q.decision == EVIDENCE_CREATED
    assert (e.evidence_type, e.actor, e.outcome_signal, e.outcome) == (
        "INDEPENDENT_APPLICATION",
        "STUDENT",
        "CORRECT",
        1.0,
    )
    assert e.evidence_confidence == 0.85  # min(mapping 0.95, attribution 0.85)
    assert e.difficulty == 1.0 and e.difficulty_multiplier == pytest.approx(1.25)
    assert e.base_weight == 1.0 and e.independence == 1.0
    assert e.strength == pytest.approx(1.0 * 1.25 * 1.0 * 0.85)


@pytest.mark.parametrize(
    ("evidence_type", "base", "independence"),
    [
        ("ASSISTED_ATTEMPT", 0.35, 0.3),
        ("INDEPENDENT_EXPLANATION", 0.75, 0.8),
        ("INDEPENDENT_APPLICATION", 1.0, 1.0),
        ("TRANSFER", 1.25, 1.0),
    ],
)
def test_type_weights_and_independence_come_from_policy(evidence_type, base, independence) -> None:
    e = qualify(item(evidence_type=evidence_type), band=3).evidence
    assert (e.base_weight, e.independence, e.difficulty_multiplier) == (base, independence, 1.0)
    assert e.strength == pytest.approx(base * independence * 0.9)


@pytest.mark.parametrize("evidence_type", ["EXPOSURE", "OBSERVATION"])
@pytest.mark.parametrize("signal", ["CORRECT", "PARTIAL", "INCORRECT", "NOT_APPLICABLE"])
@pytest.mark.parametrize("actor", ["STUDENT", "AI", "SHARED"])
def test_exposure_and_observation_never_carry_strength_or_outcome(
    evidence_type, signal, actor
) -> None:
    q = qualify(item(actor, evidence_type=evidence_type, outcome=signal, confidence=0.99))
    e = q.evidence
    assert q.decision == EVIDENCE_CREATED
    assert e.evidence_type == evidence_type
    assert (e.strength, e.outcome, e.outcome_signal) == (0.0, None, "NOT_APPLICABLE")


def test_an_ai_actor_is_never_learner_performance() -> None:
    e = qualify(item("AI", evidence_type="INDEPENDENT_APPLICATION", outcome="CORRECT")).evidence
    assert (e.evidence_type, e.actor, e.strength, e.outcome) == ("OBSERVATION", "AI", 0.0, None)
    assert e.qualification_reason == "AI_ACTOR_NOT_PERFORMANCE"


def test_a_shared_actor_is_never_independent() -> None:
    e = qualify(item("SHARED", evidence_type="INDEPENDENT_APPLICATION")).evidence
    assert (e.evidence_type, e.independence) == ("ASSISTED_ATTEMPT", 0.3)
    assert e.qualification_reason == "SHARED_NOT_INDEPENDENT"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"actor": "UNKNOWN"}, "ACTOR_UNKNOWN"),
        ({"confidence": 0.79}, "LOW_ATTRIBUTION_CONFIDENCE"),
        ({"evidence_type": "OTHER"}, "EVIDENCE_TYPE_OTHER"),
        ({"outcome": "NOT_APPLICABLE"}, "OUTCOME_UNDETERMINED"),
        ({"student_span": None}, "STUDENT_SPAN_MISSING"),
        ({"student_span": "SELECT * FROM invented_table"}, "STUDENT_SPAN_NOT_GROUNDED"),
    ],
)
def test_uncertainty_abstains_instead_of_creating_weak_evidence(change, reason) -> None:
    fields = {"outcome": "INCORRECT", **change}
    actor = fields.pop("actor", "STUDENT")
    q = qualify(item(actor, **fields))
    assert q.decision == reason and q.evidence is None


def test_low_confidence_incorrect_attempt_is_not_negative_evidence() -> None:
    assert qualify(item(outcome="INCORRECT", confidence=0.6)).evidence is None


def test_an_incorrect_attempt_creates_negative_evidence() -> None:
    e = qualify(item(outcome="INCORRECT")).evidence
    assert e.outcome == 0.0 and e.strength > 0 and e.outcome_signal == "INCORRECT"


def test_partial_outcome_is_the_policy_value() -> None:
    assert qualify(item(outcome="PARTIAL")).evidence.outcome == 0.5


def test_learner_text_copied_from_earlier_ai_output_is_the_ai_s_work() -> None:
    prior = [f"You could write: {SPAN} and then group by c.name."]
    e = qualify(item(), prior=prior).evidence
    assert (e.actor, e.evidence_type, e.strength, e.outcome) == ("AI", "OBSERVATION", 0.0, None)
    assert e.qualification_reason == "COPIED_FROM_AI"


def test_a_short_common_phrase_is_not_treated_as_copied() -> None:
    learner = "I wrote LEFT JOIN myself"
    it = item(student_span="LEFT JOIN")
    assert qualify(it, learner=learner, prior=["use a LEFT JOIN here"]).evidence.actor == "STUDENT"


def test_span_matching_tolerates_case_quotes_whitespace_and_ellipsis() -> None:
    text = 'I think `LEFT JOIN`   keeps\nevery customer, even "without orders".'
    assert span_grounded("left join keeps every customer", text)
    assert span_grounded("LEFT JOIN ... without orders", text)
    assert not span_grounded("without orders ... LEFT JOIN", text)  # order matters
    assert not span_grounded("INNER JOIN", text)
    assert not span_grounded("...", text)
    assert copied_from_ai("keeps every customer, even without", [text], 24)
    assert not copied_from_ai("keeps every", [text], 24)


def test_copy_guard_matches_an_elided_span_piece_by_piece() -> None:
    reply = "Use this: SELECT c.name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id"
    # Before p8-v1 the ellipsis itself was searched for, so an elided copy was never detected.
    assert copied_from_ai("SELECT c.name FROM customers c ... ON o.customer_id = c.id", [reply], 24)
    assert not copied_from_ai("ON o.customer_id = c.id ... SELECT c.name", [reply], 24)  # order
    # Common fragments of the learner's own work: long enough together, none long on its own.
    assert not copied_from_ai("SELECT c ... FROM cust ... LEFT JOIN o ... ON o.cu", [reply], 24)


def test_difficulty_multiplier_range_and_default() -> None:
    evidence = POLICY.evidence
    assert [difficulty_for(b, evidence) for b in (1, 2, 3, 4, 5)] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert difficulty_for(None, evidence) == 0.5
    assert qualify(item(), band=1).evidence.difficulty_multiplier == pytest.approx(0.75)
    assert qualify(item(), band=None).evidence.difficulty_multiplier == pytest.approx(1.0)


def test_evidence_confidence_is_the_minimum_of_mapping_and_attribution() -> None:
    assert (
        qualify(item(confidence=0.95), mapping_confidence=0.82).evidence.evidence_confidence == 0.82
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"base_weights": {**ARCHITECTURE_POLICY["evidence"]["base_weights"], "EXPOSURE": 0.1}},
        {"independence": {**ARCHITECTURE_POLICY["evidence"]["independence"], "OBSERVATION": 0.2}},
        {"outcome_values": {"CORRECT": 0.9, "PARTIAL": 0.5, "INCORRECT": 0.0}},
        {"base_weights": {"EXPOSURE": 0.0}},
    ],
)
def test_policy_cannot_give_exposure_strength_or_redefine_outcomes(patch) -> None:
    with pytest.raises(ValidationError):
        EvidencePolicy.model_validate({**ARCHITECTURE_POLICY["evidence"], **patch})
