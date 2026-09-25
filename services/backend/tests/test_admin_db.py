"""P7 admin operations against PostgreSQL (ADR 0008): job retry and resume, candidate review,
enrollment, model runs, benchmark runs and lookup. Every mutation is idempotent, audited and
model-free; the worker runs the retried / re-armed jobs on a scripted fake provider.
"""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Jsonb

from app.intelligence.policy import load_policy
from app.intelligence.processing.pipeline import process_raw_message_job
from app.intelligence.retrieval.engine import retrieve_pool
from app.intelligence.skill_graph.jobs import run_bootstrap_job, run_embed_skill_job
from app.jobs.queue import (
    JOB_BOOTSTRAP_COURSE_GRAPH,
    JOB_EMBED_SKILL,
    JOB_PROCESS_RAW_MESSAGE,
    fail_job,
)
from app.jobs.worker import Worker
from app.model_gateway import ProviderError, RunContext
from tests.conftest import api_client, job_for
from tests.fakes import (
    FakeProvider,
    candidate_ids_in,
    graph_proposal,
    route_responder,
    turn_segment,
)
from tests.test_courses_api import auth
from tests.test_evidence_pipeline_db import REPLY, STUDENT_TURN, mixed_turn_provider
from tests.test_pipeline_db import (
    PYTHON_SKILLS,
    bootstrapped_course,
    fetch,
    gateway_for,
    ingest_turn,
)
from tests.test_skill_graph_db import new_course

pytestmark = pytest.mark.db


def set_role(pool, user: UUID, role: str) -> None:
    with pool.connection() as conn:
        conn.execute("update public.profiles set role = %s where id = %s", (role, user))


@pytest.fixture
def admin(db_pool, new_learner) -> UUID:
    user = new_learner()
    set_role(db_pool, user, "ADMIN")
    return user


@pytest.fixture
def client(db_pool, verifier):
    return api_client(verifier, db_pool)


@pytest.fixture
def embed_jobs(db_pool):
    """EMBED_SKILL jobs have no learner (no cascade): remove the ones a test creates."""
    created: list[UUID] = []
    yield created
    with db_pool.connection() as conn:
        conn.execute(
            "delete from public.processing_jobs where job_type = 'EMBED_SKILL' "
            "and entity_id = any(%s)",
            (created,),
        )


def post(client, make_token, user, path, body=None, key=None):
    headers = auth(make_token(user))
    headers["Idempotency-Key"] = key or uuid4().hex
    return client.post(path, json=body, headers=headers)


def audit_rows(pool, entity_id):
    return fetch(
        pool,
        "select action::text, actor_type::text, actor_role::text, client_request_id, metadata "
        "from public.audit_events where entity_id = %s order by created_at",
        entity_id,
    )


def failed_turn_job(pool, learner) -> UUID:
    """A raw-message job whose attempts are exhausted (FAILED), as the worker leaves it."""
    user_id, _ = ingest_turn(pool, learner, "Explain binary search in one sentence.", None)
    with pool.connection() as conn:
        (job_id,) = conn.execute(
            "update public.processing_jobs set attempts = max_attempts where entity_id = %s "
            "and job_type = 'PROCESS_RAW_MESSAGE' returning id",
            (user_id,),
        ).fetchone()
        fail_job(conn, job_id, "RuntimeError: boom for p7-admin@test.invalid key AIza" + "x" * 35)
    return job_id


# --- Retry ----------------------------------------------------------------------------------


