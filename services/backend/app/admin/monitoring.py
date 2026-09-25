"""Admin read models: overview, model runs + request budget, benchmark runs, audit (ADR 0008).

Read-only and model-free. The request budget is re-derived from `model_runs` (every row without
`cache_source_run_id` is one provider request, ADR 0004) against the configured daily limits:
the API process needs no ModelGateway for it.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg import Connection

from app.admin.benchmark_view import presentation
from app.admin.jobs import job_counts, retry_options
from app.admin.models import (
    ERROR_PREVIEW_CHARS,
    AdminBenchmarkResponse,
    AdminModelRun,
    AdminModelRunsResponse,
    AdminOverview,
    AdminTotals,
    AuditEventSummary,
    BenchmarkRunDetail,
    BenchmarkRunSummary,
    ModelBudgetUsage,
)
from app.core.config import Settings
from app.core.redaction import redact


def quota_day(now: datetime, timezone: str) -> tuple[datetime, datetime]:
    local = now.astimezone(ZoneInfo(timezone))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def budget_usage(
    conn: Connection, settings: Settings, now: datetime | None = None
) -> list[ModelBudgetUsage]:
    start, reset = quota_day(now or datetime.now(UTC), settings.model_quota_timezone)
    used = {
        (r[0], r[1]): r[2]
        for r in conn.execute(
            """
            select provider, model, count(*) from public.model_runs
             where cache_source_run_id is null and created_at >= %s
             group by provider, model
            """,
            (start,),
        ).fetchall()
    }
    limits = settings.daily_request_limits
    for model in limits:
        used.setdefault(("google", model), 0)
    usage = []
    for (provider, model), requests in sorted(used.items()):
        limit = limits.get(model)
        usage.append(
            ModelBudgetUsage(
                provider=provider,
                model=model,
                requests=requests,
                limit=limit,
                reserve=settings.model_quota_reserve,
                available=max(limit - settings.model_quota_reserve - requests, 0)
                if limit
                else None,
                resets_at=reset,
            )
        )
    return usage


_RUN_SELECT = """
select id, trace_id, task_type, provider, model, prompt_version, status::text, attempt,
       error_code, error_message, latency_ms, input_tokens, output_tokens, total_tokens,
       cache_source_run_id is not null, course_id, processing_job_id, created_at
  from public.model_runs
