"""P3A end to end against PostgreSQL: raw turn -> qualify -> retrieve -> map/abstain -> provenance."""

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
from app.model_gateway import ProviderError
from app.model_gateway.gateway import ModelGateway
from app.model_gateway.recorder import DbModelRunRecorder
from tests.conftest import job_for, make_envelope
from tests.fakes import FakeProvider, candidate_ids_in, graph_proposal, route_responder, segment
from tests.test_skill_graph_db import PYTHON_SKILLS

pytestmark = pytest.mark.db

CLIENT = EventBatchClientInfo(extension_version="0.2.0", adapter_version="chatgpt-1")


def gateway_for(pool, provider: FakeProvider) -> ModelGateway:
    return ModelGateway(
        provider,
        DbModelRunRecorder(pool),
        generation_model="gemini-3.7-flash",
        embedding_model="gemini-embedding-2",
        default_timeout=5,
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
        if name_fragment in line:
            return re.search(r"id=([0-9a-f-]{36})", line).group(1)
    raise AssertionError(f"{name_fragment!r} was not among the candidates")


def rerank_identity(messages):
    return {"ranked_skill_ids": candidate_ids_in(messages)[:8]}


def mapper_choosing(name: str, confidence: float):
    def respond(messages):
        return {
            "mappings": [
                {
                    "skill_id": id_for(messages, name),
                    "confidence": confidence,
                    "evidence_span": "loop over a list",
                    "reason_code": "CONCEPT_USE",
                }
            ],
            "new_skill_candidate": None,
        }

    return respond


def fetch(pool, sql: str, *params):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


LOOP_QUESTION = "How do I use a for loop over lists in Python? I tried `for i in range(len(xs))`."


def test_learning_turn_is_mapped_with_full_provenance(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    course_id = bootstrapped_course(db_pool, learner, registry)
    user_id, assistant_id = ingest_turn(
        db_pool, learner, LOOP_QUESTION, "Use `for x in xs:` directly."
    )
    provider = FakeProvider(
        route_responder(
            qualification={"segments": [segment(LOOP_QUESTION)]},
            rerank=rerank_identity,
            mapping=mapper_choosing("For Loops over Lists", 0.91),
        )
    )
    worker = build_worker(db_pool, gateway_for(db_pool, provider), batch_size=10)
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
    assert seg[13:15] == ("relevance-intent/v1", "p3a-v1")
    assert seg[15] == job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE).id

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
    assert None not in decision[6:9] and decision[9] is None
    assert decision[10] == {
        "query_embedding": "retrieval-query/v1",
        "rerank": "skill-rerank/v1",
        "mapping": "skill-mapping/v1",
        "adjudication": "mapping-adjudication/v1",
    }
    assert decision[11] == {
        "accept_threshold": 0.8,
        "adjudicate_min": 0.65,
        "max_skills_per_segment": 5,
    }
    assert decision[12] == "mapper/p3a-v1" and decision[13] is None

    (mapping,) = fetch(
        db_pool,
        """select m.status::text, m.status_reason, m.confidence, m.evidence_span, n.canonical_name,
                  m.skill_id = any(d.mapper_candidate_ids)
             from public.skill_mappings m join public.skill_nodes n on n.id = m.skill_id
             join public.mapping_decisions d on d.id = m.decision_id where m.segment_id = %s""",
        seg[0],
    )
    assert mapping[0:2] == ("ACCEPTED", "FIRST_PASS_ACCEPTED")
    assert mapping[2] == pytest.approx(0.91) and mapping[3] == "loop over a list"
    assert mapping[4] == f"{registry}For Loops over Lists" and mapping[5] is True

    # Every model run of the job is traced to it, with a prompt version.
    runs = fetch(
        db_pool,
        "select task_type, prompt_version, status::text from public.model_runs where trace_id = %s order by created_at",
        f"job:{seg[15]}",
    )
    assert [r[0] for r in runs] == [
        "RELEVANCE_CLASSIFICATION",
        "EMBED_QUERY",
        "SKILL_RERANK",
        "SKILL_MAPPING",
    ]
    assert all(r[1] and r[2] == "SUCCEEDED" for r in runs)
    run_ids = {
        r[0]
        for r in fetch(
            db_pool, "select id from public.model_runs where trace_id = %s", f"job:{seg[15]}"
        )
    }
    assert {seg[12], decision[6], decision[7], decision[8]} <= run_ids

    # P3A never creates evidence, ledger, debt or verification rows.
    assert fetch(
        db_pool,
        "select count(*) from pg_tables where schemaname = 'public' and tablename in "
        "('evidence_events', 'skill_ledger', 'attributions', 'verification_sessions')",
    ) == [(0,)]


def run_assistant_job(pool, provider, assistant_id):
    return process_raw_message_job(
        pool, gateway_for(pool, provider), job_for(pool, assistant_id, JOB_PROCESS_RAW_MESSAGE)
    )


def test_non_learning_turn_stops_before_retrieval(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(
        db_pool, learner, "Write a happy birthday message for my aunt", "Happy birthday!"
    )
    provider = FakeProvider(
        route_responder(
            qualification={
                "segments": [
                    segment(
                        "birthday message",
                        context="personal",
                        intent="create",
                        relevance="none",
                        skill_bearing=False,
                        reason_code="SOCIAL",
                    )
                ]
            }
        )
    )
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "NON_LEARNING"
    assert provider.embed_calls == []
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
    provider = FakeProvider(
        route_responder(
            qualification={"segments": [segment(LOOP_QUESTION)]},
            rerank=rerank_identity,
            mapping=mapper_choosing("For Loops over Lists", 0.5),
        )
    )
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "ABSTAINED"
    assert fetch(
        db_pool,
        "select d.outcome::text, d.abstain_reason, m.status::text, m.status_reason from public.mapping_decisions d "
        "join public.skill_mappings m on m.decision_id = d.id where d.learner_id = %s",
        learner,
    ) == [("ABSTAINED", "LOW_CONFIDENCE", "ABSTAINED", "LOW_CONFIDENCE")]


def test_adjudicated_band_mapping_is_accepted(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, LOOP_QUESTION)

    def adjudicate(messages):
        skill_id = re.search(r"id=([0-9a-f-]{36})", messages[-1].content).group(1)
        return {
            "adjudications": [
                {
                    "skill_id": skill_id,
                    "verdict": "CONFIRM",
                    "confidence": 0.9,
                    "reason_code": "DIRECT_MATCH",
                }
            ]
        }

    provider = FakeProvider(
        route_responder(
            qualification={"segments": [segment(LOOP_QUESTION)]},
            rerank=rerank_identity,
            mapping=mapper_choosing("For Loops over Lists", 0.7),
            adjudication=adjudicate,
        )
    )
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "MAPPED"
    (row,) = fetch(
        db_pool,
        "select m.status::text, m.adjudicated, m.first_pass_confidence, m.confidence, d.adjudication_model_run_id "
        "is not null from public.skill_mappings m join public.mapping_decisions d on d.id = m.decision_id "
        "where m.learner_id = %s",
        learner,
    )
    assert (
        row[0:2] == ("ACCEPTED", True)
        and row[2] == pytest.approx(0.7)
        and row[3] == pytest.approx(0.9)
    )
    assert row[4] is True


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
    provider = FakeProvider(
        route_responder(
            qualification={"segments": [segment(text)]},
            rerank=rerank_identity,
            mapping={"mappings": [], "new_skill_candidate": new_skill},
        )
    )
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
            qualification={"segments": [segment("What is wrong with this code?", intent="solve")]}
        )
    )
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "UNCERTAIN"
    assert "Context incomplete: yes" in provider.calls[0]["messages"][-1].content
    assert fetch(
        db_pool,
        "select route::text, route_reason, context_incomplete from public.activity_segments where learner_id = %s",
        learner,
    ) == [("UNCERTAIN", "CONTEXT_INCOMPLETE", True)]
    assert provider.embed_calls == []