def test_a_failed_job_is_retried_once_per_key_and_audited(
    db_pool, new_learner, admin, client, make_token
) -> None:
    learner = new_learner()
    job_id = failed_turn_job(db_pool, learner)
    listed = client.get("/v1/admin/jobs?state=FAILED", headers=auth(make_token(admin))).json()
    row = next(j for j in listed["jobs"] if j["id"] == str(job_id))
    assert row["retry_mode"] == "RETRY" and row["retry_blocked"] is None
    assert "AIza" not in row["last_error"] and "@test.invalid" not in row["last_error"]
    assert "[redacted-api-key]" in row["last_error"]

    key = f"retry-{uuid4().hex}"
    first = post(
        client, make_token, admin, f"/v1/admin/jobs/{job_id}/retry", {"mode": "RETRY"}, key
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["replayed"] is False and body["mode"] == "RETRY"
    job = body["job"]
    assert (job["state"], job["attempts"], job["manual_retry_count"], job["outcome"]) == (
        "PENDING",
        0,
        1,
        "MANUAL_RETRY",
    )
    assert job["last_error"] is None and job["last_manual_retry_at"] is not None
    [audit] = audit_rows(db_pool, job_id)
    assert audit[:4] == ("JOB_RETRY", "USER", "ADMIN", key)
    assert audit[4]["previous_state"] == "FAILED" and audit[4]["manual_retry_count"] == 1

    replay = post(
        client, make_token, admin, f"/v1/admin/jobs/{job_id}/retry", {"mode": "RETRY"}, key
    )
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["audit_event_id"] == body["audit_event_id"]
    assert fetch(
        db_pool, "select manual_retry_count from public.processing_jobs where id = %s", job_id
    ) == [(1,)]
    assert len(audit_rows(db_pool, job_id)) == 1
    other = post(
        client,
        make_token,
        admin,
        f"/v1/admin/jobs/{job_id}/retry",
        {"mode": "RESUME_ATTRIBUTION"},
        key,
    )
    assert other.status_code == 409
    again = post(client, make_token, admin, f"/v1/admin/jobs/{job_id}/retry")
    assert again.status_code == 409 and again.json()["detail"].startswith("NOT_FAILED")
    assert post(client, make_token, admin, f"/v1/admin/jobs/{uuid4()}/retry").status_code == 404


def test_retries_are_capped_and_admin_only(db_pool, new_learner, admin, client, make_token) -> None:
    learner = new_learner()
    job_id = failed_turn_job(db_pool, learner)
    assert post(client, make_token, learner, f"/v1/admin/jobs/{job_id}/retry").status_code == 403
    assert (
        client.post(f"/v1/admin/jobs/{job_id}/retry", headers={"Idempotency-Key": "k"}).status_code
        == 401
    )
    for attempt in range(5):
        response = post(client, make_token, admin, f"/v1/admin/jobs/{job_id}/retry")
        assert response.status_code == 200, (attempt, response.text)
        with db_pool.connection() as conn:
            conn.execute(
                "update public.processing_jobs set state = 'FAILED' where id = %s", (job_id,)
            )
    blocked = post(client, make_token, admin, f"/v1/admin/jobs/{job_id}/retry")
    assert blocked.status_code == 409 and blocked.json()["detail"].startswith("RETRY_LIMIT_REACHED")
    job = client.get(f"/v1/admin/jobs/{job_id}", headers=auth(make_token(admin))).json()
    assert (job["manual_retry_count"], job["retry_mode"], job["retry_blocked"]) == (
        5,
        None,
        "RETRY_LIMIT_REACHED",
    )
    assert len(audit_rows(db_pool, job_id)) == 5


def test_a_bootstrap_retry_resumes_the_graph_stage_without_regenerating(
    db_pool, registry, new_learner, admin, client, make_token
) -> None:
    """N3: after an embedding failure the course is FAILED; the retry embeds only (graph v1)."""
    learner = new_learner()
    course_id = new_course(db_pool, learner)
    names = [f"{registry}{n}" for n in PYTHON_SKILLS]
    provider = FakeProvider(
        route_responder(graph=[graph_proposal(names, prefix=registry)]),
        embed_error=ProviderError("boom", "HTTP_500"),
    )
    gateway = gateway_for(db_pool, provider)
    job = job_for(db_pool, course_id, JOB_BOOTSTRAP_COURSE_GRAPH)
    worker = Worker(
        db_pool, {JOB_BOOTSTRAP_COURSE_GRAPH: lambda j: run_bootstrap_job(db_pool, gateway, j)}
    )
    from app.intelligence.skill_graph.jobs import mark_bootstrap_failed

    with db_pool.connection() as conn:
        conn.execute(
            "update public.processing_jobs set attempts = max_attempts where id = %s", (job.id,)
        )
    worker._execute(job_for(db_pool, course_id, JOB_BOOTSTRAP_COURSE_GRAPH))  # noqa: SLF001
    mark_bootstrap_failed(db_pool, job, "ProviderError: boom", final=True)
    assert fetch(
        db_pool,
        "select graph_status::text, graph_version from public.courses where id = %s",
        course_id,
    ) == [("FAILED", 1)]
    assert len(provider.calls) == 1

    response = post(client, make_token, admin, f"/v1/admin/jobs/{job.id}/retry")
    assert response.status_code == 200 and response.json()["stage_restored"] == "EMBEDDING"
    provider.embed_error = None
    assert (
        run_bootstrap_job(
            db_pool, gateway, job_for(db_pool, course_id, JOB_BOOTSTRAP_COURSE_GRAPH)
        ).outcome
        == "GRAPH_READY"
    )
    assert len(provider.calls) == 1  # 0 generation requests for the retry: no graph v2
    assert fetch(
        db_pool,
        "select graph_status::text, graph_version from public.courses where id = %s",
        course_id,
    ) == [("READY", 1)]


def test_a_bootstrap_that_never_generated_restarts_from_pending(
    db_pool, new_learner, admin, client, make_token
) -> None:
    learner = new_learner()
    course_id = new_course(db_pool, learner)
    job = job_for(db_pool, course_id, JOB_BOOTSTRAP_COURSE_GRAPH)
    with db_pool.connection() as conn:
        conn.execute(
            "update public.processing_jobs set attempts = max_attempts where id = %s", (job.id,)
        )
        fail_job(conn, job.id, "ModelOutputInvalidError: graph invalid")
        conn.execute(
            "update public.courses set graph_status = 'FAILED' where id = %s", (course_id,)
        )
    response = post(client, make_token, admin, f"/v1/admin/jobs/{job.id}/retry")
    assert response.status_code == 200 and response.json()["stage_restored"] == "PENDING"
    assert fetch(
        db_pool,
        "select graph_status::text, graph_error from public.courses where id = %s",
        course_id,
    ) == [("PENDING", None)]


def test_a_closed_verification_is_not_retried(
    db_pool, registry, new_learner, admin, client, make_token
) -> None:
    learner = new_learner()
    course_id = bootstrapped_course(db_pool, learner, registry)
    (skill,) = fetch(
        db_pool,
        "select cs.skill_id from public.course_skills cs join public.skill_nodes n "
        "on n.id = cs.skill_id where cs.course_id = %s and n.node_kind = 'SKILL' limit 1",
        course_id,
    )[0]
    with db_pool.connection() as conn:
        (session,) = conn.execute(
            """
            insert into public.verification_sessions
                (learner_id, skill_id, trigger_type, reason_code, planned_difficulty, difficulty_min,
                 difficulty_max, plan_day, plan_timezone, planner_version, planning_inputs)
            values (%s, %s, 'VERIFY', 'REPEATED_DELEGATION_UNVERIFIED', 0.5, 0.35, 0.65, current_date,
                    'UTC', 'verification-planner/p6-v1', '{}')
            returning id
            """,
            (learner, skill),
        ).fetchone()
        conn.execute(
            "update public.verification_sessions set failure_code = 'GENERATION_FAILED', "
            "failed_at = now() where id = %s",
            (session,),
        )
        (job_id,) = conn.execute(
            """
            insert into public.processing_jobs (job_type, entity_type, entity_id, learner_id, state,
                                                attempts, last_error)
            values ('GENERATE_VERIFICATION', 'verification_session', %s, %s, 'FAILED', 3, 'x')
            returning id
            """,
            (session, learner),
        ).fetchone()
    response = post(client, make_token, admin, f"/v1/admin/jobs/{job_id}/retry")
    assert response.status_code == 409
    assert response.json()["detail"].startswith("VERIFICATION_CLOSED")
    assert audit_rows(db_pool, job_id) == []


def test_resume_attribution_is_one_request_and_never_duplicates(
    db_pool, registry, new_learner, admin, client, make_token
) -> None:
    """K7: a turn analysed without attribution (P3A only, as before migration 0005) resumes at
    the attribution: exactly one SKILL_ATTRIBUTION request, no P3A call, no duplicate row."""
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    provider = mixed_turn_provider(db_pool, registry)
    gateway = gateway_for(db_pool, provider)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    job = job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE)
    process_raw_message_job(db_pool, gateway, job, evidence=False)
    with db_pool.connection() as conn:
        conn.execute(
            "update public.processing_jobs set state = 'COMPLETED', completed_at = now(), "
            "outcome = 'MAPPED' where id = %s",
            (job.id,),
        )
    assert fetch(
        db_pool, "select count(*) from public.attributions where learner_id = %s", learner
    ) == [(0,)]
    listed = client.get(f"/v1/admin/jobs/{job.id}", headers=auth(make_token(admin))).json()
    assert listed["retry_mode"] == "RESUME_ATTRIBUTION"
    # The paired user message's job deferred to the assistant's: it has nothing to resume.
    user_job = fetch(
        db_pool,
        "select j.id from public.processing_jobs j join public.raw_messages m on m.id = j.entity_id "
        "where m.learner_id = %s and m.role = 'user'",
        learner,
    )[0][0]
    with db_pool.connection() as conn:
        conn.execute(
            "update public.processing_jobs set state = 'COMPLETED', completed_at = now(), "
            "outcome = 'DEFERRED_TO_ASSISTANT' where id = %s",
            (user_job,),
        )
    assert (
        client.get(f"/v1/admin/jobs/{user_job}", headers=auth(make_token(admin))).json()[
            "retry_mode"
        ]
        is None
    )

    wrong = post(client, make_token, admin, f"/v1/admin/jobs/{job.id}/retry", {"mode": "RETRY"})
    assert wrong.status_code == 409 and wrong.json()["detail"].startswith("NOT_FAILED")
    resumed = post(
        client, make_token, admin, f"/v1/admin/jobs/{job.id}/retry", {"mode": "RESUME_ATTRIBUTION"}
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["job"]["state"] == "PENDING"
    assert audit_rows(db_pool, job.id)[0][0] == "JOB_RESUME_ATTRIBUTION"

    calls = len(provider.calls)
    result = process_raw_message_job(
        db_pool, gateway, job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE)
    )
    assert result.outcome == "EVIDENCE_RECORDED"
    assert len(provider.calls) - calls == 1
    assert "contribution attributor" in provider.calls[-1]["system"]
    counts = fetch(
        db_pool,
        "select (select count(*) from public.attributions where learner_id = %s), "
        "(select count(*) from public.evidence_events where learner_id = %s), "
        "(select count(*) from public.activity_segments where learner_id = %s)",
        learner,
        learner,
        learner,
    )
    assert counts == [(2, 2, 1)]
    with db_pool.connection() as conn:
        conn.execute(
            "update public.processing_jobs set state = 'COMPLETED', completed_at = now() where id = %s",
            (job.id,),
        )
    assert (
        client.get(f"/v1/admin/jobs/{job.id}", headers=auth(make_token(admin))).json()["retry_mode"]
        is None
    )
    again = process_raw_message_job(
        db_pool, gateway, job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE)
    )
    assert again.outcome == "ALREADY_ANALYZED" and len(provider.calls) - calls == 1
    assert fetch(
        db_pool,
        "select (select count(*) from public.attributions where learner_id = %s), "
        "(select count(*) from public.evidence_events where learner_id = %s)",
        learner,
        learner,
    ) == [(2, 2)]


