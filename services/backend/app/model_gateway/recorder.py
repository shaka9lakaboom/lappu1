"""Persistence of model_runs rows.

The database recorder writes each run on its own connection and commits
immediately, so the log survives even when the calling job's transaction
rolls back. If a run cannot be recorded the call is treated as failed: no
model output is used without its trace.
"""

from typing import Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.model_gateway.types import ModelRun


class ModelRunRecorder(Protocol):
    def record(self, run: ModelRun) -> None: ...


class InMemoryRunRecorder:
    """For tests and offline tooling."""

    def __init__(self) -> None:
        self.runs: list[ModelRun] = []

    def record(self, run: ModelRun) -> None:
        self.runs.append(run)


class DbModelRunRecorder:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def record(self, run: ModelRun) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                insert into public.model_runs (
                    id, trace_id, task_type, provider, model, prompt_version, input_hash,
                    output_hash, output, input_tokens, output_tokens, total_tokens, latency_ms,
                    status, attempt, repair_of_id, error_code, error_message,
                    learner_id, course_id, processing_job_id
                ) values (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s::public.model_run_status, %s, %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (
                    run.id,
                    run.trace_id,
                    run.task_type,
                    run.provider,
                    run.model,
                    run.prompt_version,
                    run.input_hash,
                    run.output_hash,
                    Jsonb(run.output) if run.output is not None else None,
                    run.input_tokens,
                    run.output_tokens,
                    run.total_tokens,
                    run.latency_ms,
                    run.status.value,
                    run.attempt,
                    run.repair_of_id,
                    run.error_code,
                    run.error_message[:2000] if run.error_message else None,
                    run.learner_id,
                    run.course_id,
                    run.processing_job_id,
                ),
            )
