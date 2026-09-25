"""P8 hardening against PostgreSQL (ADR 0008 H3, H15): a worker that crashes at a stage boundary,
a correction interrupted mid-recompute, and replays after a correction. The retry always ends
where an uninterrupted run would, with no duplicate row and no lost correction."""

from uuid import uuid4

import pytest

from app.intelligence.processing.pipeline import process_raw_message_job
from app.jobs.queue import JOB_PROCESS_RAW_MESSAGE
from app.jobs.worker import build_worker
from tests.conftest import api_client, job_for
from tests.test_courses_api import auth
from tests.test_evidence_pipeline_db import (
    REPLY,
    STUDENT_TURN,
    counts,
    job_state,
    ledger,
    mixed_turn_provider,
)
from tests.test_pipeline_db import bootstrapped_course, fetch, gateway_for, ingest_turn

pytestmark = pytest.mark.db


class Crash(RuntimeError):
    """A simulated worker death at a stage boundary (the process would exit here)."""


def crash_once(monkeypatch, target, name):
    real = getattr(target, name)
    state = {"crashed": False}

    def wrapper(*args, **kwargs):
        if not state["crashed"]:
            state["crashed"] = True
            raise Crash(f"crash in {name}")
        return real(*args, **kwargs)

    monkeypatch.setattr(target, name, wrapper)
    return state


def run_again(pool, entity_id):
    """What the next worker does once the failed attempt's backoff is over."""
    with pool.connection() as conn:
        conn.execute(
            "update public.processing_jobs set available_at = now() where entity_id = %s",
            (entity_id,),
        )


def test_a_crash_before_p3a_commits_leaves_nothing_and_the_retry_runs_once(
    db_pool, registry, new_learner, monkeypatch
) -> None:
    from app.intelligence.processing import pipeline

    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    crashed = crash_once(monkeypatch, pipeline, "persist_analysis")
    worker = build_worker(db_pool, gateway_for(db_pool, mixed_turn_provider(db_pool, registry)))
    worker.drain()
    # One failed attempt, to be retried after its backoff (not FAILED, nothing written).
    assert crashed["crashed"] and job_state(db_pool, assistant_id)[:2] == ("RETRY_WAIT", 1)
    assert fetch(
        db_pool, "select count(*) from public.activity_segments where learner_id = %s", learner
    ) == [(0,)]
    run_again(db_pool, assistant_id)
    worker.drain()
    assert job_state(db_pool, assistant_id)[0] == "COMPLETED"
    assert fetch(
        db_pool, "select count(*) from public.activity_segments where learner_id = %s", learner
    ) == [(1,)]
    assert counts(db_pool, learner) == (2, 2, 2)


def test_a_crash_after_evidence_before_the_ledger_is_repaired_by_the_retry(
    db_pool, registry, new_learner, monkeypatch
) -> None:
    """The ledger is a rebuildable cache: evidence committed, the ledger write lost, the retry
    re-derives it without a model call and without a second attribution."""
    from app.intelligence.evidence import stage

    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    provider = mixed_turn_provider(db_pool, registry)
    crash_once(monkeypatch, stage, "recompute_ledger")
    worker = build_worker(db_pool, gateway_for(db_pool, provider))
    worker.drain()
    assert counts(db_pool, learner) == (2, 2, 0)  # evidence committed, no ledger yet
    calls = len(provider.calls)
    run_again(db_pool, assistant_id)
    worker.drain()
    assert job_state(db_pool, assistant_id)[0] == "COMPLETED"
    assert counts(db_pool, learner) == (2, 2, 2)
    assert len(provider.calls) == calls  # nothing was asked of the model again


def test_a_correction_interrupted_mid_recompute_is_all_or_nothing_and_retries_once(
    db_pool, registry, new_learner, monkeypatch, verifier, make_token
) -> None:
    from app.experience import feedback

    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    build_worker(db_pool, gateway_for(db_pool, mixed_turn_provider(db_pool, registry))).drain()
    target = fetch(
        db_pool,
        "select id from public.evidence_events where learner_id = %s and actor = 'STUDENT'",
        learner,
    )[0][0]
    before = ledger(db_pool, learner)
    client = api_client(verifier, db_pool)
    body = {"action": "DONT_COUNT", "target_type": "EVIDENCE_EVENT", "target_id": str(target)}
    headers = {**auth(make_token(learner)), "Idempotency-Key": f"crash-{uuid4().hex}"}

    crash_once(monkeypatch, feedback, "recompute_ledger")
    assert client.post("/v1/feedback", json=body, headers=headers).status_code == 500
    # Rolled back as one: no feedback row, the evidence still counts, the ledger untouched.
    assert fetch(db_pool, "select count(*) from public.feedback where user_id = %s", learner) == [
        (0,)
    ]
    assert fetch(db_pool, "select excluded from public.evidence_events where id = %s", target) == [
        (False,)
    ]
    assert ledger(db_pool, learner) == before

    retried = client.post("/v1/feedback", json=body, headers=headers)
    assert retried.status_code == 201 and retried.json()["created"] is True
    replayed = client.post("/v1/feedback", json=body, headers=headers)
    assert replayed.status_code == 200 and replayed.json()["created"] is False
    assert fetch(db_pool, "select count(*) from public.feedback where user_id = %s", learner) == [
        (1,)
    ]
    assert fetch(db_pool, "select excluded from public.evidence_events where id = %s", target) == [
        (True,)
    ]


def test_a_replay_after_a_correction_never_brings_the_evidence_back(
    db_pool, registry, new_learner, verifier, make_token
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    provider = mixed_turn_provider(db_pool, registry)
    gateway = gateway_for(db_pool, provider)
    job = job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE)
    process_raw_message_job(db_pool, gateway, job)
    target = fetch(
        db_pool,
        "select id from public.evidence_events where learner_id = %s and actor = 'STUDENT'",
        learner,
    )[0][0]
    client = api_client(verifier, db_pool)
    response = client.post(
        "/v1/feedback",
        json={"action": "DONT_COUNT", "target_type": "EVIDENCE_EVENT", "target_id": str(target)},
        headers={**auth(make_token(learner)), "Idempotency-Key": f"replay-{uuid4().hex}"},
    )
    assert response.status_code == 201
    after, calls = ledger(db_pool, learner), len(provider.calls)
    for _ in range(2):
        assert process_raw_message_job(db_pool, gateway, job).outcome == "ALREADY_ANALYZED"
    assert len(provider.calls) == calls
    assert counts(db_pool, learner) == (2, 2, 2)  # no evidence re-created
    assert fetch(db_pool, "select excluded from public.evidence_events where id = %s", target) == [
        (True,)
    ]
    assert ledger(db_pool, learner) == after
