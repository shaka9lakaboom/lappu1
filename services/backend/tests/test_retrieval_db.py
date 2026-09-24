"""Two-stage lexical + pgvector retrieval against PostgreSQL (architecture §9.3)."""

from uuid import UUID

import pytest

from app.courses.models import CourseCreateRequest
from app.courses.service import create_course
from app.intelligence.retrieval.engine import fetch_raw_candidates, retrieve_candidates
from app.intelligence.skill_graph.canonical import skill_key, slugify
from app.intelligence.skill_graph.embedding import skill_embedding_text, vector_literal
from app.model_gateway import RunContext
from tests.fakes import (
    FakeProvider,
    candidate_ids_in,
    hash_embedding,
    make_gateway,
    policy,
    route_responder,
)

pytestmark = pytest.mark.db

MODEL = "gemini-embedding-2"
RETRIEVAL = policy().retrieval


def add_skill(
    conn,
    name: str,
    description: str,
    *,
    kind: str = "SKILL",
    status: str = "ACTIVE",
    aliases: tuple[str, ...] = (),
    vector: list[float] | None = None,
) -> UUID:
    (skill_id,) = conn.execute(
        """insert into public.skill_nodes (slug, canonical_name, normalized_name, description, node_kind,
                                           status, source, merged_into_id)
           values (%s, %s, %s, %s, %s::public.skill_node_kind, 'ACTIVE', 'SEED', null) returning id""",
        (slugify(name), name, skill_key(name), description, kind),
    ).fetchone()
    for alias in aliases:
        conn.execute(
            "insert into public.skill_aliases (skill_id, alias, normalized_alias, source) values (%s, %s, %s, 'SEED')",
            (skill_id, alias, skill_key(alias)),
        )
    if status != "ACTIVE":
        conn.execute(
            "update public.skill_nodes set status = %s::public.skill_status where id = %s",
            (status, skill_id),
        )
    _, text = skill_embedding_text(name, description, list(aliases))
    conn.execute(
        """insert into public.skill_embeddings (skill_id, model, graph_version, content_hash, input_version, embedding)
           values (%s, %s, 1, repeat('0', 64), 'skill-embedding-text/v1', %s::extensions.vector)""",
        (skill_id, MODEL, vector_literal(vector or hash_embedding(text))),
    )
    return skill_id


def add_course(conn, learner: UUID, skills: dict[UUID, float]) -> UUID:
    course = create_course(conn, learner, CourseCreateRequest(name="Retrieval course"), None).course
    for skill_id, importance in skills.items():
        conn.execute(
            "insert into public.course_skills (course_id, skill_id, importance, source, graph_version) "
            "values (%s, %s, %s, 'SEED', 1)",
            (course.id, skill_id, importance),
        )
    return course.id


def by_name(candidates) -> dict[str, object]:
    return {c.canonical_name: c for c in candidates}


def test_lexical_channel_uses_full_text_search_and_aliases(db_pool, registry, new_learner) -> None:
    with db_pool.connection() as conn:
        add_skill(conn, f"{registry}List Comprehensions", "Build lists with comprehension syntax.")
        add_skill(
            conn,
            f"{registry}Data Visualization",
            "Create charts from data.",
            aliases=(f"{registry}Dataviz",),
        )
        raw = fetch_raw_candidates(
            conn,
            query_text=f"{registry.strip()} how does comprehension syntax work",
            query_vector=None,
            course_ids=[],
            embedding_model=MODEL,
            channel_limit=40,
        )
        found = {c.canonical_name: c for c in raw}
        assert f"{registry}List Comprehensions" in found
        assert (
            found[f"{registry}List Comprehensions"].lexical_raw
            > found[f"{registry}Data Visualization"].lexical_raw
        )

        via_alias = fetch_raw_candidates(
            conn,
            query_text=f"help with my {registry.strip()}dataviz homework".replace(
                registry.strip(), registry.strip() + " "
            ),
            query_vector=None,
            course_ids=[],
            embedding_model=MODEL,
            channel_limit=40,
        )
        assert any(
            c.canonical_name == f"{registry}Data Visualization" and c.lexical_raw > 0
            for c in via_alias
        )


