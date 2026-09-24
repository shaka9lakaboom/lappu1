"""processing_jobs queue primitives (architecture §7.3). No worker runs in P1."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.jobs import queue

pytestmark = pytest.mark.db

TEST_JOB = "P1_TEST_JOB"


def test_retry_delay_backs_off_and_caps() -> None:
    assert queue.retry_delay(1) == timedelta(seconds=30)
    assert queue.retry_delay(2) == timedelta(seconds=60)
    assert queue.retry_delay(50) == timedelta(minutes=30)


@pytest.fixture
def jobs(db_pool, new_learner):
    learner = new_learner()
    with db_pool.connection() as conn:
        ids = [
            queue.enqueue_job(
                conn,
                job_type=TEST_JOB,
                entity_type="raw_message",
                entity_id=uuid4(),
                learner_id=learner,
            )
            for _ in range(3)
        ]
    return learner, ids


def test_enqueue_is_idempotent(db_pool, new_learner) -> None:
    learner, entity = new_learner(), uuid4()
    with db_pool.connection() as conn:
        first = queue.enqueue_job(
            conn, job_type=TEST_JOB, entity_type="raw_message", entity_id=entity, learner_id=learner
        )
        second = queue.enqueue_job(
            conn, job_type=TEST_JOB, entity_type="raw_message", entity_id=entity, learner_id=learner
        )
    assert first is not None and second is None


def test_concurrent_claims_skip_locked_rows(db_pool, jobs) -> None:
    _, ids = jobs
    with db_pool.connection() as holder, db_pool.connection() as other:
        # Worker A claims inside an open transaction and keeps its row locks.
        holder_tx = holder.transaction()
        holder_tx.__enter__()
        try:
            claimed_a = holder.execute(
                """
                select id from public.processing_jobs
                 where job_type = %s and state = 'PENDING'
                 order by created_at limit 2 for update skip locked
                """,
                (TEST_JOB,),
            ).fetchall()
            claimed_b = queue.claim_jobs(
                other, job_types=[TEST_JOB], worker_id="worker-b", limit=10
            )
        finally:
            holder_tx.__exit__(None, None, None)
    locked = {row[0] for row in claimed_a}
    assert len(locked) == 2
    assert {job.id for job in claimed_b} == set(ids) - locked


def test_claim_complete_and_fail_lifecycle(db_pool, jobs) -> None:
    _, ids = jobs
    with db_pool.connection() as conn:
        claimed = queue.claim_jobs(conn, job_types=[TEST_JOB], worker_id="w1", limit=10)
        assert {j.id for j in claimed} == set(ids)
        assert all(j.attempts == 1 for j in claimed)
        # Nothing left to claim while they are PROCESSING.
        assert queue.claim_jobs(conn, job_types=[TEST_JOB], worker_id="w2") == []

        done, retried, _ = ids
        queue.complete_job(conn, done)
        assert queue.fail_job(conn, retried, "transient") == "RETRY_WAIT"

        states = dict(
            conn.execute(
                "select id, state::text from public.processing_jobs where id = any(%s)", (ids,)
            ).fetchall()
        )
        assert states[done] == "COMPLETED"
        assert states[retried] == "RETRY_WAIT"
        # Backoff: not claimable again yet.
        assert queue.claim_jobs(conn, job_types=[TEST_JOB], worker_id="w3") == []


def test_attempts_exhausted_marks_failed(db_pool, new_learner) -> None:
    learner = new_learner()
    with db_pool.connection() as conn:
        job_id = queue.enqueue_job(
            conn,
            job_type=TEST_JOB,
            entity_type="raw_message",
            entity_id=uuid4(),
            learner_id=learner,
        )
        for attempt in range(1, 4):
            conn.execute(
                "update public.processing_jobs set available_at = now() - interval '1 second'"
                " where id = %s",
                (job_id,),
            )
            (job,) = queue.claim_jobs(conn, job_types=[TEST_JOB], worker_id="w")
            assert job.attempts == attempt
            state = queue.fail_job(conn, job_id, f"boom {attempt}")
        assert state == "FAILED"
        (last_error,) = conn.execute(
            "select last_error from public.processing_jobs where id = %s", (job_id,)
        ).fetchone()
        assert last_error == "boom 3"
