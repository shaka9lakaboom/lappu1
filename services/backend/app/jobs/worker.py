"""Durable worker loop over processing_jobs (architecture §7.3).

Claims jobs with FOR UPDATE SKIP LOCKED (so several workers can run safely),
dispatches them by job type, completes, defers or fails them with backoff, and
recovers jobs left PROCESSING by a crashed worker. The same code runs inside
the API process (started by app.main when configured) or standalone:

    python -m app.jobs.worker            # loop until Ctrl+C
    python -m app.jobs.worker --once     # drain what is runnable now, then exit
"""

import argparse
import logging
import os
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from uuid import uuid4

from psycopg_pool import ConnectionPool

from app.jobs.queue import (
    JOB_BOOTSTRAP_COURSE_GRAPH,
    JOB_PROCESS_RAW_MESSAGE,
    ClaimedJob,
    JobResult,
    claim_jobs,
    complete_job,
    defer_job,
    fail_job,
    recover_stale_jobs,
)
from app.model_gateway import ModelGateway, ModelGatewayError

logger = logging.getLogger("skillmirror.worker")

# Provider backpressure (429 / 503): re-queue after the server's retry hint (clamped)
# or a default delay, without spending an attempt. The job stays pending, never FAILED.
BACKPRESSURE_MIN_DELAY = 15
BACKPRESSURE_DEFAULT_DELAY = 60
BACKPRESSURE_MAX_DELAY = 600

Handler = Callable[[ClaimedJob], JobResult]
FailureHook = Callable[[ClaimedJob, str, bool], None]


@dataclass
class WorkerStats:
    completed: int = 0
    deferred: int = 0
    backpressure: int = 0
    failed: int = 0
    recovered: int = 0


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:6]}"


class Worker:
    def __init__(
        self,
        pool: ConnectionPool,
        handlers: dict[str, Handler],
        *,
        worker_id: str | None = None,
        batch_size: int = 4,
        stale_after: timedelta = timedelta(minutes=15),
        failure_hooks: dict[str, FailureHook] | None = None,
    ) -> None:
        self._pool = pool
        self._handlers = handlers
        self.worker_id = worker_id or default_worker_id()
        self._batch_size = batch_size
        self._stale_after = stale_after
        self._failure_hooks = failure_hooks or {}
        self.stats = WorkerStats()

    def run_once(self) -> int:
        """Recover stale jobs, claim one batch and run it. Returns jobs claimed."""
        with self._pool.connection() as conn:
            recovered = recover_stale_jobs(conn, self._stale_after)
            if recovered:
                self.stats.recovered += len(recovered)
                logger.warning("recovered %d stale job(s): %s", len(recovered), recovered)
                for job_id, state in recovered:
                    if state == "FAILED":
                        self._notify_failed(job_id, "worker lock expired")
            jobs = claim_jobs(
                conn,
                job_types=list(self._handlers),
                worker_id=self.worker_id,
                limit=self._batch_size,
            )
        for job in jobs:
            self._execute(job)
        return len(jobs)

    def drain(self, max_rounds: int = 1000) -> WorkerStats:
        """Run until nothing is claimable (deferred jobs are not waited for)."""
        for _ in range(max_rounds):
            if self.run_once() == 0:
                break
        return self.stats

    def run_forever(self, stop: threading.Event, poll_seconds: float = 2.0) -> None:
        logger.info("worker %s started (job types: %s)", self.worker_id, ", ".join(self._handlers))
        while not stop.is_set():
            try:
                claimed = self.run_once()
            except Exception:
                logger.exception("worker loop error; retrying after %.1fs", poll_seconds)
                claimed = 0
            if claimed == 0:
                stop.wait(poll_seconds)
        logger.info("worker %s stopped", self.worker_id)

    def _execute(self, job: ClaimedJob) -> None:
        try:
            result = self._handlers[job.job_type](job)
        except ModelGatewayError as exc:
            if exc.transient:
                self._backpressure(job, exc)
            else:
                self._fail(job, exc)
            return
        except Exception as exc:
            self._fail(job, exc)
            return

        with self._pool.connection() as conn:
            if result.defer_seconds:
                defer_job(conn, job.id, timedelta(seconds=result.defer_seconds), result.outcome)
                self.stats.deferred += 1
            else:
                complete_job(conn, job.id, result.outcome)
                self.stats.completed += 1
        logger.info("job %s %s -> %s", job.id, job.job_type, result.outcome)

    def _backpressure(self, job: ClaimedJob, exc: ModelGatewayError) -> None:
        hint = exc.retry_after or BACKPRESSURE_DEFAULT_DELAY
        delay = int(min(max(hint, BACKPRESSURE_MIN_DELAY), BACKPRESSURE_MAX_DELAY))
        with self._pool.connection() as conn:
            defer_job(conn, job.id, timedelta(seconds=delay), "MODEL_BACKPRESSURE")
        self.stats.backpressure += 1
        logger.warning(
            "job %s %s: model backpressure (%s); retrying in %ds without spending an attempt",
            job.id,
            job.job_type,
            exc,
            delay,
        )

    def _fail(self, job: ClaimedJob, exc: Exception) -> None:
        error = f"{type(exc).__name__}: {exc}"
        with self._pool.connection() as conn:
            state = fail_job(conn, job.id, error)
        self.stats.failed += 1
        logger.warning(
            "job %s %s attempt %d/%d failed -> %s: %s",
            job.id,
            job.job_type,
            job.attempts,
            job.max_attempts,
            state,
            error,
        )
        hook = self._failure_hooks.get(job.job_type)
        if hook:
            try:
                hook(job, error, state == "FAILED")
            except Exception:
                logger.exception("failure hook for job %s raised", job.id)

    def _notify_failed(self, job_id, error: str) -> None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select id, job_type, entity_type, entity_id, learner_id, attempts, max_attempts "
                "from public.processing_jobs where id = %s",
                (job_id,),
            ).fetchone()
        if row is None:
            return
        job = ClaimedJob(*row)
        hook = self._failure_hooks.get(job.job_type)
        if hook:
            hook(job, error, True)


