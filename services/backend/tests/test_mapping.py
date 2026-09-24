"""P3A mapping: candidate-only mapper, confidence gate, adjudication, abstention (§9.4, §16)."""

from uuid import uuid4

import pytest

from app.intelligence.contracts import SkillCandidate
from app.intelligence.mapping.engine import map_segment
from app.intelligence.mapping.gate import gate
from app.model_gateway import RunContext
from tests.fakes import FakeProvider, candidate_ids_in, make_gateway, policy, route_responder

MAP_POLICY = policy().mapping
CTX = RunContext("job:map")


def cand(
    name: str, *, in_course: bool = True, parents: tuple = (), topic: str = "Loops"
) -> SkillCandidate:
    return SkillCandidate(
        skill_id=uuid4(),
        canonical_name=name,
        description=f"Demonstrate {name}.",
        node_kind="SUBSKILL" if parents else "SKILL",
        in_course=in_course,
        parent_ids=parents,
        topic_names=(topic,),
        semantic_similarity=0.8,
        lexical_score=0.5,
        course_context_prior=0.5 if in_course else 0.0,
        candidate_score=0.6,
        rank=1,
    )


LOOPS = cand("For Loops")
DICTS = cand("Dictionary Iteration")
REGRESSION_TESTING = cand("Regression Testing", in_course=False, topic="Software Testing")
CANDIDATES = [LOOPS, DICTS, REGRESSION_TESTING]


def mapping(skill: SkillCandidate, confidence: float, span: str = "loop over") -> dict:
    return {
        "skill_id": str(skill.skill_id),
        "confidence": confidence,
        "evidence_span": span,
        "reason_code": "CONCEPT_USE",
    }


def output(*mappings: dict, new=None) -> dict:
    return {"mappings": list(mappings), "new_skill_candidate": new}


def run(candidates=CANDIDATES, **responses):
    provider = FakeProvider(route_responder(**responses))
    gateway, recorder = make_gateway(provider)
    result = map_segment(
        gateway,
        segment_text="How do I loop over a list and a dict?",
        course_context="Introduction to Python Programming (Beginner)",
        candidates=candidates,
        policy=MAP_POLICY,
        context=CTX,
    )
    return result, provider, recorder


@pytest.mark.parametrize(
    ("confidence", "decision"),
    [
        (1.0, "ACCEPT"),
        (0.80, "ACCEPT"),
        (0.7999, "ADJUDICATE"),
        (0.65, "ADJUDICATE"),
        (0.6499, "ABSTAIN"),
        (0.0, "ABSTAIN"),
    ],
)
def test_gate_boundaries(confidence, decision) -> None:
    assert gate(confidence, MAP_POLICY) == decision


def test_confidence_080_is_accepted_without_second_pass() -> None:
    result, provider, recorder = run(mapping=output(mapping(LOOPS, 0.80)))
    assert result.outcome == "MAPPED" and result.abstain_reason is None
    (skill,) = result.skills
    assert (skill.skill_id, skill.status, skill.status_reason, skill.adjudicated) == (
        LOOPS.skill_id,
        "ACCEPTED",
        "FIRST_PASS_ACCEPTED",
        False,
    )
    assert len(provider.calls) == 1 and result.mapping_run_id == recorder.runs[0].id
    # The mapper was shown exactly the supplied candidates.
    assert candidate_ids_in(provider.calls[0]["messages"]) == [str(c.skill_id) for c in CANDIDATES]


def test_multiple_skills_can_be_mapped() -> None:
    result, _, _ = run(mapping=output(mapping(LOOPS, 0.9), mapping(DICTS, 0.85)))
    assert [s.status for s in result.skills] == ["ACCEPTED", "ACCEPTED"]


def test_band_confirmed_by_adjudication_is_accepted() -> None:
    result, provider, recorder = run(
        mapping=output(mapping(LOOPS, 0.65)),
        adjudication={
            "adjudications": [
                {
                    "skill_id": str(LOOPS.skill_id),
                    "verdict": "CONFIRM",
                    "confidence": 0.86,
                    "reason_code": "DIRECT_MATCH",
                }
            ]
        },
    )
    (skill,) = result.skills
    assert skill.status == "ACCEPTED" and skill.status_reason == "ADJUDICATION_CONFIRMED"
    assert skill.adjudicated and skill.first_pass_confidence == 0.65 and skill.confidence == 0.86
    assert result.adjudication_run_id == recorder.runs[-1].id
    assert recorder.runs[-1].prompt_version == "mapping-adjudication/v1"


@pytest.mark.parametrize(
    ("verdict", "confidence", "status", "reason"),
    [
        ("CONFIRM", 0.79, "ABSTAINED", "ADJUDICATION_UNRESOLVED"),
        ("UNRESOLVED", 0.5, "ABSTAINED", "ADJUDICATION_UNRESOLVED"),
        ("REJECT", 0.9, "REJECTED", "ADJUDICATION_REJECTED"),
    ],
)
def test_band_not_confirmed_is_not_accepted(verdict, confidence, status, reason) -> None:
    result, _, _ = run(
        mapping=output(mapping(LOOPS, 0.72)),
        adjudication={
            "adjudications": [
                {
                    "skill_id": str(LOOPS.skill_id),
                    "verdict": verdict,
                    "confidence": confidence,
                    "reason_code": "CHECKED",
                }
            ]
        },
    )
    assert result.outcome == "ABSTAINED"
    assert (result.skills[0].status, result.skills[0].status_reason) == (status, reason)


