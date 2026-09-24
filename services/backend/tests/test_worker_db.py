"""Durable worker (architecture §7.3, §15.4): SKIP LOCKED claims, retry, defer, crash recovery."""

import threading
import time
from collections import Counter
from datetime import timedelta
from uuid import uuid4

import pytest

from app.jobs import queue
from app.jobs.queue import JobResult
from app.jobs.worker import Worker

pytestmark = pytest.mark.db

TEST_JOB = "P3_WORKER_TEST_JOB"


def enqueue(pool, learner, n: int = 1) -> list:
    with pool.connection() as conn:
        return [
            queue.enqueue_job(
                conn,
                job_type=TEST_JOB,
                entity_type="raw_message",
                entity_id=uuid4(),
                learner_id=learner,
            )
            for _ in range(n)
        ]


def job_state(pool, job_id) -> tuple:
    with pool.connection() as conn:
        return conn.execute(
            "select state::text, attempts, outcome, last_error, locked_by from public.processing_jobs where id = %s",
            (job_id,),
        ).fetchone()


def make_due(pool, job_id) -> None:
    with pool.connection() as conn:
        conn.execute(
            "update public.processing_jobs set available_at = now() - interval '1 second' where id = %s",
            (job_id,),
        )


def test_concurrent_workers_process_each_job_exactly_once(db_pool, new_learner) -> None:
    learner = new_learner()
    ids = enqueue(db_pool, learner, 12)
    handled: Counter = Counter()
    lock = threading.Lock()

    def handler(job):
        time.sleep(0.02)
        with lock:
            handled[job.id] += 1
        return JobResult("DONE")

    workers = [
        Worker(db_pool, {TEST_JOB: handler}, worker_id=f"w{i}", batch_size=2) for i in range(3)
    ]
    threads = [threading.Thread(target=w.drain) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert set(handled) == set(ids) and set(handled.values()) == {1}
    assert sum(w.stats.completed for w in workers) == 12
    assert {job_state(db_pool, i)[:3] for i in ids} == {("COMPLETED", 1, "DONE")}


def test_failed_job_is_retried_then_completes(db_pool, new_learner) -> None:
    (job_id,) = enqueue(db_pool, new_learner())
    outcomes = iter([RuntimeError("transient provider error"), JobResult("SUCCEEDED")])

    def handler(job):
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    worker = Worker(db_pool, {TEST_JOB: handler}, worker_id="retry-worker")
    worker.drain()
    state, attempts, _, error, locked_by = job_state(db_pool, job_id)
    assert (state, attempts, locked_by) == ("RETRY_WAIT", 1, None)
    assert error == "RuntimeError: transient provider error"
    assert worker.run_once() == 0  # backoff: not claimable yet
    make_due(db_pool, job_id)
    worker.drain()
    assert job_state(db_pool, job_id)[:3] == ("COMPLETED", 2, "SUCCEEDED")


def test_deferral_does_not_spend_attempts(db_pool, new_learner) -> None:
    (job_id,) = enqueue(db_pool, new_learner())
    results = iter([JobResult("AWAITING_ASSISTANT", defer_seconds=30), JobResult("ANALYSED")])
    worker = Worker(db_pool, {TEST_JOB: lambda job: next(results)})
    worker.drain()
    assert job_state(db_pool, job_id)[:3] == ("PENDING", 0, "AWAITING_ASSISTANT")
    make_due(db_pool, job_id)
    worker.drain()
    assert job_state(db_pool, job_id)[:3] == ("COMPLETED", 1, "ANALYSED")


def test_crashed_worker_jobs_are_recovered_and_rerun(db_pool, new_learner) -> None:
    (job_id,) = enqueue(db_pool, new_learner())
    with db_pool.connection() as conn:
        (claimed,) = queue.claim_jobs(conn, job_types=[TEST_JOB], worker_id="crashed-worker")
        # The worker dies mid-job: the row stays PROCESSING with an old lock.
        conn.execute(
            "update public.processing_jobs set locked_at = now() - interval '1 hour' where id = %s",
            (job_id,),
        )
    assert claimed.id == job_id and job_state(db_pool, job_id)[0] == "PROCESSING"

    ran: list = []
    worker = Worker(
        db_pool,
        {TEST_JOB: lambda job: ran.append(job.id) or JobResult("RECOVERED")},
        stale_after=timedelta(minutes=15),
    )
    worker.drain()
    assert ran == [job_id] and worker.stats.recovered == 1
    assert job_state(db_pool, job_id)[:3] == ("COMPLETED", 2, "RECOVERED")


def test_recovery_after_exhausted_attempts_fails_and_notifies(db_pool, new_learner) -> None:
    (job_id,) = enqueue(db_pool, new_learner())
    with db_pool.connection() as conn:
        conn.execute(
            "update public.processing_jobs set state = 'PROCESSING', attempts = 3, locked_by = 'dead', "
            "locked_at = now() - interval '2 hours' where id = %s",
            (job_id,),
        )
    failures: list = []
    worker = Worker(
        db_pool,
        {TEST_JOB: lambda job: JobResult("never")},
        failure_hooks={TEST_JOB: lambda job, error, final: failures.append((job.id, final))},
    )
    worker.run_once()
    assert job_state(db_pool, job_id)[0] == "FAILED"
    assert failures == [(job_id, True)]


def test_fresh_processing_jobs_are_not_stolen(db_pool, new_learner) -> None:
    (job_id,) = enqueue(db_pool, new_learner())
    with db_pool.connection() as conn:
        queue.claim_jobs(conn, job_types=[TEST_JOB], worker_id="busy-worker")
    worker = Worker(db_pool, {TEST_JOB: lambda job: JobResult("stolen")})
    assert worker.run_once() == 0 and worker.stats.recovered == 0
    assert job_state(db_pool, job_id)[0] == "PROCESSING"