def build_worker(pool: ConnectionPool, gateway: ModelGateway, **kwargs: object) -> Worker:
    """The production worker: P2 course bootstrap + P3A raw-message processing."""
    from app.intelligence.processing.pipeline import process_raw_message_job
    from app.intelligence.skill_graph.jobs import mark_bootstrap_failed, run_bootstrap_job

    return Worker(
        pool,
        {
            JOB_BOOTSTRAP_COURSE_GRAPH: partial(run_bootstrap_job, pool, gateway),
            JOB_PROCESS_RAW_MESSAGE: partial(process_raw_message_job, pool, gateway),
        },
        failure_hooks={
            JOB_BOOTSTRAP_COURSE_GRAPH: lambda job, error, final: mark_bootstrap_failed(
                pool, job, error, final
            ),
        },
        **kwargs,  # type: ignore[arg-type]
    )


def main() -> None:  # pragma: no cover - thin CLI
    from app.core.config import get_settings
    from app.db.pool import create_pool
    from app.model_gateway import build_gateway
    from app.observability.logging import configure_logging

    parser = argparse.ArgumentParser(description="SkillMirror processing worker")
    parser.add_argument("--once", action="store_true", help="drain runnable jobs, then exit")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set")
    pool = create_pool(settings.database_url.get_secret_value())
    gateway = build_gateway(settings, pool)
    if gateway is None:
        raise SystemExit("GEMINI_API_KEY is not set: the worker needs the model gateway")
    worker = build_worker(
        pool,
        gateway,
        batch_size=settings.worker_batch_size,
        stale_after=timedelta(seconds=settings.worker_stale_after_seconds),
    )
    try:
        if args.once:
            stats = worker.drain()
            print(f"worker drained: {stats}")
        else:
            stop = threading.Event()
            try:
                worker.run_forever(stop, settings.worker_poll_seconds)
            except KeyboardInterrupt:
                stop.set()
    finally:
        pool.close()


if __name__ == "__main__":  # pragma: no cover
    main()