def test_invalid_adjudication_abstains() -> None:
    bad = {"adjudications": []}
    result, _, _ = run(mapping=output(mapping(LOOPS, 0.7)), adjudication=[bad, bad])
    assert result.outcome == "ABSTAINED" and result.abstain_reason == "ADJUDICATION_UNRESOLVED"
    assert result.skills[0].status == "ABSTAINED"


def test_below_065_abstains_without_second_pass() -> None:
    result, provider, _ = run(mapping=output(mapping(LOOPS, 0.64)))
    assert result.outcome == "ABSTAINED" and result.abstain_reason == "LOW_CONFIDENCE"
    assert result.skills[0].status == "ABSTAINED" and len(provider.calls) == 1


def test_mapper_cannot_return_an_id_outside_the_candidates() -> None:
    invented = {
        "skill_id": str(uuid4()),
        "confidence": 0.99,
        "evidence_span": "x",
        "reason_code": "OTHER",
    }
    result, provider, recorder = run(mapping=[output(invented), output(invented)])
    assert result.outcome == "ABSTAINED" and result.abstain_reason == "INVALID_MODEL_OUTPUT"
    assert result.skills == []
    assert "not in the candidate list" in provider.calls[1]["messages"][-1].content
    assert result.mapping_run_id == recorder.runs[-1].id


def test_out_of_set_id_is_repaired_once() -> None:
    invented = {
        "skill_id": str(uuid4()),
        "confidence": 0.99,
        "evidence_span": "x",
        "reason_code": "OTHER",
    }
    result, _, _ = run(mapping=[output(invented), output(mapping(LOOPS, 0.9))])
    assert result.outcome == "MAPPED" and result.skills[0].skill_id == LOOPS.skill_id


def test_unknown_concept_becomes_new_skill_candidate_not_a_skill() -> None:
    new = {
        "canonical_name": "Polars LazyFrame Queries",
        "parent_candidate_id": str(DICTS.skill_id),
        "description": "Build lazy query plans with the Polars library.",
    }
    result, _, _ = run(mapping=output(new=new))
    assert result.outcome == "ABSTAINED" and result.abstain_reason == "NO_MATCHING_CANDIDATE"
    assert result.skills == []
    assert result.new_skill_candidate.canonical_name == "Polars LazyFrame Queries"
    assert result.new_skill_candidate.parent_candidate_id == DICTS.skill_id


def test_new_skill_parent_must_be_a_candidate() -> None:
    new = {"canonical_name": "Polars", "parent_candidate_id": str(uuid4()), "description": ""}
    result, _, _ = run(mapping=[output(new=new), output(new=new)])
    assert result.abstain_reason == "INVALID_MODEL_OUTPUT" and result.new_skill_candidate is None


def test_no_candidates_abstains() -> None:
    result, provider, _ = run(candidates=[], mapping=output())
    assert result.outcome == "ABSTAINED" and result.abstain_reason == "NO_CANDIDATES"
    assert "Candidates: (none)" in provider.calls[0]["messages"][-1].content


def test_hierarchy_mismatch_is_rejected_by_adjudication() -> None:
    result, provider, _ = run(
        mapping=output(mapping(REGRESSION_TESTING, 0.7, "regression")),
        adjudication={
            "adjudications": [
                {
                    "skill_id": str(REGRESSION_TESTING.skill_id),
                    "verdict": "REJECT",
                    "confidence": 0.9,
                    "reason_code": "UNRELATED_DOMAIN",
                }
            ]
        },
    )
    assert result.skills[0].status == "REJECTED" and result.abstain_reason == "CANDIDATES_REJECTED"
    # The adjudicator sees the candidate's place in the hierarchy and its scope.
    assert "(global) [topic: Software Testing]" in provider.calls[1]["messages"][-1].content


def test_parent_is_dropped_when_its_child_skill_is_accepted() -> None:
    parent = cand("Iteration")
    child = cand("Iterating Over Dictionaries", parents=(parent.skill_id,))
    result, _, _ = run(
        candidates=[parent, child], mapping=output(mapping(parent, 0.9), mapping(child, 0.9))
    )
    by_id = {s.skill_id: s for s in result.skills}
    assert by_id[child.skill_id].status == "ACCEPTED"
    assert (by_id[parent.skill_id].status, by_id[parent.skill_id].status_reason) == (
        "REJECTED",
        "HIERARCHY_REDUNDANT",
    )
    assert result.outcome == "MAPPED"


def test_alias_worded_segment_maps_to_the_canonical_candidate() -> None:
    dataviz = cand("Data Visualization", topic="Plotting")
    result, _, _ = run(candidates=[dataviz], mapping=output(mapping(dataviz, 0.9, "my dataviz")))
    assert result.skills[0].skill_id == dataviz.skill_id and result.outcome == "MAPPED"
