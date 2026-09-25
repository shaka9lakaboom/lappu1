"""Persistence of model_runs rows.

The database recorder writes each run on its own connection and commits
immediately, so the log survives even when the calling job's transaction
rolls back. If a run cannot be recorded the call is treated as failed: no
model output is used without its trace.

model_runs is also the request ledger of the daily budget (ADR 0004): every row
without `cache_source_run_id` is one provider request.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.model_gateway.types import ModelRun


class ModelRunRecorder(Protocol):
    def record(self, run: ModelRun) -> None: ...


class InMemoryRunRecorder:
    """For tests and offline tooling. Also a request ledger for the budget."""

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.runs: list[ModelRun] = []
        self._clock = clock
        self._recorded_at: list[datetime] = []

    def record(self, run: ModelRun) -> None:
        self.runs.append(run)
        self._recorded_at.append(self._clock())

    def provider_requests_since(self, provider: str, model: str, since: datetime) -> int:
        return sum(
            1
            for run, at in zip(self.runs, self._recorded_at, strict=True)
            if run.provider == provider
            and run.model == model
            and run.provider_request
            and at >= since
        )

    @property
    def provider_requests(self) -> list[ModelRun]:
        return [r for r in self.runs if r.provider_request]


class ModelRunsSchemaError(RuntimeError):
    """The database lacks a column the gateway writes (a migration is missing)."""


class DbModelRunRecorder:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def verify_schema(self) -> None:
        """Fail fast if migration 0004 (cache provenance) is not applied: every insert
        would fail, so a worker must not start and burn attempts on it."""
        with self._pool.connection() as conn:
            found = {
                r[0]
                for r in conn.execute(
                    """
                    select column_name from information_schema.columns
                     where table_schema = 'public' and table_name = 'model_runs'
                       and column_name in ('cache_key', 'cache_source_run_id')
                    """
                ).fetchall()
            }
        missing = {"cache_key", "cache_source_run_id"} - found
        if missing:
            raise ModelRunsSchemaError(
                "public.model_runs lacks "
                + ", ".join(sorted(missing))
                + ": apply supabase/migrations/0004_model_gateway_cache.sql"
            )

    def provider_requests_since(self, provider: str, model: str, since: datetime) -> int:
        with self._pool.connection() as conn:
            (count,) = conn.execute(
                """
                select count(*) from public.model_runs
                 where provider = %s and model = %s and created_at >= %s
                   and cache_source_run_id is null
                """,
                (provider, model, since),
            ).fetchone()
        return int(count)

    def record(self, run: ModelRun) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                insert into public.model_runs (
                    id, trace_id, task_type, provider, model, prompt_version, input_hash,
                    output_hash, output, input_tokens, output_tokens, total_tokens, latency_ms,
                    status, attempt, repair_of_id, error_code, error_message,
                    learner_id, course_id, processing_job_id, cache_key, cache_source_run_id
                ) values (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s::public.model_run_status, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
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
                    run.cache_key,
                    run.cache_source_run_id,
                ),
            )