# --- Candidate review ------------------------------------------------------------------------


def new_candidate(
    pool,
    name: str,
    *,
    description: str | None = "A skill proposed by a turn.",
    course: UUID | None = None,
    parent: UUID | None = None,
) -> UUID:
    from app.intelligence.skill_graph.canonical import skill_key

    with pool.connection() as conn:
        (candidate,) = conn.execute(
            """
            insert into public.skill_candidates
                (canonical_name, normalized_name, description, first_course_id, parent_candidate_id)
            values (%s, %s, %s, %s, %s) returning id
            """,
            (name, skill_key(name), description, course, parent),
        ).fetchone()
    return candidate


def test_approve_creates_a_mappable_skill_through_the_controlled_path(
    db_pool, registry, new_learner, admin, client, make_token, embed_jobs
) -> None:
    learner = new_learner()
    course_id = bootstrapped_course(db_pool, learner, registry)
    name = f"{registry}Polars LazyFrame Queries"
    candidate = new_candidate(
        db_pool, name, description="Build lazy query plans with Polars.", course=course_id
    )
    queue = client.get("/v1/admin/skill-candidates", headers=auth(make_token(admin))).json()
    assert str(candidate) in {c["id"] for c in queue["candidates"]}
    runs_before = fetch(db_pool, "select count(*) from public.model_runs")[0][0]

    key = f"approve-{uuid4().hex}"
    body = {"action": "APPROVE", "difficulty_band": 3, "note": "Real course topic."}
    response = post(
        client, make_token, admin, f"/v1/admin/skill-candidates/{candidate}/review", body, key
    )
    assert response.status_code == 200, response.text
    result = response.json()
    skill_id = UUID(result["skill"]["id"])
    embed_jobs.append(skill_id)
    assert result["candidate"]["status"] == "APPROVED"
    assert result["candidate"]["resolved_skill"]["id"] == str(skill_id)
    node = fetch(
        db_pool,
        "select status::text, source::text, node_kind::text, difficulty_band, source_course_id "
        "from public.skill_nodes where id = %s",
        skill_id,
    )
    assert node == [("ACTIVE", "CANDIDATE_APPROVAL", "SKILL", 3, course_id)]
    assert fetch(
        db_pool,
        "select importance, source::text from public.course_skills where course_id = %s and skill_id = %s",
        course_id,
        skill_id,
    ) == [(0.5, "CANDIDATE_APPROVAL")]
    assert fetch(
        db_pool,
        "select state::text from public.processing_jobs where id = %s",
        UUID(result["embed_job_id"]),
    ) == [("PENDING",)]
    [audit] = audit_rows(db_pool, candidate)
    assert audit[0] == "CANDIDATE_APPROVE" and audit[4]["course_overlay"] is True
    assert fetch(db_pool, "select count(*) from public.model_runs")[0][0] == runs_before

    replay = post(
        client, make_token, admin, f"/v1/admin/skill-candidates/{candidate}/review", body, key
    )
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert fetch(
        db_pool, "select count(*) from public.skill_nodes where canonical_name = %s", name
    ) == [(1,)]
    again = post(client, make_token, admin, f"/v1/admin/skill-candidates/{candidate}/review", body)
    assert again.status_code == 409 and again.json()["detail"].startswith("ALREADY_REVIEWED")

    # The worker embeds it (one embedding request) and retrieval now finds it.
    provider = FakeProvider()
    gateway = gateway_for(db_pool, provider)
    embed_job = job_for(db_pool, skill_id, JOB_EMBED_SKILL)
    assert run_embed_skill_job(db_pool, gateway, embed_job).outcome == "SKILL_EMBEDDED"
    assert run_embed_skill_job(db_pool, gateway, embed_job).outcome == "ALREADY_EMBEDDED"
    assert len(provider.embed_calls) == 1 and provider.calls == []
    with db_pool.connection() as conn:
        pool = retrieve_pool(
            conn,
            gateway,
            query_text="How do I write Polars LazyFrame queries with scan_csv?",
            course_ids=[course_id],
            policy=load_policy(conn).retrieval,
            context=RunContext(trace_id="test:p7"),
        )
    assert str(skill_id) in {str(c.skill_id) for c in pool.candidates}


