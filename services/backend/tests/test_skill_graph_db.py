"""Course graph bootstrap against PostgreSQL: generate -> canonicalize -> embed (§8.2, §9.3)."""

from uuid import UUID

import pytest

from app.courses.models import CourseCreateRequest
from app.courses.service import create_course
from app.intelligence.skill_graph.canonical import skill_key
from app.intelligence.skill_graph.embedding import embed_skills
from app.intelligence.skill_graph.jobs import course_skill_ids, run_bootstrap_job
from app.intelligence.skill_graph.registry import add_edge, merge_skill, rename_skill, resolve_keys
from app.jobs.queue import JOB_BOOTSTRAP_COURSE_GRAPH
from app.jobs.worker import Worker
from app.model_gateway import ModelOutputInvalidError, ProviderError, RunContext
from app.model_gateway.gateway import ModelGateway
from app.model_gateway.recorder import DbModelRunRecorder
from tests.conftest import job_for
from tests.fakes import FakeProvider, graph_proposal, route_responder

pytestmark = pytest.mark.db

PYTHON_SKILLS = [
    "Variables and Assignment",
    "Integer and Float Arithmetic",
    "String Formatting",
    "String Slicing",
    "Boolean Expressions",
    "If Elif Else Branching",
    "For Loops over Lists",
    "While Loops",
    "Loop Control with Break and Continue",
    "List Indexing",
    "List Comprehensions",
    "Dictionary Lookup and Update",
    "Dictionary Iteration",
    "Tuples and Unpacking",
    "Sets and Membership",
    "Defining Functions",
    "Function Parameters and Defaults",
    "Return Values",
    "Variable Scope",
    "Recursion Basics",
    "Exception Handling with Try Except",
    "Raising Exceptions",
    "Reading Text Files",
    "Writing Text Files",
    "Importing Modules",
    "Classes and Objects",
    "Instance Methods",
    "Debugging with Print Statements",
    "Reading Tracebacks",
    "Writing Unit Tests with Assert",
]


def db_gateway(pool, provider: FakeProvider) -> ModelGateway:
    return ModelGateway(
        provider,
        DbModelRunRecorder(pool),
        generation_model="gemini-3.7-flash",
        embedding_model="gemini-embedding-2",
        default_timeout=5,
    )


def new_course(pool, learner: UUID, name: str = "Introduction to Python Programming") -> UUID:
    with pool.connection() as conn:
        created = create_course(
            conn, learner, CourseCreateRequest(name=name, level="Beginner"), None
        )
    return created.course.id


def course_row(pool, course_id: UUID) -> tuple:
    with pool.connection() as conn:
        return conn.execute(
            "select graph_status::text, graph_version, graph_error, graph_model_run_id "
            "from public.courses where id = %s",
            (course_id,),
        ).fetchone()


def names(prefix: str, base: list[str] = PYTHON_SKILLS) -> list[str]:
    return [f"{prefix}{n}" for n in base]


