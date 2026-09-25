"""P3A end to end against PostgreSQL: raw turn -> qualify -> retrieve -> map/abstain -> provenance.

The default execution is the combined turn analysis (ADR 0004): local retrieval,
then ONE generation call, plus at most one adjudication call. The staged path is
covered by its own provenance test.

These tests run the P3A stage alone (`evidence=False`). The P3B attribution /
evidence and P4 ledger stages that follow it are covered by test_evidence_pipeline_db.py.
"""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.courses.models import CourseCreateRequest
from app.courses.service import create_course
from app.ingestion.models import EventBatchClientInfo, RawActivityEnvelope
from app.ingestion.service import ingest_batch
from app.intelligence.processing.pipeline import process_raw_message_job
from app.intelligence.skill_graph.jobs import run_bootstrap_job
from app.jobs.queue import JOB_BOOTSTRAP_COURSE_GRAPH, JOB_PROCESS_RAW_MESSAGE
from app.jobs.worker import build_worker
from app.model_gateway import (
    DbResultCache,
    InMemoryResultCache,
    ProviderError,
    QuotaPolicy,
    RequestBudget,
    TieredResultCache,
)
from app.model_gateway.gateway import ModelGateway
from app.model_gateway.recorder import DbModelRunRecorder
from tests.conftest import job_for, make_envelope
from tests.fakes import (
    FakeProvider,
    candidate_ids_in,
    graph_proposal,
    item_ids_in,
    proposal,
    route_responder,
    segment,
    turn_segment,
)
from tests.test_skill_graph_db import PYTHON_SKILLS

pytestmark = pytest.mark.db

CLIENT = EventBatchClientInfo(extension_version="0.2.0", adapter_version="chatgpt-1")
TURN_PROMPT_VERSIONS = {
    "query_embedding": "retrieval-query/v1",
    "turn_analysis": "turn-analysis/v1",
    "adjudication": "turn-adjudication/v1",
}


def gateway_for(pool, provider: FakeProvider, **options) -> ModelGateway:
    options.setdefault("generation_model", "gemini-3.7-flash")
    return ModelGateway(
        provider,
        DbModelRunRecorder(pool),
        embedding_model="gemini-embedding-2",
        default_timeout=5,
        **options,
    )


def cached_gateway_for(pool, provider: FakeProvider, **options) -> ModelGateway:
    """The production cache layout: memory + durable model_runs tier; vectors in memory."""
    return gateway_for(
        pool,
        provider,
        result_cache=TieredResultCache(InMemoryResultCache(), DbResultCache(pool)),
        embedding_cache=InMemoryResultCache(),
        **options,
    )


def bootstrapped_course(pool, learner: UUID, prefix: str) -> UUID:
    with pool.connection() as conn:
        course = create_course(
            conn,
            learner,
            CourseCreateRequest(name="Introduction to Python Programming", level="Beginner"),
            None,
        ).course
    names = [f"{prefix}{n}" for n in PYTHON_SKILLS]
    provider = FakeProvider(route_responder(graph=graph_proposal(names, prefix=prefix)))
    run_bootstrap_job(
        pool, gateway_for(pool, provider), job_for(pool, course.id, JOB_BOOTSTRAP_COURSE_GRAPH)
    )
    return course.id