def test_approve_refuses_a_taken_name_or_a_missing_description(
    db_pool, registry, new_learner, admin, client, make_token
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    taken = new_candidate(db_pool, f"{registry}While Loops")
    response = post(
        client,
        make_token,
        admin,
        f"/v1/admin/skill-candidates/{taken}/review",
        {"action": "APPROVE"},
    )
    assert response.status_code == 409 and response.json()["detail"].startswith("NAME_TAKEN")
    bare = new_candidate(db_pool, f"{registry}Pandas Pivot Tables", description=None)
    missing = post(
        client,
        make_token,
        admin,
        f"/v1/admin/skill-candidates/{bare}/review",
        {"action": "APPROVE"},
    )
    assert missing.status_code == 422 and missing.json()["detail"].startswith(
        "DESCRIPTION_REQUIRED"
    )
    shape = post(
        client, make_token, admin, f"/v1/admin/skill-candidates/{bare}/review", {"action": "MERGE"}
    )
    assert shape.status_code == 422
    assert fetch(
        db_pool,
        "select status::text from public.skill_candidates where id = any(%s) order by id",
        [taken, bare],
    ) == [("PENDING_REVIEW",), ("PENDING_REVIEW",)]


def test_merge_adds_an_alias_and_re_embeds_the_target(
    db_pool, registry, new_learner, admin, client, make_token, embed_jobs
) -> None:
    learner = new_learner()
    course_id = bootstrapped_course(db_pool, learner, registry)
    (target,) = fetch(
        db_pool,
        "select id from public.skill_nodes where canonical_name = %s",
        f"{registry}While Loops",
    )[0]
    embed_jobs.append(target)
    candidate = new_candidate(db_pool, f"{registry}Indefinite Iteration", course=course_id)
    response = post(
        client,
        make_token,
        admin,
        f"/v1/admin/skill-candidates/{candidate}/review",
        {"action": "MERGE", "target_skill_id": str(target)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["candidate"]["status"] == "MERGED"
    assert fetch(
        db_pool,
        "select skill_id, alias_kind::text, source::text from public.skill_aliases where alias = %s",
        f"{registry}Indefinite Iteration",
    ) == [(target, "VARIANT", "CANDIDATE_APPROVAL")]
    provider = FakeProvider()
    gateway = gateway_for(db_pool, provider)
    with db_pool.connection() as conn:
        # The bootstrap embedded the target before the alias existed: its text changed.
        before = conn.execute(
            "select content_hash from public.skill_embeddings where skill_id = %s", (target,)
        ).fetchone()
    assert (
        run_embed_skill_job(db_pool, gateway, job_for(db_pool, target, JOB_EMBED_SKILL)).outcome
        == "SKILL_EMBEDDED"
    )
    after = fetch(
        db_pool, "select content_hash from public.skill_embeddings where skill_id = %s", target
    )
    assert before is not None and after[0][0] != before[0]
    missing = post(
        client,
        make_token,
        admin,
        f"/v1/admin/skill-candidates/{new_candidate(db_pool, f'{registry}Other Name')}/review",
        {"action": "MERGE", "target_skill_id": str(uuid4())},
    )
    assert missing.status_code == 422


def test_a_rejected_name_never_comes_back_for_review(
    db_pool, registry, new_learner, admin, client, make_token
) -> None:
    """N4: a later proposal of a rejected name counts on the rejected row."""
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
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

    def propose():
        _, assistant_id = ingest_turn(db_pool, learner, text)
        process_raw_message_job(
            db_pool,
            gateway_for(db_pool, provider),
            job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE),
            evidence=False,
        )

    propose()
    [(candidate,)] = fetch(
        db_pool,
        "select id from public.skill_candidates where canonical_name = %s",
        new_skill["canonical_name"],
    )
    rejected = post(
        client,
        make_token,
        admin,
        f"/v1/admin/skill-candidates/{candidate}/review",
        {"action": "REJECT", "note": "Not part of this course."},
    )
    assert rejected.status_code == 200 and rejected.json()["candidate"]["status"] == "REJECTED"
    propose()
    propose()
    assert fetch(
        db_pool,
        "select id, status::text, occurrences from public.skill_candidates where canonical_name = %s",
        new_skill["canonical_name"],
    ) == [(candidate, "REJECTED", 3)]
    assert fetch(
        db_pool,
        "select count(*) from public.mapping_decisions where learner_id = %s and new_skill_candidate_id = %s",
        learner,
        candidate,
    ) == [(3,)]
    pending = client.get("/v1/admin/skill-candidates", headers=auth(make_token(admin))).json()
    assert str(candidate) not in {c["id"] for c in pending["candidates"]}


# --- Enrollment, model runs, benchmark, lookup ------------------------------------------------


def test_enrollment_is_idempotent_and_guards_teacher_memberships(
    db_pool, new_learner, admin, client, make_token
) -> None:
    owner, student, teacher = new_learner(), new_learner(), new_learner()
    set_role(db_pool, teacher, "TEACHER")
    course_id = new_course(db_pool, owner)
    email = fetch(db_pool, "select email from auth.users where id = %s", student)[0][0]
    path = f"/v1/admin/courses/{course_id}/members"
    key = f"enroll-{uuid4().hex}"
    added = post(client, make_token, admin, path, {"email": email.upper(), "role": "STUDENT"}, key)
    assert added.status_code == 201, added.text
    assert added.json()["member"]["user_id"] == str(student) and added.json()["created"] is True
    replay = post(client, make_token, admin, path, {"email": email.upper(), "role": "STUDENT"}, key)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert (
        post(
            client, make_token, admin, path, {"user_id": str(student), "role": "TEACHER"}
        ).status_code
        == 409
    )
    refused = post(client, make_token, admin, path, {"user_id": str(owner), "role": "TEACHER"})
    assert refused.status_code == 409  # the owner is already a STUDENT member
    stranger = new_learner()
    no_role = post(client, make_token, admin, path, {"user_id": str(stranger), "role": "TEACHER"})
    assert no_role.status_code == 422 and no_role.json()["detail"].startswith(
        "TEACHER_ROLE_REQUIRED"
    )
    ok = post(client, make_token, admin, path, {"user_id": str(teacher), "role": "TEACHER"})
    assert ok.status_code == 201
    assert (
        post(
            client, make_token, admin, path, {"email": "nobody@test.invalid", "role": "STUDENT"}
        ).status_code
        == 404
    )
    assert (
        post(
            client,
            make_token,
            admin,
            f"/v1/admin/courses/{uuid4()}/members",
            {"user_id": str(student), "role": "STUDENT"},
        ).status_code
        == 404
    )
    detail = client.get(f"/v1/admin/courses/{course_id}", headers=auth(make_token(admin))).json()
    assert {(m["user_id"], m["role"]) for m in detail["members"]} == {
        (str(owner), "STUDENT"),
        (str(student), "STUDENT"),
        (str(teacher), "TEACHER"),
    }
    assert (detail["course"]["student_count"], detail["course"]["teacher_count"]) == (2, 1)
    assert detail["bootstrap_job"]["job_type"] == "BOOTSTRAP_COURSE_GRAPH"
    assert [a[0] for a in audit_rows(db_pool, course_id)] == ["COURSE_MEMBER_ADD"] * 2


def test_model_runs_never_expose_output_and_redact_errors(
    db_pool, admin, client, make_token
) -> None:
    run_id = uuid4()
    with db_pool.connection() as conn:
        conn.execute(
            """
            insert into public.model_runs (id, trace_id, task_type, provider, model, prompt_version,
                                           input_hash, output, latency_ms, status, error_code,
                                           error_message)
            values (%s, 'job:p7', 'TURN_ANALYSIS', 'google', 'gemini-3.5-flash-lite',
                    'turn-analysis/v1', %s, %s, 12, 'FAILED', 'HTTP_400',
                    'request to https://x/?key=AIzaSyA1234567890123456789012345678901 failed for '
                    || 'someone@example.com via postgresql://u:pw@db:5432/postgres')
            """,
            (run_id, "a" * 64, Jsonb({"secret": "model output"})),
        )
    try:
        response = client.get(
            "/v1/admin/model-runs?failures_only=true", headers=auth(make_token(admin))
        )
        assert response.status_code == 200
        assert "model output" not in response.text and '"output"' not in response.text
        run = next(r for r in response.json()["runs"] if r["id"] == str(run_id))
        for leaked in ("AIza", "someone@example.com", "u:pw@"):
            assert leaked not in run["error_message"]
        budget = {b["model"] for b in response.json()["budget"]}
        assert {"gemini-3.7-flash", "gemini-3.8-flash"} <= budget
    finally:
        with db_pool.connection() as conn:
            conn.execute("delete from public.model_runs where id = %s", (run_id,))


def test_benchmark_runs_and_the_overview(db_pool, admin, client, make_token) -> None:
    run_id = uuid4()
    now = datetime.now(UTC)
    with db_pool.connection() as conn:
        conn.execute(
            """
            insert into public.benchmark_runs (id, set_name, set_version, mode, policy_hash,
                                               case_count, passed_count, failed_count, hard_gates,
                                               metrics, verdict, report, started_at, finished_at)
            values (%s, 'critical-gate', 'v1', 'DETERMINISTIC', %s, 120, 120, 0,
                    '{"false_debt": {"value": 0, "threshold": 0, "pass": true}}', '{}', 'PASS',
                    '{"families": {"REL": {"cases": 14, "passed": 14}}}', %s, %s)
            """,
            (run_id, "b" * 64, now, now),
        )
    try:
        listed = client.get("/v1/admin/benchmark", headers=auth(make_token(admin))).json()
        assert listed["latest"]["DETERMINISTIC"]["id"] == str(run_id)
        assert "report" not in listed["runs"][0]
        detail = client.get(f"/v1/admin/benchmark/{run_id}", headers=auth(make_token(admin))).json()
        assert detail["report"]["families"]["REL"]["passed"] == 14
        overview = client.get("/v1/admin/overview", headers=auth(make_token(admin)))
        assert overview.status_code == 200
        body = overview.json()
        assert body["latest_benchmark"]["verdict"] in ("PASS", "FAIL")
        assert body["totals"]["learners"] >= 1 and "jobs_by_state" in body
    finally:
        with db_pool.connection() as conn:
            conn.execute("delete from public.benchmark_runs where id = %s", (run_id,))


def test_a_stored_fail_verdict_is_shown_with_its_parts_never_rewritten(
    db_pool, admin, client, make_token
) -> None:
    """P9: LIVE 71/72 FAIL with every hard gate held reads 'hard gates PASS, 1 provider /
    transport failure, stored verdict FAIL' - and the row stays FAIL."""
    run_id = uuid4()
    now = datetime.now(UTC)
    gates = json.dumps({f"g{n}": {"value": 0, "threshold": 0, "pass": True} for n in range(13)})
    report = json.dumps(
        {
            "failing": ["REL-06"],
            "blocked": [],
            "families": {"REL": {"cases": 14, "passed": 13, "blocked": 0, "hard_failures": 0}},
            "errors": {"REL-06": "ModelUnavailableError(TRANSPORT)"},
        }
    )
    with db_pool.connection() as conn:
        conn.execute(
            """
            insert into public.benchmark_runs (id, set_name, set_version, mode, provider, model,
                                               policy_hash, case_count, passed_count, failed_count,
                                               hard_gates, metrics, verdict, report, started_at,
                                               finished_at)
            values (%s, 'critical-gate', 'v1', 'LIVE', 'google', 'gemini-3.5-flash-lite', %s, 72,
                    71, 1, %s, '{}', 'FAIL', %s, %s, %s)
            """,
            (run_id, "c" * 64, gates, report, now, now),
        )
    try:
        headers = auth(make_token(admin))
        listed = client.get("/v1/admin/benchmark", headers=headers).json()
        (run,) = [r for r in listed["runs"] if r["id"] == str(run_id)]
        assert run["verdict"] == "FAIL" and (run["passed_count"], run["case_count"]) == (71, 72)
        assert run["hard_gates_total"] == 13 and run["hard_gates_failed"] == []
        assert run["failed_without_hard_gate"] == 1 and run["hard_gate_failure_cases"] == 0
        assert run["provider_failure_cases"] == ["REL-06"]
        assert run["case_errors"] == {"REL-06": "ModelUnavailableError(TRANSPORT)"}
        assert "report" not in run
        detail = client.get(f"/v1/admin/benchmark/{run_id}", headers=headers).json()
        assert detail["provider_failure_cases"] == ["REL-06"] and detail["verdict"] == "FAIL"
        (stored,) = conn_rows(db_pool, run_id)
        assert stored == ("FAIL", 71, 1)  # presentation never writes the row
    finally:
        with db_pool.connection() as conn:
            conn.execute("delete from public.benchmark_runs where id = %s", (run_id,))


def conn_rows(pool, run_id):
    with pool.connection() as conn:
        return conn.execute(
            "select verdict::text, passed_count, failed_count from public.benchmark_runs where id = %s",
            (run_id,),
        ).fetchall()


def test_course_and_skill_lookup(db_pool, registry, new_learner, admin, client, make_token) -> None:
    learner = new_learner()
    course_id = bootstrapped_course(db_pool, learner, registry)
    headers = auth(make_token(admin))
    courses = client.get(f"/v1/admin/courses?q={course_id}", headers=headers).json()["courses"]
    assert [c["id"] for c in courses] == [str(course_id)]
    skills = client.get(f"/v1/admin/skills?q={registry.strip()}%20While", headers=headers).json()
    [skill] = [s for s in skills["skills"] if s["canonical_name"] == f"{registry}While Loops"]
    assert skill["course_count"] == 1 and skill["embedded"] is True
    detail = client.get(f"/v1/admin/skills/{skill['id']}", headers=headers).json()
    assert detail["courses"][0]["id"] == str(course_id)
    assert client.get(f"/v1/admin/skills/{uuid4()}", headers=headers).status_code == 404
    assert client.get("/v1/admin/skills", headers=auth(make_token(learner))).status_code == 403