def test_vector_channel_finds_semantic_matches_without_shared_words(
    db_pool, registry, new_learner
) -> None:
    target = [0.0] * 768
    target[5] = 1.0
    with db_pool.connection() as conn:
        add_skill(conn, f"{registry}Semantic Target", "Unrelated wording entirely.", vector=target)
        raw = fetch_raw_candidates(
            conn,
            query_text="xylophone quantum banana",
            query_vector=target,
            course_ids=[],
            embedding_model=MODEL,
            channel_limit=40,
        )
    hit = next(c for c in raw if c.canonical_name == f"{registry}Semantic Target")
    assert hit.semantic == pytest.approx(1.0, abs=1e-5)
    assert hit.lexical_raw == 0.0


def test_course_skills_first_then_global_registry(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    with db_pool.connection() as conn:
        course_skill = add_skill(
            conn, f"{registry}Recursive Functions", "Write recursive functions with base cases."
        )
        add_skill(
            conn, f"{registry}Recursive Algorithms", "Write recursive functions with base cases."
        )
        course_id = add_course(conn, learner, {course_skill: 0.9})
        gateway, recorder = make_gateway(
            FakeProvider(
                route_responder(rerank=lambda m: {"ranked_skill_ids": candidate_ids_in(m)[:8]})
            )
        )
        result = retrieve_candidates(
            conn,
            gateway,
            query_text=f"{registry.strip()} write recursive functions with base cases",
            course_ids=[course_id],
            course_context="Retrieval course",
            policy=RETRIEVAL,
            context=RunContext("t"),
        )
    found = by_name(result.candidates)
    in_course, global_ = (
        found[f"{registry}Recursive Functions"],
        found[f"{registry}Recursive Algorithms"],
    )
    assert in_course.in_course and not global_.in_course
    assert (
        in_course.course_context_prior == pytest.approx(0.9) and global_.course_context_prior == 0.0
    )
    assert in_course.rank < global_.rank
    assert in_course.candidate_score == pytest.approx(
        0.55 * in_course.semantic_similarity + 0.30 * in_course.lexical_score + 0.15 * 0.9, abs=1e-5
    )
    assert result.course_ids == (course_id,)
    assert (
        recorder.runs[0].task_type == "EMBED_QUERY"
        and recorder.runs[0].prompt_version == "retrieval-query/v1"
    )
    assert result.query_model_run_id == recorder.runs[0].id


def test_pool_is_top_20_and_mapper_gets_reranked_top_8(db_pool, registry, new_learner) -> None:
    with db_pool.connection() as conn:
        ids = [
            add_skill(conn, f"{registry}Loop Pattern {i:02d}", f"Apply loop pattern number {i}.")
            for i in range(30)
        ]
        seen: list[list[str]] = []

        def rerank(messages):
            shown = candidate_ids_in(messages)
            seen.append(shown)
            return {"ranked_skill_ids": list(reversed(shown))[:8]}

        gateway, recorder = make_gateway(FakeProvider(route_responder(rerank=rerank)))
        result = retrieve_candidates(
            conn,
            gateway,
            query_text=f"{registry.strip()} loop pattern",
            course_ids=[],
            course_context="none",
            policy=RETRIEVAL,
            context=RunContext("t"),
        )
    assert len(result.candidates) == 20
    assert [c.rank for c in result.candidates] == list(range(1, 21))
    assert len(result.reranked_ids) == 8 and not result.rerank_fallback
    assert set(result.reranked_ids) <= {c.skill_id for c in result.candidates}
    assert [str(i) for i in result.reranked_ids] == list(reversed(seen[0]))[:8]
    assert len(seen[0]) == 20  # the reranker saw exactly the pool
    assert result.rerank_model_run_id == recorder.runs[-1].id
    assert set(ids) >= {c.skill_id for c in result.candidates}


def test_only_active_assessable_skills_are_candidates(db_pool, registry, new_learner) -> None:
    with db_pool.connection() as conn:
        active = add_skill(conn, f"{registry}Visible Skill", "Tokenize strings with split.")
        topic = add_skill(
            conn, f"{registry}Visible Topic", "Tokenize strings with split.", kind="TOPIC"
        )
        candidate = add_skill(
            conn, f"{registry}Hidden Candidate", "Tokenize strings with split.", status="CANDIDATE"
        )
        deprecated = add_skill(
            conn, f"{registry}Old Skill", "Tokenize strings with split.", status="DEPRECATED"
        )
        raw = fetch_raw_candidates(
            conn,
            query_text=f"{registry.strip()} tokenize strings with split",
            query_vector=hash_embedding("tokenize strings with split"),
            course_ids=[],
            embedding_model=MODEL,
            channel_limit=40,
        )
    ids = {c.skill_id for c in raw}
    assert active in ids
    assert not ids & {topic, candidate, deprecated}