def ingest_turn(
    pool,
    learner: UUID,
    user_text: str,
    assistant_text: str | None = "Here is an explanation.",
    *,
    captured_at: datetime | None = None,
    attachment: bool = False,
    conversation: str | None = None,
    base_index: int = 0,
) -> tuple[UUID, UUID | None]:
    conversation = conversation or str(uuid4())
    when = (captured_at or datetime.now(UTC)).isoformat()
    user_id_ext = f"u-{uuid4().hex[:10]}"
    user = make_envelope(
        learner,
        content_text=user_text,
        external_message_id=user_id_ext,
        external_conversation_id=conversation,
        message_index=base_index,
        captured_at=when,
    )
    if attachment:
        user["attachment_metadata"] = [
            {
                "kind": "image",
                "filename": "screenshot.png",
                "mime_type": "image/png",
                "content_available": False,
            }
        ]
        user["context_incomplete"] = True
    events = [user]
    if assistant_text is not None:
        events.append(
            make_envelope(
                learner,
                content_text=assistant_text,
                external_message_id=f"a-{uuid4().hex[:10]}",
                external_parent_message_id=user_id_ext,
                external_conversation_id=conversation,
                message_index=base_index + 1,
                role="assistant",
                captured_at=when,
            )
        )
    with pool.connection() as conn:
        stored = ingest_batch(
            conn, learner, [RawActivityEnvelope.model_validate(e) for e in events], CLIENT
        )
    return stored[0].raw_message_id, (stored[1].raw_message_id if len(stored) > 1 else None)


def id_for(messages, name_fragment: str) -> str:
    for line in messages[-1].content.splitlines():
        if name_fragment in line and "id=" in line:
            return re.search(r"id=([0-9a-f-]{36})", line).group(1)
    raise AssertionError(f"{name_fragment!r} was not among the candidates")


def rerank_identity(messages):
    return {"ranked_skill_ids": candidate_ids_in(messages)[:8]}


def mapper_choosing(name: str, confidence: float):
    def respond(messages):
        return {
            "mappings": [proposal(id_for(messages, name), confidence)],
            "new_skill_candidate": None,
        }

    return respond


def turn_choosing(text: str, name: str | None, confidence: float = 0.91, **qualification):
    """TURN_ANALYSIS answer: rank the pool as given, map `name` (if any) at `confidence`."""

    def respond(messages):
        ids = candidate_ids_in(messages)
        mappings = [proposal(id_for(messages, name), confidence)] if name else []
        chosen = [m["skill_id"] for m in mappings]
        ranked = chosen + [i for i in ids if i not in chosen]
        return {
            "segments": [turn_segment(text, ranked=ranked[:8], mappings=mappings, **qualification)]
        }

    return respond


def confirm_all(confidence: float = 0.9):
    def respond(messages):
        return {
            "adjudications": [
                {
                    "item_id": i,
                    "verdict": "CONFIRM",
                    "confidence": confidence,
                    "reason_code": "DIRECT_MATCH",
                }
                for i in item_ids_in(messages)
            ]
        }

    return respond


def fetch(pool, sql: str, *params):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def job_runs(pool, job_id: UUID) -> list[tuple]:
    return fetch(
        pool,
        "select id, task_type, prompt_version, status::text, cache_source_run_id "
        "from public.model_runs where trace_id = %s order by created_at",
        f"job:{job_id}",
    )


LOOP_QUESTION = "How do I use a for loop over lists in Python? I tried `for i in range(len(xs))`."
LOOP_SKILL = "For Loops over Lists"


