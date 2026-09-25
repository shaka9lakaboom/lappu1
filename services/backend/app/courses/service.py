"""Course onboarding and course skill-graph reads (architecture §8.2, §13).

Creating a course writes the course, the creator's membership and one
BOOTSTRAP_COURSE_GRAPH job in a single transaction; the graph itself is
generated asynchronously by the worker, never inside the HTTP request.
Every read is scoped to the authenticated learner's memberships.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection

from app.courses.models import (
    Course,
    CourseCreateRequest,
    CourseSkill,
    CourseSkillsResponse,
    SkillEdge,
    SkillNode,
)
from app.jobs.queue import JOB_BOOTSTRAP_COURSE_GRAPH, enqueue_job


class UnknownLearnerError(Exception):
    pass


@dataclass(frozen=True)
class CreatedCourse:
    course: Course
    created: bool
    bootstrap_job_id: UUID | None


_COURSE_SELECT = """
select c.id, c.name, c.subject, c.level, c.description, c.status::text, c.graph_status::text,
       c.graph_version, c.graph_error, c.graph_generated_at, m.role::text,
       c.owner_id = %(learner)s as is_owner,
       (select count(*) from public.course_skills cs
          join public.skill_nodes n on n.id = cs.skill_id
         where cs.course_id = c.id and cs.active and n.node_kind in ('SKILL', 'SUBSKILL')),
       (select j.state::text from public.processing_jobs j
         where j.entity_id = c.id and j.job_type = 'BOOTSTRAP_COURSE_GRAPH'),
       (select j.outcome from public.processing_jobs j
         where j.entity_id = c.id and j.job_type = 'BOOTSTRAP_COURSE_GRAPH'),
       (select j.available_at from public.processing_jobs j
         where j.entity_id = c.id and j.job_type = 'BOOTSTRAP_COURSE_GRAPH'),
       c.created_at, c.updated_at
  from public.courses c
  join public.course_memberships m on m.course_id = c.id and m.user_id = %(learner)s
"""


# Outcomes of a deferral that did not spend an attempt (app/jobs/worker.py).
_WAIT_OUTCOMES = frozenset({"MODEL_BACKPRESSURE", "MODEL_BUDGET_RESERVE"})

# Stored bootstrap errors are "<ExceptionClass>: <message>" (app/jobs/worker.py) or the stale
# lock recovery note. Only these fixed summaries leave the API.
_PUBLIC_GRAPH_ERRORS = {
    "ModelOutputInvalidError": "The AI returned a skill graph that did not pass validation.",
    "ModelTimeoutError": "The AI provider did not answer in time.",
    "ModelUnavailableError": "The AI provider was unavailable.",
    "ModelRateLimitedError": "The AI provider refused the request.",
    "ModelCallError": "The AI provider request failed.",
    "worker lock expired": "The worker stopped while generating this skill graph.",
}
_PUBLIC_GRAPH_ERROR_DEFAULT = "An internal error interrupted skill graph generation."


def public_graph_error(error: str | None) -> str | None:
    if not error:
        return None
    return _PUBLIC_GRAPH_ERRORS.get(error.split(":", 1)[0].strip(), _PUBLIC_GRAPH_ERROR_DEFAULT)


def bootstrap_wait(
    job_state: str | None, outcome: str | None, available_at: datetime | None
) -> tuple[str | None, datetime | None]:
    """(wait reason, next attempt due) of a bootstrap job that is queued or waits for a retry.
    A queued job without a reason is simply due; a running or finished one has neither."""
    if job_state == "RETRY_WAIT":
        return "RETRY_AFTER_ERROR", available_at
    if job_state == "PENDING":
        return (outcome if outcome in _WAIT_OUTCOMES else None), available_at
    return None, None


def _course(row: tuple) -> Course:
    wait_reason, next_attempt_at = bootstrap_wait(row[13], row[14], row[15])
    return Course(
        id=row[0],
        name=row[1],
        subject=row[2],
        level=row[3],
        description=row[4],
        status=row[5],
        graph_status=row[6],
        graph_version=row[7],
        graph_error=public_graph_error(row[8]),
        graph_generated_at=row[9],
        role=row[10],
        is_owner=row[11],
        skill_count=row[12],
        bootstrap_job_state=row[13],
        bootstrap_wait_reason=wait_reason,
        bootstrap_next_attempt_at=next_attempt_at,
        created_at=row[16],
        updated_at=row[17],
    )


def create_course(
    conn: Connection, learner_id: UUID, request: CourseCreateRequest, idempotency_key: str | None
) -> CreatedCourse:
    with conn.transaction():
        if (
            conn.execute("select 1 from public.profiles where id = %s", (learner_id,)).fetchone()
            is None
        ):
            raise UnknownLearnerError(str(learner_id))
        if idempotency_key:
            existing = conn.execute(
                "select id from public.courses where owner_id = %s and client_request_id = %s",
                (learner_id, idempotency_key),
            ).fetchone()
            if existing:
                return CreatedCourse(get_course(conn, learner_id, existing[0]), False, None)
        (course_id,) = conn.execute(
            """
            insert into public.courses
                (owner_id, name, subject, level, description, client_request_id)
            values (%s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                learner_id,
                request.name,
                request.subject,
                request.level,
                request.description,
                idempotency_key,
            ),
        ).fetchone()
        conn.execute(
            "insert into public.course_memberships (course_id, user_id, role) "
            "values (%s, %s, 'STUDENT')",
            (course_id, learner_id),
        )
        job_id = enqueue_job(
            conn,
            job_type=JOB_BOOTSTRAP_COURSE_GRAPH,
            entity_type="course",
            entity_id=course_id,
            learner_id=learner_id,
        )
        course = get_course(conn, learner_id, course_id)
    if course is None:  # pragma: no cover - the row was inserted in this transaction
        raise RuntimeError(f"course {course_id} vanished after insert")
    return CreatedCourse(course, True, job_id)