def test_invalid_qualification_output_abstains_with_provenance(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    user_id, assistant_id = ingest_turn(db_pool, learner, "Explain recursion", "Recursion is ...")
    provider = FakeProvider(route_responder(qualification=["{oops", "still not json"]))
    assert run_assistant_job(db_pool, provider, assistant_id).outcome == "UNCERTAIN"
    (seg,) = fetch(
        db_pool,
        "select route::text, route_reason, text, learning_relevance, source_message_ids, qualification_model_run_id "
        "from public.activity_segments where learner_id = %s",
        learner,
    )
    assert seg[0:2] == ("UNCERTAIN", "MODEL_OUTPUT_INVALID") and seg[3] is None
    assert seg[2] == "Learner: Explain recursion\n\nAssistant: Recursion is ..."
    assert seg[4] == [user_id, assistant_id]
    assert fetch(
        db_pool, "select status::text, attempt from public.model_runs where id = %s", seg[5]
    ) == [("INVALID_OUTPUT", 2)]


def test_rerun_is_idempotent(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, LOOP_QUESTION)
    provider = FakeProvider(
        route_responder(
            qualification={"segments": [segment(LOOP_QUESTION)]},
            rerank=rerank_identity,
            mapping=mapper_choosing("For Loops over Lists", 0.95),
        )
    )
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


def test_user_message_pairing_rules(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    fresh_user, _ = ingest_turn(db_pool, learner, "A question with no reply yet", None)
    provider = FakeProvider(route_responder(qualification={"segments": [segment("old question")]}))
    job = job_for(db_pool, fresh_user, JOB_PROCESS_RAW_MESSAGE)
    result = process_raw_message_job(db_pool, gateway_for(db_pool, provider), job)
    assert (result.outcome, result.defer_seconds) == ("AWAITING_ASSISTANT", 20)

    old_user, _ = ingest_turn(
        db_pool,
        learner,
        "An old question whose reply was never captured",
        None,
        captured_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    provider = FakeProvider(
        route_responder(
            qualification={
                "segments": [segment("old question", relevance="none", skill_bearing=False)]
            }
        )
    )
    job = job_for(db_pool, old_user, JOB_PROCESS_RAW_MESSAGE)
    assert (
        process_raw_message_job(db_pool, gateway_for(db_pool, provider), job).outcome
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
            db_pool, gateway, job_for(db_pool, raw_id, JOB_PROCESS_RAW_MESSAGE)
        )

    assert run(stored[0].raw_message_id).outcome == "ORPHAN_ASSISTANT"
    assert run(stored[1].raw_message_id).outcome == "SUPERSEDED_REVISION"


def test_model_outage_leaves_the_job_retryable_and_raw_data_safe(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    user_id, assistant_id = ingest_turn(db_pool, learner, LOOP_QUESTION)
    provider = FakeProvider(route_responder(qualification=ProviderError("unavailable", "HTTP_503")))
    worker = build_worker(db_pool, gateway_for(db_pool, provider), batch_size=10)
    worker.drain()
    states = dict(
        fetch(
            db_pool,
            "select entity_id, state::text from public.processing_jobs where learner_id = %s",
            learner,
        )
    )
    assert states[assistant_id] == "RETRY_WAIT" and states[user_id] == "COMPLETED"
    assert fetch(
        db_pool, "select count(*) from public.activity_segments where learner_id = %s", learner
    ) == [(0,)]
    assert fetch(
        db_pool, "select count(*) from public.raw_messages where learner_id = %s", learner
    ) == [(2,)]
    assert fetch(
        db_pool, "select status::text from public.model_runs where learner_id = %s", learner
    ) == [("UNAVAILABLE",)]