def test_learning_turn_is_one_generation_call_with_full_provenance(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    course_id = bootstrapped_course(db_pool, learner, registry)
    user_id, assistant_id = ingest_turn(
        db_pool, learner, LOOP_QUESTION, "Use `for x in xs:` directly."
    )
    provider = FakeProvider(route_responder(turn=turn_choosing(LOOP_QUESTION, LOOP_SKILL)))
    worker = build_worker(db_pool, gateway_for(db_pool, provider), batch_size=10, evidence=False)
    worker.drain()

    jobs = dict(
        fetch(
            db_pool,
            "select entity_id, state::text || ':' || coalesce(outcome, '') from public.processing_jobs "
            "where learner_id = %s and job_type = 'PROCESS_RAW_MESSAGE'",
            learner,
        )
    )
    assert jobs == {user_id: "COMPLETED:DEFERRED_TO_ASSISTANT", assistant_id: "COMPLETED:MAPPED"}
    assert len(provider.calls) == 1 and len(provider.embed_calls) == 1

    (seg,) = fetch(
        db_pool,
        """select id, anchor_message_id, user_message_id, assistant_message_id, source_message_ids, route::text,
                  route_reason, context::text, intent::text, learning_relevance::text, skill_bearing,
                  course_ids, qualification_model_run_id, prompt_version, analysis_version, processing_job_id
             from public.activity_segments where learner_id = %s""",
        learner,
    )
    assert seg[1:4] == (user_id, user_id, assistant_id)
    assert seg[4] == [user_id, assistant_id]
    assert seg[5:11] == ("MAP", "LEARNING_SKILL_BEARING", "academic", "learn", "high", True)
    assert seg[11] == [course_id]
    assert seg[13:15] == ("turn-analysis/v1", "p3a-v1")
    job_id = job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE).id
    assert seg[15] == job_id

    (decision,) = fetch(
        db_pool,
        """select id, outcome::text, abstain_reason, jsonb_array_length(retrieval_candidates),
                  cardinality(mapper_candidate_ids), rerank_fallback, query_model_run_id, rerank_model_run_id,
                  mapping_model_run_id, adjudication_model_run_id, prompt_versions, policy_snapshot -> 'mapping',
                  mapper_version, new_skill_candidate_id
             from public.mapping_decisions where segment_id = %s""",
        seg[0],
    )
    assert decision[1:6] == ("MAPPED", None, 20, 8, False)
    # One call produced qualification, the top-8 and the mapping.
    assert decision[7] == decision[8] == seg[12] and decision[6] is not None
    assert decision[9] is None
    assert decision[10] == TURN_PROMPT_VERSIONS
    assert decision[11] == {
        "accept_threshold": 0.8,
        "adjudicate_min": 0.65,
        "max_skills_per_segment": 5,
    }
    assert decision[12] == "mapper/p3a-turn-v1" and decision[13] is None

    (mapping,) = fetch(
        db_pool,
        """select m.status::text, m.status_reason, m.confidence, m.evidence_span, n.canonical_name,
                  m.skill_id = any(d.mapper_candidate_ids), m.mapper_version
             from public.skill_mappings m join public.skill_nodes n on n.id = m.skill_id
             join public.mapping_decisions d on d.id = m.decision_id where m.segment_id = %s""",
        seg[0],
    )
    assert mapping[0:2] == ("ACCEPTED", "FIRST_PASS_ACCEPTED")
    assert mapping[2] == pytest.approx(0.91) and mapping[3] == "loop over a list"
    assert mapping[4] == f"{registry}{LOOP_SKILL}" and mapping[5] is True
    assert mapping[6] == "mapper/p3a-turn-v1"

    # Every model run of the job is traced to it: one embedding + ONE generation request.
    runs = job_runs(db_pool, job_id)
    assert [(r[1], r[2], r[3]) for r in runs] == [
        ("EMBED_QUERY", "retrieval-query/v1", "SUCCEEDED"),
        ("TURN_ANALYSIS", "turn-analysis/v1", "SUCCEEDED"),
    ]
    assert all(r[4] is None for r in runs)  # both were real provider requests
    assert {seg[12], decision[6]} == {r[0] for r in runs}

    # The P3A stage alone writes no attribution, evidence or ledger rows.
    assert fetch(
        db_pool,
        "select (select count(*) from public.attributions where learner_id = %s)"
        " + (select count(*) from public.evidence_events where learner_id = %s)"
        " + (select count(*) from public.skill_ledger where learner_id = %s)",
        learner,
        learner,
        learner,
    ) == [(0,)]