"""


def _run(row: tuple) -> AdminModelRun:
    return AdminModelRun(
        id=row[0],
        trace_id=row[1],
        task_type=row[2],
        provider=row[3],
        model=row[4],
        prompt_version=row[5],
        status=row[6],
        attempt=row[7],
        error_code=row[8],
        error_message=redact(row[9], ERROR_PREVIEW_CHARS),
        latency_ms=row[10],
        input_tokens=row[11],
        output_tokens=row[12],
        total_tokens=row[13],
        cache_hit=row[14],
        course_id=row[15],
        processing_job_id=row[16],
        created_at=row[17],
    )


def list_model_runs(
    conn: Connection,
    settings: Settings,
    *,
    failures_only: bool = False,
    status: str | None = None,
    task_type: str | None = None,
    before: datetime | None = None,
    limit: int = 50,
) -> AdminModelRunsResponse:
    rows = conn.execute(
        _RUN_SELECT
        + """
         where (not %(failures)s or status <> 'SUCCEEDED')
           and (%(status)s::text is null or status::text = %(status)s)
           and (%(task)s::text is null or task_type = %(task)s)
           and (%(before)s::timestamptz is null or created_at < %(before)s)
         order by created_at desc, id desc
         limit %(limit)s
        """,
        {
            "failures": failures_only,
            "status": status,
            "task": task_type,
            "before": before,
            "limit": limit,
        },
    ).fetchall()
    runs = [_run(r) for r in rows]
    counts = dict(
        conn.execute(
            """
            select status::text, count(*) from public.model_runs
             where created_at >= now() - interval '24 hours' group by status
            """
        ).fetchall()
    )
    return AdminModelRunsResponse(
        runs=runs,
        next_before=runs[-1].created_at if len(runs) == limit else None,
        status_counts_24h=counts,
        budget=budget_usage(conn, settings),
    )


_BENCHMARK_COLUMNS = """
id, set_name, set_version, mode::text, provider, model, routing, case_count, passed_count,
failed_count, blocked_count, verdict::text, hard_gates, metrics, provider_requests,
embedding_requests, code_sha, started_at, finished_at
"""


def _benchmark(row: tuple) -> BenchmarkRunSummary:
    """A summary from `_BENCHMARK_COLUMNS, report`: the stored fields plus their presentation."""
    keys = [k.strip() for k in _BENCHMARK_COLUMNS.replace("::text", "").split(",")]
    fields = dict(zip(keys, row[:-1], strict=True))
    return BenchmarkRunSummary(
        **fields,
        **presentation(fields["id"], fields["failed_count"], fields["hard_gates"], row[-1] or {}),
    )


def list_benchmark_runs(conn: Connection, limit: int = 20) -> AdminBenchmarkResponse:
    rows = conn.execute(
        f"select {_BENCHMARK_COLUMNS}, report from public.benchmark_runs "  # noqa: S608 - constant columns
        "order by created_at desc, id desc limit %s",
        (limit,),
    ).fetchall()
    latest_rows = conn.execute(
        f"select distinct on (mode) {_BENCHMARK_COLUMNS}, report from public.benchmark_runs "  # noqa: S608
        "order by mode, created_at desc, id desc"
    ).fetchall()
    latest = {r[3]: _benchmark(r) for r in latest_rows}
    return AdminBenchmarkResponse(latest=latest, runs=[_benchmark(r) for r in rows])


def get_benchmark_run(conn: Connection, run_id: UUID) -> BenchmarkRunDetail | None:
    row = conn.execute(
        f"select {_BENCHMARK_COLUMNS}, prompt_versions, policy_hash, report "  # noqa: S608
        "from public.benchmark_runs where id = %s",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    summary = _benchmark((*row[:-3], row[-1]))
    return BenchmarkRunDetail(
        **summary.model_dump(), prompt_versions=row[-3], policy_hash=row[-2], report=row[-1]
    )


def recent_audit(conn: Connection, limit: int = 10) -> list[AuditEventSummary]:
    rows = conn.execute(
        """
        select id, actor_type::text, actor_id, actor_role::text, action::text, entity_type,
               entity_id, metadata, created_at
          from public.audit_events order by created_at desc, id desc limit %s
        """,
        (limit,),
    ).fetchall()
    return [
        AuditEventSummary(
            id=r[0],
            actor_type=r[1],
            actor_id=r[2],
            actor_role=r[3],
            action=r[4],
            entity_type=r[5],
            entity_id=r[6],
            metadata=r[7],
            created_at=r[8],
        )
        for r in rows
    ]


def admin_overview(conn: Connection, settings: Settings) -> AdminOverview:
    failed = conn.execute(
        """
        select j.job_type, j.manual_retry_count, vs.state::text, vs.failure_code
          from public.processing_jobs j
          left join public.verification_sessions vs
            on j.entity_type = 'verification_session' and vs.id = j.entity_id
         where j.state = 'FAILED'
        """
    ).fetchall()
    failed_by_type: dict[str, int] = {}
    retryable = 0
    for job_type, retries, session_state, session_failure in failed:
        failed_by_type[job_type] = failed_by_type.get(job_type, 0) + 1
        mode, _ = retry_options(
            state="FAILED",
            job_type=job_type,
            manual_retry_count=retries,
            resumable=False,
            session_state=session_state,
            session_failure=session_failure,
        )
        retryable += mode is not None
    (pending_candidates,) = conn.execute(
        "select count(*) from public.skill_candidates where status = 'PENDING_REVIEW'"
    ).fetchone()
    (model_failures,) = conn.execute(
        """
        select count(*) from public.model_runs
         where status <> 'SUCCEEDED' and created_at >= now() - interval '24 hours'
        """
    ).fetchone()
    totals = conn.execute(
        """
        select (select count(*) from public.profiles),
               (select count(*) from public.courses),
               (select count(*) from public.skill_nodes
                 where status = 'ACTIVE' and node_kind in ('SKILL', 'SUBSKILL')),
               (select count(*) from public.evidence_events)
        """
    ).fetchone()
    latest = conn.execute(
        f"select {_BENCHMARK_COLUMNS}, report from public.benchmark_runs "  # noqa: S608 - constant columns
        "order by created_at desc, id desc limit 1"
    ).fetchone()
    return AdminOverview(
        jobs_by_state=job_counts(conn),
        failed_by_type=failed_by_type,
        retryable_failed=retryable,
        pending_candidates=pending_candidates,
        model_failures_24h=model_failures,
        budget=budget_usage(conn, settings),
        latest_benchmark=_benchmark(latest) if latest else None,
        totals=AdminTotals(
            learners=totals[0],
            courses=totals[1],
            active_skills=totals[2],
            evidence_events=totals[3],
        ),
        recent_audit=recent_audit(conn),
    )