def list_courses(conn: Connection, learner_id: UUID) -> list[Course]:
    rows = conn.execute(
        _COURSE_SELECT + " order by c.created_at desc", {"learner": learner_id}
    ).fetchall()
    return [_course(r) for r in rows]


def get_course(conn: Connection, learner_id: UUID, course_id: UUID) -> Course | None:
    row = conn.execute(
        _COURSE_SELECT + " where c.id = %(course)s", {"learner": learner_id, "course": course_id}
    ).fetchone()
    return _course(row) if row else None


def get_course_skills(
    conn: Connection, learner_id: UUID, course_id: UUID
) -> CourseSkillsResponse | None:
    course = get_course(conn, learner_id, course_id)
    if course is None:
        return None
    rows = conn.execute(
        """
        select n.id, n.slug, n.canonical_name, n.description, n.node_kind::text, n.status::text,
               n.version, n.difficulty_band, n.assessment_types,
               coalesce((select array_agg(a.alias order by a.alias) from public.skill_aliases a
                          where a.skill_id = n.id and a.alias_kind <> 'CANONICAL'), '{}'),
               cs.importance, cs.source::text, cs.active, cs.graph_version,
               exists (select 1 from public.skill_embeddings e where e.skill_id = n.id)
          from public.course_skills cs
          join public.skill_nodes n on n.id = cs.skill_id
         where cs.course_id = %s and cs.active
         order by n.node_kind, cs.importance desc, n.canonical_name
        """,
        (course_id,),
    ).fetchall()
    skills = [
        CourseSkill(
            skill=SkillNode(
                id=r[0],
                slug=r[1],
                canonical_name=r[2],
                description=r[3],
                node_kind=r[4],
                status=r[5],
                version=r[6],
                difficulty_band=r[7],
                assessment_types=list(r[8]),
                aliases=list(r[9]),
            ),
            importance=r[10],
            source=r[11],
            active=r[12],
            graph_version=r[13],
            embedded=r[14],
        )
        for r in rows
    ]
    ids = [s.skill.id for s in skills]
    edge_rows = conn.execute(
        """
        select from_skill_id, to_skill_id, edge_type::text, weight from public.skill_edges
         where from_skill_id = any(%s) and to_skill_id = any(%s)
         order by edge_type, from_skill_id, to_skill_id
        """,
        (ids, ids),
    ).fetchall()
    edges = [
        SkillEdge(from_skill_id=r[0], to_skill_id=r[1], edge_type=r[2], weight=r[3])
        for r in edge_rows
    ]
    return CourseSkillsResponse(
        course_id=course.id,
        graph_status=course.graph_status,
        graph_version=course.graph_version,
        skills=skills,
        edges=edges,
    )