def test_bootstrap_job_builds_an_embedded_course_graph(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    course_id = new_course(db_pool, learner)
    proposal = graph_proposal(names(registry), prefix=registry)
    proposal["skills"][10]["aliases"] = ["Listcomps", "list comprehension syntax"]
    provider = FakeProvider(route_responder(graph=proposal))
    gateway = db_gateway(db_pool, provider)
    job = job_for(db_pool, course_id, JOB_BOOTSTRAP_COURSE_GRAPH)

    assert run_bootstrap_job(db_pool, gateway, job).outcome == "GRAPH_READY"

    status, version, error, graph_run = course_row(db_pool, course_id)
    assert (status, version, error) == ("READY", 1, None) and graph_run is not None
    with db_pool.connection() as conn:
        kinds = dict(
            conn.execute(
                """select n.node_kind::text, count(*) from public.course_skills cs
                 join public.skill_nodes n on n.id = cs.skill_id
                where cs.course_id = %s and cs.active group by 1""",
                (course_id,),
            ).fetchall()
        )
        embedded = conn.execute(
            """select count(*), count(distinct e.model), min(e.graph_version), bool_and(e.model_run_id is not null)
                 from public.skill_embeddings e join public.course_skills cs on cs.skill_id = e.skill_id
                where cs.course_id = %s""",
            (course_id,),
        ).fetchone()
        dims = conn.execute(
            """select distinct extensions.vector_dims(e.embedding) from public.skill_embeddings e
                 join public.course_skills cs on cs.skill_id = e.skill_id where cs.course_id = %s""",
            (course_id,),
        ).fetchall()
        alias_owner = conn.execute(
            "select n.canonical_name from public.skill_aliases a join public.skill_nodes n on n.id = a.skill_id "
            "where a.normalized_alias = %s",
            (skill_key("Listcomps"),),
        ).fetchone()
        edges = conn.execute(
            "select edge_type::text, count(*) from public.skill_edges where course_id = %s group by 1",
            (course_id,),
        ).fetchall()
        runs = conn.execute(
            "select task_type, prompt_version, status::text from public.model_runs where course_id = %s "
            "order by created_at",
            (course_id,),
        ).fetchall()
    assert kinds == {"SKILL": 30, "TOPIC": 4}
    assert embedded == (30, 1, 1, True) and dims == [(768,)]
    assert alias_owner == (f"{registry}List Comprehensions",)
    assert dict(edges)["PARENT"] == 30 and dict(edges)["PREREQUISITE"] > 0
    assert runs[0] == ("SKILL_GRAPH_BOOTSTRAP", "skill-graph-bootstrap/v1", "SUCCEEDED")
    assert {r[:2] for r in runs[1:]} == {("EMBED_SKILLS", "skill-embedding-text/v1")}

    # Replay after completion is a no-op: no model call, no new rows.
    calls = len(provider.calls), len(provider.embed_calls)
    assert run_bootstrap_job(db_pool, gateway, job).outcome == "ALREADY_READY"
    assert (len(provider.calls), len(provider.embed_calls)) == calls


def test_second_course_reuses_canonical_skills_and_aliases(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    first = new_course(db_pool, learner, "Python I")
    gateway = db_gateway(
        db_pool,
        FakeProvider(route_responder(graph=graph_proposal(names(registry), prefix=registry))),
    )
    run_bootstrap_job(db_pool, gateway, job_for(db_pool, first, JOB_BOOTSTRAP_COURSE_GRAPH))

    variants = list(PYTHON_SKILLS)
    variants[6] = "for loops over list"  # case + plural variant of "For Loops over Lists"
    variants[29] = "Brand New Skill For Course Two"
    second_proposal = graph_proposal(names(registry, variants), prefix=registry)
    second_proposal["skills"][0]["canonical_name"] = f"{registry}Assigning Variables"
    second_proposal["skills"][0]["aliases"] = [
        f"{registry}Variables and Assignment"
    ]  # alias of an existing skill
    second_proposal["skills"][1]["aliases"] = [
        f"{registry}String Slicing"
    ]  # owned by another skill
    second = new_course(db_pool, learner, "Python II")
    provider = FakeProvider(route_responder(graph=second_proposal))
    run_bootstrap_job(
        db_pool, db_gateway(db_pool, provider), job_for(db_pool, second, JOB_BOOTSTRAP_COURSE_GRAPH)
    )

    with db_pool.connection() as conn:
        known = resolve_keys(
            conn,
            [
                skill_key(f"{registry}{n}")
                for n in (
                    "For Loops over Lists",
                    "Variables and Assignment",
                    "Integer and Float Arithmetic",
                    "String Slicing",
                )
            ],
        )
        overlap = conn.execute(
            """select count(*) from public.course_skills a join public.course_skills b on a.skill_id = b.skill_id
                where a.course_id = %s and b.course_id = %s""",
            (first, second),
        ).fetchone()[0]
        variant_key = conn.execute(
            "select skill_id from public.skill_aliases where normalized_alias = %s",
            (skill_key(f"{registry}Assigning Variables"),),
        ).fetchone()[0]
        slicing_owners = conn.execute(
            "select count(distinct skill_id) from public.skill_aliases where normalized_alias = %s",
            (skill_key(f"{registry}String Slicing"),),
        ).fetchone()[0]
        total = conn.execute(
            "select count(*) from public.skill_nodes where canonical_name like %s and node_kind = 'SKILL'",
            (f"{registry}%",),
        ).fetchone()[0]
    # 30 shared skills + 1 new one; the spelling variant and the alias-matched proposal reuse UUIDs.
    assert overlap == 29 + 4  # 29 skills + 4 shared topics
    assert total == 31
    assert variant_key == known[skill_key(f"{registry}Variables and Assignment")]
    assert slicing_owners == 1  # the conflicting alias was not re-pointed
    # Second course embedded only what was new or changed.
    assert sum(len(c["texts"]) for c in provider.embed_calls) == 2


def test_embeddings_are_cached_and_refreshed_when_text_changes(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    course_id = new_course(db_pool, learner)
    provider = FakeProvider(route_responder(graph=graph_proposal(names(registry), prefix=registry)))
    gateway = db_gateway(db_pool, provider)
    run_bootstrap_job(db_pool, gateway, job_for(db_pool, course_id, JOB_BOOTSTRAP_COURSE_GRAPH))
    ctx = RunContext("test:cache")
    with db_pool.connection() as conn:
        ids = course_skill_ids(conn, course_id)
        before = len(provider.embed_calls)
        assert embed_skills(conn, gateway, ids, ctx).embedded == 0
        assert len(provider.embed_calls) == before  # nothing re-embedded

        loops = resolve_keys(conn, [skill_key(f"{registry}While Loops")])[
            skill_key(f"{registry}While Loops")
        ]
        conn.execute(
            "insert into public.skill_aliases (skill_id, alias, normalized_alias, source) values (%s, %s, %s, 'MANUAL')",
            (
                loops,
                f"{registry}Indefinite Iteration",
                skill_key(f"{registry}Indefinite Iteration"),
            ),
        )
        rename_skill(
            conn,
            resolve_keys(conn, [skill_key(f"{registry}Return Values")])[
                skill_key(f"{registry}Return Values")
            ],
            f"{registry}Returning Values from Functions",
        )
        report = embed_skills(conn, gateway, ids, ctx)
    assert (report.embedded, report.cached) == (2, 28)


def test_embedding_failure_retries_without_regenerating_the_graph(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    course_id = new_course(db_pool, learner)
    provider = FakeProvider(
        route_responder(graph=[graph_proposal(names(registry), prefix=registry)]),
        embed_error=ProviderError("rate_limited", "HTTP_429"),
    )
    gateway = db_gateway(db_pool, provider)
    job = job_for(db_pool, course_id, JOB_BOOTSTRAP_COURSE_GRAPH)
    worker = Worker(
        db_pool, {JOB_BOOTSTRAP_COURSE_GRAPH: lambda j: run_bootstrap_job(db_pool, gateway, j)}
    )
    worker._execute(job)  # noqa: SLF001 - exercise the failure path directly
    status, version, _, _ = course_row(db_pool, course_id)
    assert (status, version) == ("EMBEDDING", 1)

    provider.embed_error = None
    assert run_bootstrap_job(db_pool, gateway, job).outcome == "GRAPH_READY"
    assert len(provider.calls) == 1  # the graph was generated once
    assert course_row(db_pool, course_id)[0] == "READY"


def test_invalid_graph_output_fails_the_job_and_finally_the_course(
    db_pool, registry, new_learner
) -> None:
    from app.intelligence.skill_graph.jobs import mark_bootstrap_failed

    learner = new_learner()
    course_id = new_course(db_pool, learner)
    gateway = db_gateway(db_pool, FakeProvider(route_responder(graph={"topics": [], "skills": []})))
    job = job_for(db_pool, course_id, JOB_BOOTSTRAP_COURSE_GRAPH)
    with pytest.raises(ModelOutputInvalidError):
        run_bootstrap_job(db_pool, gateway, job)
    mark_bootstrap_failed(db_pool, job, "ModelOutputInvalidError: graph invalid", final=False)
    assert course_row(db_pool, course_id)[:3] == (
        "GENERATING",
        0,
        "ModelOutputInvalidError: graph invalid",
    )
    mark_bootstrap_failed(db_pool, job, "ModelOutputInvalidError: graph invalid", final=True)
    assert course_row(db_pool, course_id)[0] == "FAILED"
    with db_pool.connection() as conn:
        statuses = [
            r[0]
            for r in conn.execute(
                "select status::text from public.model_runs where course_id = %s", (course_id,)
            ).fetchall()
        ]
    assert statuses == ["INVALID_OUTPUT", "INVALID_OUTPUT"]


def test_rename_and_merge_keep_identity(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    course_id = new_course(db_pool, learner)
    gateway = db_gateway(
        db_pool,
        FakeProvider(route_responder(graph=graph_proposal(names(registry), prefix=registry))),
    )
    run_bootstrap_job(db_pool, gateway, job_for(db_pool, course_id, JOB_BOOTSTRAP_COURSE_GRAPH))
    key = lambda n: skill_key(f"{registry}{n}")  # noqa: E731
    with db_pool.connection() as conn:
        found = resolve_keys(
            conn, [key("Reading Tracebacks"), key("Debugging with Print Statements")]
        )
        tracebacks, debugging = (
            found[key("Reading Tracebacks")],
            found[key("Debugging with Print Statements")],
        )

        rename_skill(conn, tracebacks, f"{registry}Interpreting Tracebacks")
        after = resolve_keys(conn, [key("Reading Tracebacks"), key("Interpreting Tracebacks")])
        assert after == {
            key("Reading Tracebacks"): tracebacks,
            key("Interpreting Tracebacks"): tracebacks,
        }

        merge_skill(conn, debugging, tracebacks)
        merged = conn.execute(
            "select status::text, merged_into_id from public.skill_nodes where id = %s",
            (debugging,),
        ).fetchone()
        assert merged == ("MERGED", tracebacks)
        # The merged name still resolves - to the survivor's UUID.
        assert resolve_keys(conn, [key("Debugging with Print Statements")]) == {
            key("Debugging with Print Statements"): tracebacks
        }
        in_course = {
            r[0]
            for r in conn.execute(
                "select skill_id from public.course_skills where course_id = %s", (course_id,)
            ).fetchall()
        }
        assert tracebacks in in_course and debugging not in in_course


def test_prerequisite_cycles_are_refused_across_the_registry(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    course_id = new_course(db_pool, learner)
    gateway = db_gateway(
        db_pool,
        FakeProvider(route_responder(graph=graph_proposal(names(registry), prefix=registry))),
    )
    run_bootstrap_job(db_pool, gateway, job_for(db_pool, course_id, JOB_BOOTSTRAP_COURSE_GRAPH))
    key = lambda n: skill_key(f"{registry}{n}")  # noqa: E731
    with db_pool.connection() as conn:
        ids = resolve_keys(
            conn, [key("Variables and Assignment"), key("String Formatting"), key("Return Values")]
        )
        a, b, c = (
            ids[key(n)] for n in ("Variables and Assignment", "String Formatting", "Return Values")
        )
        assert add_edge(conn, a, b, "PREREQUISITE", course_id=None) in ("added", "exists")
        assert add_edge(conn, b, c, "PREREQUISITE", course_id=None) in ("added", "exists")
        assert add_edge(conn, c, a, "PREREQUISITE", course_id=None) == "cycle"
        assert add_edge(conn, a, a, "RELATED", course_id=None) == "self"
        assert add_edge(conn, c, a, "RELATED", course_id=None) in ("added", "exists")
