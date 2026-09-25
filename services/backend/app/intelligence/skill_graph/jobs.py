"""BOOTSTRAP_COURSE_GRAPH job: generate -> canonicalize -> embed (architecture §8.2).

Resumable: the course's graph_status records the last completed stage, so a
retry after an embedding failure does not regenerate the graph, and a replay
after completion is a no-op.
"""

import logging
from uuid import UUID

from psycopg_pool import ConnectionPool

from app.intelligence.policy import load_policy
from app.intelligence.skill_graph.bootstrap import CourseBrief, generate_course_graph
from app.intelligence.skill_graph.embedding import embed_skills
from app.intelligence.skill_graph.registry import canonicalize_and_persist
from app.jobs.queue import JOB_BOOTSTRAP_COURSE_GRAPH, ClaimedJob, JobResult
from app.model_gateway import ModelGateway, RunContext

__all__ = ["JOB_BOOTSTRAP_COURSE_GRAPH", "mark_bootstrap_failed", "run_bootstrap_job"]

logger = logging.getLogger("skillmirror.skill_graph")


def run_bootstrap_job(pool: ConnectionPool, gateway: ModelGateway, job: ClaimedJob) -> JobResult:
    with pool.connection() as conn:
        row = conn.execute(
            """
            select owner_id, name, subject, level, description, graph_status::text
              from public.courses where id = %s
            """,
            (job.entity_id,),
        ).fetchone()
        if row is None:
            return JobResult("COURSE_NOT_FOUND")
        owner_id, name, subject, level, description, graph_status = row
        if graph_status == "READY":
            return JobResult("ALREADY_READY")
        policy = load_policy(conn)
        generate = graph_status != "EMBEDDING"
        if generate:
            conn.execute(
                "update public.courses set graph_status = 'GENERATING' where id = %s",
                (job.entity_id,),
            )

    context = RunContext(
        trace_id=f"job:{job.id}",
        learner_id=owner_id,
        course_id=job.entity_id,
        processing_job_id=job.id,
    )
    if generate:
        brief = CourseBrief(name=name, subject=subject, level=level, description=description)
        result = generate_course_graph(gateway, brief, policy.skill_graph, context)
        with pool.connection() as conn, conn.transaction():
            report = canonicalize_and_persist(
                conn,
                course_id=job.entity_id,
                proposal=result.parsed,
                policy=policy.skill_graph,
                model_run_id=result.run.id,
            )
        logger.info(
            "course graph v%d course=%s skills=%d (new %d, reused %d) topics=%d aliases=%d "
            "alias_conflicts=%d edges=%d skipped=%d",
            report.graph_version,
            job.entity_id,
            report.assessable_count,
            len(report.created_skills),
            len(report.reused_skills),
            len(report.created_topics) + len(report.reused_topics),
            report.aliases_added,
            len(report.alias_conflicts),
            report.edges_added,
            len(report.edges_skipped),
        )

    with pool.connection() as conn:
        skill_ids = course_skill_ids(conn, job.entity_id)
        embed_report = embed_skills(conn, gateway, skill_ids, context)
        conn.execute(
            """
            update public.courses
               set graph_status = 'READY', graph_generated_at = now(), graph_error = null
             where id = %s
            """,
            (job.entity_id,),
        )
    logger.info(
        "course graph ready course=%s embedded=%d cached=%d",
        job.entity_id,
        embed_report.embedded,
        embed_report.cached,
    )
    return JobResult("GRAPH_READY")


def course_skill_ids(conn, course_id: UUID) -> list[UUID]:
    return [
        r[0]
        for r in conn.execute(
            "select skill_id from public.course_skills where course_id = %s and active",
            (course_id,),
        ).fetchall()
    ]


def mark_bootstrap_failed(pool: ConnectionPool, job: ClaimedJob, error: str, final: bool) -> None:
    """Record the error on the course; FAILED only once the job has no attempts left."""
    with pool.connection() as conn:
        conn.execute(
            """
            update public.courses
               set graph_error = %s,
                   graph_status = case when %s then 'FAILED'::public.course_graph_status
                                       else graph_status end
             where id = %s and graph_status <> 'READY'
            """,
            (error[:2000], final, job.entity_id),
        )
