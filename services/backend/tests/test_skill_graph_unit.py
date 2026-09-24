"""Skill graph engine, pure parts: canonical keys, proposal validation, bootstrap call."""

import pytest

from app.intelligence.skill_graph.bootstrap import (
    PROMPT_VERSION,
    CourseBrief,
    find_cycle,
    generate_course_graph,
    validate_proposal,
)
from app.intelligence.skill_graph.canonical import skill_key, slugify
from app.intelligence.skill_graph.embedding import embedding_content_hash, skill_embedding_text
from app.intelligence.skill_graph.schemas import GraphProposal
from app.model_gateway import ModelOutputInvalidError, ModelRunStatus, RunContext
from tests.fakes import FakeProvider, graph_proposal, make_gateway, policy, route_responder

COURSE = CourseBrief("Introduction to Python Programming", "Computer Science", "Beginner", None)
GRAPH_POLICY = policy().skill_graph
NAMES = [f"Python Competency {chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(30)]


@pytest.mark.parametrize(
    "variants",
    [
        ["Data Visualization", "data visualisation", "Data-Visualisations", "DATA  VISUALIZATION!"],
        ["For Loops", "for loop", "For-loops", "for_loops"],
        ["Modelling", "Modeling"],
        ["Organising Data", "organizing data"],
        ["Analysing Results", "Analyzing results"],
        ["Café Queries", "cafe query"],
        ["Classes & Objects", "class and object"],
        ["Libraries", "library"],
    ],
)
def test_spelling_and_case_variants_share_one_key(variants) -> None:
    assert len({skill_key(v) for v in variants}) == 1


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("C++", "C"),
        ("C#", "C"),
        ("For Loops", "While Loops"),
        ("Analysis", "Analyst"),
        ("Recursion", "Regression"),
    ],
)
def test_distinct_skills_keep_distinct_keys(a, b) -> None:
    assert skill_key(a) != skill_key(b)


def test_keys_are_stable_and_normalized() -> None:
    assert skill_key("  List   Comprehensions ") == "list comprehension"
    assert skill_key("Status") == "status" and skill_key("Process") == "process"
    assert slugify("C++ Pointers & References") == "cpp-pointers-and-references"
    assert slugify("!!!") == "skill"


def valid() -> GraphProposal:
    return GraphProposal.model_validate(graph_proposal(NAMES))


def test_valid_proposal_passes() -> None:
    validate_proposal(valid(), COURSE, GRAPH_POLICY)


def test_proposal_problems_are_all_reported() -> None:
    data = graph_proposal(NAMES)
    data["skills"][0]["topic_key"] = "t99"
    data["skills"][1]["prerequisite_keys"] = ["s1"]
    data["skills"][0]["prerequisite_keys"] = ["s2"]  # s1 <-> s2 cycle
    data["skills"][2]["canonical_name"] = "Python Competency AA"  # duplicate of s1
    data["skills"][3]["canonical_name"] = "Introduction to Python Programming"  # too broad
    data["skills"][4]["related_keys"] = ["s5"]  # self reference
    with pytest.raises(ValueError) as exc_info:
        validate_proposal(GraphProposal.model_validate(data), COURSE, GRAPH_POLICY)
    message = str(exc_info.value)
    for fragment in (
        "unknown topic_key",
        "cycle",
        "duplicates",
        "as broad as the course",
        "itself",
    ):
        assert fragment in message


def test_spelling_variant_duplicates_are_rejected() -> None:
    data = graph_proposal(NAMES)
    data["skills"][0]["canonical_name"] = "Data Visualization"
    data["skills"][1]["canonical_name"] = "Data Visualisation"
    with pytest.raises(ValueError, match="duplicates"):
        validate_proposal(GraphProposal.model_validate(data), COURSE, GRAPH_POLICY)


@pytest.mark.parametrize("count", [5, 120])
def test_skill_count_outside_hard_bounds_is_rejected(count) -> None:
    names = [f"Distinct Skill Number {i}" for i in range(count)]
    data = graph_proposal(names)
    with pytest.raises(ValueError, match="propose between 30 and 60"):
        validate_proposal(GraphProposal.model_validate(data), COURSE, GRAPH_POLICY)


def test_find_cycle() -> None:
    assert find_cycle({"a": ["b"], "b": ["c"]}) is None
    assert find_cycle({"a": ["b"], "b": ["c"], "c": ["a"]}) == ["a", "b", "c", "a"]


def test_bootstrap_repairs_an_invalid_graph_once() -> None:
    broken = graph_proposal(NAMES)
    broken["skills"][0]["prerequisite_keys"] = ["s2"]
    broken["skills"][1]["prerequisite_keys"] = ["s1"]
    provider = FakeProvider(route_responder(graph=[broken, graph_proposal(NAMES)]))
    gateway, recorder = make_gateway(provider)
    result = generate_course_graph(gateway, COURSE, GRAPH_POLICY, RunContext("job:x"))
    assert len(result.parsed.skills) == 30
    assert [r.status for r in recorder.runs] == [
        ModelRunStatus.INVALID_OUTPUT,
        ModelRunStatus.SUCCEEDED,
    ]
    assert all(r.prompt_version == PROMPT_VERSION for r in recorder.runs)
    assert "cycle" in provider.calls[1]["messages"][-1].content
    # Course fields are data inside the user message, never instructions in the system prompt.
    assert "Introduction to Python Programming" not in provider.calls[0]["system"]


def test_bootstrap_gives_up_after_one_repair() -> None:
    provider = FakeProvider(route_responder(graph=[{"topics": []}, {"topics": []}]))
    gateway, _ = make_gateway(provider)
    with pytest.raises(ModelOutputInvalidError):
        generate_course_graph(gateway, COURSE, GRAPH_POLICY, RunContext("job:x"))


def test_embedding_text_and_hash_ignore_alias_order() -> None:
    title, text = skill_embedding_text(
        "For Loops", "Write for loops.", ["iteration", "Looping", "For Loops"]
    )
    assert title == "For Loops"
    assert text == "For Loops. Write for loops. Also known as: Looping, iteration."
    _, reordered = skill_embedding_text("For Loops", "Write for loops.", ["Looping", "iteration"])
    assert embedding_content_hash("m", title, text) == embedding_content_hash("m", title, reordered)
    assert embedding_content_hash("m", title, text) != embedding_content_hash(
        "other-model", title, text
    )