def test_staged_mode_keeps_the_original_calls_and_provenance(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, LOOP_QUESTION)
    provider = FakeProvider(
        route_responder(
            qualification={"segments": [segment(LOOP_QUESTION)]},
            rerank=rerank_identity,
            mapping=mapper_choosing(LOOP_SKILL, 0.91),
        )
    )
    worker = build_worker(
        db_pool,
        gateway_for(db_pool, provider),
        turn_analysis_mode="staged",
        batch_size=10,
        evidence=False,
    )
    worker.drain()
    job_id = job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE).id
    assert [r[1] for r in job_runs(db_pool, job_id)] == [
        "RELEVANCE_CLASSIFICATION",
        "EMBED_QUERY",
        "SKILL_RERANK",
        "SKILL_MAPPING",
    ]
    (row,) = fetch(
        db_pool,
        "select s.prompt_version, d.outcome::text, d.prompt_versions, d.mapper_version, "
        "d.rerank_model_run_id <> d.mapping_model_run_id "
        "from public.activity_segments s join public.mapping_decisions d on d.segment_id = s.id "
        "where s.learner_id = %s",
        learner,
    )
    assert row == (
        "relevance-intent/v1",
        "MAPPED",
        {
            "query_embedding": "retrieval-query/v1",
            "rerank": "skill-rerank/v1",
            "mapping": "skill-mapping/v1",
            "adjudication": "mapping-adjudication/v1",
        },
        "mapper/p3a-v1",
        True,
    )


def run_assistant_job(pool, provider, assistant_id, gateway=None):
    return process_raw_message_job(
        pool,
        gateway or gateway_for(pool, provider),
        job_for(pool, assistant_id, JOB_PROCESS_RAW_MESSAGE),
        evidence=False,
    )


