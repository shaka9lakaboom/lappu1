"""Retrieval scoring (deterministic) and the top-20 -> top-8 rerank (architecture §9.3)."""

from uuid import UUID, uuid4

import pytest

from app.intelligence.policy import IntelligencePolicy, RetrievalWeights
from app.intelligence.retrieval.engine import to_contract
from app.intelligence.retrieval.rerank import rerank_candidates
from app.intelligence.retrieval.scoring import RawCandidate, candidate_score, score_candidates
from app.model_gateway import RunContext
from tests.fakes import ARCHITECTURE_POLICY, FakeProvider, make_gateway, policy, route_responder

WEIGHTS = policy().retrieval.weights


def raw(
    name: str, lexical: float, semantic: float | None, importance: float | None
) -> RawCandidate:
    return RawCandidate(
        skill_id=uuid4(),
        canonical_name=name,
        description=f"{name} description",
        node_kind="SKILL",
        lexical_raw=lexical,
        semantic=semantic,
        course_importance=importance,
    )


def test_score_formula_uses_policy_weights() -> None:
    assert candidate_score(0.8, 0.5, 1.0, WEIGHTS) == pytest.approx(
        0.55 * 0.8 + 0.30 * 0.5 + 0.15 * 1.0
    )
    assert candidate_score(1, 1, 1, WEIGHTS) == pytest.approx(1.0)
    assert candidate_score(0, 0, 0, WEIGHTS) == 0.0


def test_inputs_are_clamped() -> None:
    assert candidate_score(1.7, -3, 2, WEIGHTS) == pytest.approx(0.55 + 0.15)
    assert candidate_score(float("nan"), 0.0, 0.0, WEIGHTS) == 0.0


def test_other_weights_change_the_score() -> None:
    lexical_only = RetrievalWeights(semantic=0.0, lexical=1.0, course_prior=0.0)
    assert candidate_score(0.9, 0.2, 1.0, lexical_only) == pytest.approx(0.2)


def test_weights_must_sum_to_one() -> None:
    data = {**ARCHITECTURE_POLICY, "retrieval": {**ARCHITECTURE_POLICY["retrieval"]}}
    data["retrieval"]["weights"] = {"semantic": 0.6, "lexical": 0.3, "course_prior": 0.15}
    with pytest.raises(ValueError, match="sum to 1"):
        IntelligencePolicy.model_validate(data)


def test_lexical_score_is_relative_to_best_match() -> None:
    a, b, c = raw("A", 0.4, None, None), raw("B", 0.1, None, None), raw("C", 0.0, None, None)
    scored = {s.raw.canonical_name: s for s in score_candidates([a, b, c], WEIGHTS, 20)}
    assert scored["A"].lexical_score == 1.0
    assert scored["B"].lexical_score == pytest.approx(0.25)
    assert scored["C"].lexical_score == 0.0
    assert score_candidates([c], WEIGHTS, 20)[0].lexical_score == 0.0  # no lexical match at all


def test_course_prior_prefers_course_skills() -> None:
    in_course = raw("Course Skill", 0.2, 0.7, 0.5)
    global_twin = raw("Global Skill", 0.2, 0.7, None)
    top = score_candidates([global_twin, in_course], WEIGHTS, 20)
    assert [s.raw.canonical_name for s in top] == ["Course Skill", "Global Skill"]
    assert top[0].course_context_prior == 0.5 and top[1].course_context_prior == 0.0
    assert top[0].candidate_score - top[1].candidate_score == pytest.approx(0.15 * 0.5)
    # A clearly better global match still wins over a weak course skill.
    strong_global = raw("Strong Global", 0.2, 0.95, None)
    weak_course = raw("Weak Course", 0.0, 0.3, 0.5)
    assert (
        score_candidates([weak_course, strong_global], WEIGHTS, 20)[0].raw.canonical_name
        == "Strong Global"
    )


def test_semantic_channel_alone_can_rank() -> None:
    semantic_hit = raw("Semantic Hit", 0.0, 0.9, None)
    lexical_hit = raw("Lexical Hit", 0.3, 0.1, None)
    top = score_candidates([lexical_hit, semantic_hit], WEIGHTS, 20)
    assert top[0].raw.canonical_name == "Semantic Hit"


def test_pool_is_cut_to_top_20_deterministically() -> None:
    candidates = [raw(f"Skill {i:02d}", 0.1 * (i % 7), 0.02 * i, None) for i in range(30)]
    first = score_candidates(candidates, WEIGHTS, 20)
    second = score_candidates(list(reversed(candidates)), WEIGHTS, 20)
    assert len(first) == 20
    assert [s.raw.skill_id for s in first] == [s.raw.skill_id for s in second]
    assert all(
        a.candidate_score >= b.candidate_score for a, b in zip(first, first[1:], strict=False)
    )


def pool(n: int):
    return to_contract(
        score_candidates(
            [raw(f"Skill {i:02d}", 0.0, 0.9 - i * 0.01, None) for i in range(n)], WEIGHTS, 20
        )
    )


def test_small_pool_skips_the_rerank_call() -> None:
    candidates = pool(6)
    provider = FakeProvider()
    gateway, recorder = make_gateway(provider)
    ids, run_id, fallback = rerank_candidates(
        gateway,
        segment_text="s",
        course_context="c",
        candidates=candidates,
        rerank_size=8,
        context=RunContext("t"),
    )
    assert ids == [c.skill_id for c in candidates] and run_id is None and not fallback
    assert recorder.runs == []


def test_rerank_returns_top_8_from_the_pool_in_model_order() -> None:
    candidates = pool(20)
    chosen = [str(candidates[i].skill_id) for i in (19, 3, 7)]
    provider = FakeProvider(route_responder(rerank={"ranked_skill_ids": chosen}))
    gateway, recorder = make_gateway(provider)
    ids, run_id, fallback = rerank_candidates(
        gateway,
        segment_text="s",
        course_context="c",
        candidates=candidates,
        rerank_size=8,
        context=RunContext("t"),
    )
    assert len(ids) == 8 and not fallback and run_id == recorder.runs[-1].id
    assert [str(i) for i in ids[:3]] == chosen
    # Remaining slots are filled in candidate-score order, without repeats.
    expected_fill = [c.skill_id for c in candidates if str(c.skill_id) not in chosen][:5]
    assert ids[3:] == expected_fill
    assert set(ids) <= {c.skill_id for c in candidates}


def test_rerank_cannot_add_ids_and_falls_back_to_score_order() -> None:
    candidates = pool(20)
    invented = {"ranked_skill_ids": [str(uuid4())]}
    provider = FakeProvider(route_responder(rerank=[invented, invented]))
    gateway, recorder = make_gateway(provider)
    ids, run_id, fallback = rerank_candidates(
        gateway,
        segment_text="s",
        course_context="c",
        candidates=candidates,
        rerank_size=8,
        context=RunContext("t"),
    )
    assert fallback and run_id is None
    assert ids == [c.skill_id for c in candidates[:8]]
    assert len(recorder.runs) == 2
    assert isinstance(ids[0], UUID)
