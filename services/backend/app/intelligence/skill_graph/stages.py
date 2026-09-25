"""Course graph bootstrap stages (courses.graph_status) outside the worker job (ADR 0008).

Model-free, so the admin API restores a stage without loading the ModelGateway.

    PENDING -> GENERATING -> EMBEDDING (graph canonicalized, graph_version >= 1) -> READY
                         any stage, attempts exhausted -> FAILED

A FAILED course has lost its stage. Before a manual retry of its BOOTSTRAP_COURSE_GRAPH job the
stage is restored, so the job resumes where it stopped: a canonicalized graph only gets its
embeddings (no second generation request, no spurious graph v2).
"""

from uuid import UUID

from psycopg import Connection


def prepare_bootstrap_retry(conn: Connection, course_id: UUID) -> str | None:
    """In the caller's transaction: the stage the retried job resumes from.

    EMBEDDING when the graph was canonicalized, PENDING when it never was; READY / None leave the
    course untouched (the job then completes as ALREADY_READY / COURSE_NOT_FOUND)."""
    row = conn.execute(
        "select graph_status::text, graph_version from public.courses where id = %s for update",
        (course_id,),
    ).fetchone()
    if row is None:
        return None
    status, version = row
    if status == "READY":
        return "READY"
    stage = "EMBEDDING" if version >= 1 else "PENDING"
    conn.execute(
        """
        update public.courses set graph_status = %s::public.course_graph_status, graph_error = null
         where id = %s
        """,
        (stage, course_id),
    )
    return stage