def test_non_learning_turn_is_one_generation_call_and_no_decision(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(
        db_pool, learner, "Write a happy birthday message for my aunt", "Happy birthday!"
    )
    provider = FakeProvider(
        route_responder(
            turn=turn_choosing(
                "birthday message",
                None,
                context="personal",
                intent="create",
                relevance="none",
                skill_bearing=False,
                reason_code="SOCIAL",
            )
        )
    )
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "NON_LEARNING"
    # Retrieval ran first (one query embedding, no generation); nothing was mapped.
    assert len(provider.calls) == 1 and len(provider.embed_calls) == 1
    assert fetch(
        db_pool,
        "select route::text, route_reason from public.activity_segments where learner_id = %s",
        learner,
    ) == [("STOP", "NON_LEARNING")]
    assert fetch(
        db_pool, "select count(*) from public.mapping_decisions where learner_id = %s", learner
    ) == [(0,)]


def test_low_confidence_mapping_abstains_explicitly(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, LOOP_QUESTION)
    provider = FakeProvider(route_responder(turn=turn_choosing(LOOP_QUESTION, LOOP_SKILL, 0.5)))
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "ABSTAINED"
    assert len(provider.calls) == 1
    assert fetch(
        db_pool,
        "select d.outcome::text, d.abstain_reason, m.status::text, m.status_reason from public.mapping_decisions d "
        "join public.skill_mappings m on m.decision_id = d.id where d.learner_id = %s",
        learner,
    ) == [("ABSTAINED", "LOW_CONFIDENCE", "ABSTAINED", "LOW_CONFIDENCE")]


def test_ambiguous_turn_is_two_generation_calls_and_is_accepted_on_confirmation(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, LOOP_QUESTION)
    provider = FakeProvider(
        route_responder(
            turn=turn_choosing(LOOP_QUESTION, LOOP_SKILL, 0.7), turn_adjudication=confirm_all()
        )
    )
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "MAPPED"
    assert len(provider.calls) == 2
    (row,) = fetch(
        db_pool,
        "select m.status::text, m.status_reason, m.adjudicated, m.first_pass_confidence, m.confidence, "
        "r.task_type, r.prompt_version from public.skill_mappings m "
        "join public.mapping_decisions d on d.id = m.decision_id "
        "join public.model_runs r on r.id = d.adjudication_model_run_id where m.learner_id = %s",
        learner,
    )
    assert row[0:3] == ("ACCEPTED", "ADJUDICATION_CONFIRMED", True)
    assert row[3] == pytest.approx(0.7) and row[4] == pytest.approx(0.9)
    assert row[5:] == ("MAPPING_ADJUDICATION", "turn-adjudication/v1")


def test_unknown_concept_creates_a_pending_candidate_not_a_skill(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    before = fetch(db_pool, "select count(*) from public.skill_nodes")[0][0]
    new_skill = {
        "canonical_name": f"{registry}Polars LazyFrame Queries",
        "parent_candidate_id": None,
        "description": "Build lazy query plans with Polars.",
    }
    text = "How do I build a lazy query with Polars scan_csv and collect?"

    def turn(messages):
        return {
            "segments": [
                turn_segment(text, ranked=candidate_ids_in(messages)[:8], new_skill=new_skill)
            ]
        }

    provider = FakeProvider(route_responder(turn=turn))
    for _ in range(2):
        _, assistant_id = ingest_turn(db_pool, learner, text)
        assert run_assistant_job(db_pool, provider, assistant_id).outcome == "ABSTAINED"
    (candidate,) = fetch(
        db_pool,
        "select id, status::text, occurrences, resolved_skill_id from public.skill_candidates where canonical_name = %s",
        new_skill["canonical_name"],
    )
    assert candidate[1:] == ("PENDING_REVIEW", 2, None)
    assert fetch(db_pool, "select count(*) from public.skill_nodes")[0][0] == before
    assert fetch(
        db_pool,
        "select count(*) from public.mapping_decisions where learner_id = %s and new_skill_candidate_id = %s "
        "and abstain_reason = 'NO_MATCHING_CANDIDATE'",
        learner,
        candidate[0],
    ) == [(2,)]


def test_missing_attachment_context_is_retained_but_not_mapped(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(
        db_pool,
        learner,
        "What is wrong with the code in this screenshot?",
        "I can't see the image.",
        attachment=True,
    )
    provider = FakeProvider(
        route_responder(
            turn={"segments": [turn_segment("What is wrong with this code?", intent="solve")]}
        )
    )
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "UNCERTAIN"
    prompt = provider.calls[0]["messages"][-1].content
    assert "Context incomplete: yes" in prompt and "Candidates: (none" in prompt
    assert fetch(
        db_pool,
        "select route::text, route_reason, context_incomplete from public.activity_segments where learner_id = %s",
        learner,
    ) == [("UNCERTAIN", "CONTEXT_INCOMPLETE", True)]
    assert provider.embed_calls == []  # never mapped, so it does not even retrieve


def test_invalid_turn_output_abstains_with_provenance(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    user_id, assistant_id = ingest_turn(db_pool, learner, "Explain recursion", "Recursion is ...")
    provider = FakeProvider(route_responder(turn=["{oops", "still not json"]))
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "UNCERTAIN"
    (seg,) = fetch(
        db_pool,
        "select route::text, route_reason, text, learning_relevance, source_message_ids, "
        "qualification_model_run_id, prompt_version from public.activity_segments where learner_id = %s",
        learner,
    )
    assert seg[0:2] == ("UNCERTAIN", "MODEL_OUTPUT_INVALID") and seg[3] is None
    assert seg[2] == "Learner: Explain recursion\n\nAssistant: Recursion is ..."
    assert seg[4] == [user_id, assistant_id] and seg[6] == "turn-analysis/v1"
    assert fetch(
        db_pool,
        "select status::text, attempt, cache_key from public.model_runs where id = %s",
        seg[5],
    ) == [("INVALID_OUTPUT", 2, None)]


def test_rerun_is_idempotent(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, LOOP_QUESTION)
    provider = FakeProvider(route_responder(turn=turn_choosing(LOOP_QUESTION, LOOP_SKILL, 0.95)))
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "MAPPED"
    calls = len(provider.calls)
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "ALREADY_ANALYZED"
    assert len(provider.calls) == calls  # no second model call
    counts = fetch(
        db_pool,
        "select (select count(*) from public.activity_segments where learner_id = %s), "
        "(select count(*) from public.skill_mappings where learner_id = %s)",
        learner,
        learner,
    )
    assert counts == [(1, 1)]


def test_repeated_identical_turn_is_served_from_the_cache(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    provider = FakeProvider(
        route_responder(
            turn=turn_choosing(LOOP_QUESTION, LOOP_SKILL, 0.7), turn_adjudication=confirm_all()
        )
    )
    gateway = cached_gateway_for(db_pool, provider)
    _, first = ingest_turn(db_pool, learner, LOOP_QUESTION)
    assert run_assistant_job(db_pool, provider, first, gateway).outcome == "MAPPED"
    assert (len(provider.calls), len(provider.embed_calls)) == (2, 1)

    # The same turn again (a new conversation, so the same unit): zero provider requests.
    _, second = ingest_turn(db_pool, learner, LOOP_QUESTION)
    assert run_assistant_job(db_pool, provider, second, gateway).outcome == "MAPPED"
    assert (len(provider.calls), len(provider.embed_calls)) == (2, 1)

    first_runs = job_runs(db_pool, job_for(db_pool, first, JOB_PROCESS_RAW_MESSAGE).id)
    second_runs = job_runs(db_pool, job_for(db_pool, second, JOB_PROCESS_RAW_MESSAGE).id)
    assert [r[1] for r in second_runs] == [r[1] for r in first_runs]
    assert [r[4] for r in second_runs] == [r[0] for r in first_runs]  # hit -> source run
    # The second decision references the hit rows, which point to the producing runs.
    (decision,) = fetch(
        db_pool,
        "select d.mapping_model_run_id, d.adjudication_model_run_id, d.query_model_run_id "
        "from public.mapping_decisions d join public.activity_segments s on s.id = d.segment_id "
        "where s.anchor_message_id = (select parent.id from public.raw_messages parent "
        "  join public.raw_messages a on a.external_parent_message_id = parent.external_message_id "
        " where a.id = %s)",
        second,
    )
    assert set(decision) == {r[0] for r in second_runs}

    # After a restart (fresh memory tiers), the durable tier still serves the generation;
    # only the query embedding (memory-only cache) is requested again.
    restarted = cached_gateway_for(db_pool, provider)
    _, third = ingest_turn(db_pool, learner, LOOP_QUESTION)
    assert run_assistant_job(db_pool, provider, third, restarted).outcome == "MAPPED"
    assert (len(provider.calls), len(provider.embed_calls)) == (2, 2)


def test_adjudication_429_defers_and_the_resumed_job_reuses_the_turn_analysis(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    user_id, assistant_id = ingest_turn(db_pool, learner, LOOP_QUESTION)
    rate_limited = iter([ProviderError("rate_limited", "HTTP_429", retry_after=20)])

    def adjudicate(messages):
        return next(rate_limited, None) or confirm_all()(messages)

    # Only ONE turn-analysis answer is scripted: a second request would fail the job.
    provider = FakeProvider(
        route_responder(
            turn=[turn_choosing(LOOP_QUESTION, LOOP_SKILL, 0.7)], turn_adjudication=adjudicate
        )
    )
    worker = build_worker(
        db_pool, cached_gateway_for(db_pool, provider), batch_size=10, evidence=False
    )
    worker.drain()
    job = job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE)
    assert fetch(
        db_pool,
        "select state::text, attempts, outcome from public.processing_jobs where id = %s",
        job.id,
    ) == [("PENDING", 0, "MODEL_BACKPRESSURE")]
    assert len(provider.calls) == 2  # no retry loop

    with db_pool.connection() as conn:  # the deferral has elapsed
        conn.execute(
            "update public.processing_jobs set available_at = now() where id = %s", (job.id,)
        )
    worker.drain()
    assert fetch(
        db_pool,
        "select state::text, outcome from public.processing_jobs where id = %s",
        job.id,
    ) == [("COMPLETED", "MAPPED")]
    assert len(provider.calls) == 3  # just the adjudication again
    statuses = [(r[1], r[3], r[4] is not None) for r in job_runs(db_pool, job.id)]
    assert statuses == [
        ("EMBED_QUERY", "SUCCEEDED", False),
        ("TURN_ANALYSIS", "SUCCEEDED", False),
        ("MAPPING_ADJUDICATION", "RATE_LIMITED", False),
        ("EMBED_QUERY", "SUCCEEDED", True),
        ("TURN_ANALYSIS", "SUCCEEDED", True),
        ("MAPPING_ADJUDICATION", "SUCCEEDED", False),
    ]
    assert user_id is not None


def test_spent_budget_defers_the_job_before_any_provider_request(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    model = f"budget-test-{uuid4().hex[:8]}"  # isolates this test's request count
    recorder = DbModelRunRecorder(db_pool)
    provider = FakeProvider(route_responder(turn=turn_choosing(LOOP_QUESTION, LOOP_SKILL)))
    gateway = ModelGateway(
        provider,
        recorder,
        generation_model=model,
        embedding_model="gemini-embedding-2",
        default_timeout=5,
        budget=RequestBudget(QuotaPolicy({model: 3}, reserve=2), recorder),
    )
    _, first = ingest_turn(db_pool, learner, LOOP_QUESTION)
    _, second = ingest_turn(db_pool, learner, "How do I loop over a dict's items?")
    worker = build_worker(db_pool, gateway, batch_size=10, evidence=False)
    worker.drain()
    states = fetch(
        db_pool,
        "select state::text, attempts, outcome from public.processing_jobs "
        "where entity_id = any(%s) order by state",
        [first, second],
    )
    assert states == [("COMPLETED", 1, "MAPPED"), ("PENDING", 0, "MODEL_BUDGET_RESERVE")]
    assert len(provider.calls) == 1  # the reserve (2 of 3) was never touched
    assert fetch(
        db_pool,
        "select count(*) from public.model_runs where model = %s and cache_source_run_id is null",
        model,
    ) == [(1,)]
    (delay,) = fetch(
        db_pool,
        "select extract(epoch from available_at - now()) from public.processing_jobs "
        "where entity_id = any(%s) and state = 'PENDING'",
        [first, second],
    )[0]
    assert 15 <= float(delay) <= 3600


def test_user_message_pairing_rules(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    fresh_user, _ = ingest_turn(db_pool, learner, "A question with no reply yet", None)
    provider = FakeProvider(route_responder(turn={"segments": [turn_segment("old question")]}))
    job = job_for(db_pool, fresh_user, JOB_PROCESS_RAW_MESSAGE)
    result = process_raw_message_job(db_pool, gateway_for(db_pool, provider), job, evidence=False)
    assert (result.outcome, result.defer_seconds) == ("AWAITING_ASSISTANT", 20)
    assert provider.calls == []

    old_user, _ = ingest_turn(
        db_pool,
        learner,
        "An old question whose reply was never captured",
        None,
        captured_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    provider = FakeProvider(
        route_responder(
            turn={"segments": [turn_segment("old question", relevance="none", skill_bearing=False)]}
        )
    )
    job = job_for(db_pool, old_user, JOB_PROCESS_RAW_MESSAGE)
    assert (
        process_raw_message_job(
            db_pool, gateway_for(db_pool, provider), job, evidence=False
        ).outcome
        == "NON_LEARNING"
    )
    assert fetch(
        db_pool,
        "select user_message_id, assistant_message_id, source_message_ids from public.activity_segments "
        "where anchor_message_id = %s",
        old_user,
    ) == [(old_user, None, [old_user])]
    assert "Assistant response:\n<<<\n(not captured)" in provider.calls[0]["messages"][-1].content


def test_orphan_assistant_and_superseded_revisions_are_skipped(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    orphan = make_envelope(
        learner, role="assistant", content_text="Standalone reply", message_index=0
    )
    edited_id = f"u-{uuid4().hex[:8]}"
    first_rev = make_envelope(
        learner,
        content_text="Draft question",
        external_message_id=edited_id,
        message_index=0,
        external_conversation_id=str(uuid4()),
    )
    second_rev = {
        **make_envelope(
            learner, content_text="Edited question", external_message_id=edited_id, revision_index=1
        ),
        "external_conversation_id": first_rev["external_conversation_id"],
        "message_index": 0,
    }
    with db_pool.connection() as conn:
        stored = ingest_batch(
            conn,
            learner,
            [RawActivityEnvelope.model_validate(e) for e in (orphan, first_rev, second_rev)],
            CLIENT,
        )
    gateway = gateway_for(db_pool, FakeProvider())

    def run(raw_id):
        return process_raw_message_job(
            db_pool, gateway, job_for(db_pool, raw_id, JOB_PROCESS_RAW_MESSAGE), evidence=False
        )

    assert run(stored[0].raw_message_id).outcome == "ORPHAN_ASSISTANT"
    assert run(stored[1].raw_message_id).outcome == "SUPERSEDED_REVISION"


@pytest.mark.parametrize(
    ("error", "state", "attempts", "outcome", "run_status"),
    [
        # Provider backpressure: deferred, no attempt spent, the job stays pending.
        (
            ProviderError("unavailable", "HTTP_503"),
            "PENDING",
            0,
            "MODEL_BACKPRESSURE",
            "UNAVAILABLE",
        ),
        (
            ProviderError("rate_limited", "HTTP_429", retry_after=30),
            "PENDING",
            0,
            "MODEL_BACKPRESSURE",
            "RATE_LIMITED",
        ),
        # A real provider failure is a failed attempt with backoff.
        (ProviderError("failed", "HTTP_500"), "RETRY_WAIT", 1, None, "FAILED"),
    ],
)
def test_model_outage_leaves_the_job_retryable_and_raw_data_safe(
    db_pool, registry, new_learner, error, state, attempts, outcome, run_status
) -> None:
    learner = new_learner()
    user_id, assistant_id = ingest_turn(db_pool, learner, LOOP_QUESTION)
    provider = FakeProvider(route_responder(turn=error))
    worker = build_worker(db_pool, gateway_for(db_pool, provider), batch_size=10, evidence=False)
    worker.drain()
    jobs = {
        row[0]: row[1:]
        for row in fetch(
            db_pool,
            "select entity_id, state::text, attempts, outcome from public.processing_jobs "
            "where learner_id = %s",
            learner,
        )
    }
    assert jobs[assistant_id] == (state, attempts, outcome)
    assert jobs[user_id][0] == "COMPLETED"
    assert len(provider.calls) == 1  # one request, no immediate retry
    assert fetch(
        db_pool, "select count(*) from public.activity_segments where learner_id = %s", learner
    ) == [(0,)]
    assert fetch(
        db_pool, "select count(*) from public.raw_messages where learner_id = %s", learner
    ) == [(2,)]
    assert fetch(
        db_pool,
        "select task_type, status::text, cache_key from public.model_runs "
        "where learner_id = %s order by created_at",
        learner,
    ) == [
        ("EMBED_QUERY", "SUCCEEDED", fetch_cache_key(db_pool, learner)),
        ("TURN_ANALYSIS", run_status, None),
    ]


def fetch_cache_key(pool, learner):
    return fetch(
        pool,
        "select cache_key from public.model_runs where learner_id = %s and task_type = 'EMBED_QUERY'",
        learner,
    )[0][0]
