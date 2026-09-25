"""Admin course / skill lookup and course enrollment (ADR 0008). No model call.

Enrollment is how a cohort forms: an ADMIN adds existing accounts to a course as STUDENT or
TEACHER members. A TEACHER membership needs a TEACHER or ADMIN profile (the membership guard of
migration 0009); profile roles themselves change only through the operator script
`scripts/grant_role.py`.
"""

from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg import Connection

from app.admin.audit import begin_request, record, request_hash
from app.admin.jobs import list_jobs
from app.admin.models import (
    AdminCourse,
    AdminCourseDetail,
    AdminMember,
    AdminSkill,
    AdminSkillCourse,
    AdminSkillDetail,
    CourseMemberAddRequest,
    SkillRef,
)
from app.auth.roles import Actor
from app.jobs.queue import JOB_BOOTSTRAP_COURSE_GRAPH


class LookupNotFoundError(LookupError):
    pass


class MembershipError(ValueError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _like(q: str | None) -> str | None:
    if not q or not q.strip():
        return None
    escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_COURSE_SELECT = """
select c.id, c.name, c.subject, c.level, c.status::text, c.graph_status::text, c.graph_version,
       (select count(*) from public.course_skills cs join public.skill_nodes n on n.id = cs.skill_id
         where cs.course_id = c.id and cs.active and n.node_kind in ('SKILL', 'SUBSKILL')),
       (select count(*) from public.course_memberships m
         where m.course_id = c.id and m.role = 'STUDENT'),
       (select count(*) from public.course_memberships m
         where m.course_id = c.id and m.role = 'TEACHER'),
       c.created_at
  from public.courses c
"""


def _course(row: tuple) -> AdminCourse:
    return AdminCourse(
        id=row[0],
        name=row[1],
        subject=row[2],
        level=row[3],
        status=row[4],
        graph_status=row[5],
        graph_version=row[6],
        skill_count=row[7],
        student_count=row[8],
        teacher_count=row[9],
        created_at=row[10],
    )


def search_courses(conn: Connection, q: str | None, limit: int = 25) -> list[AdminCourse]:
    rows = conn.execute(
        _COURSE_SELECT
        + """
         where %(q)s::text is null or c.name ilike %(q)s or c.subject ilike %(q)s
               or c.id::text = %(raw)s
         order by c.created_at desc limit %(limit)s
        """,
        {"q": _like(q), "raw": (q or "").strip().lower(), "limit": limit},
    ).fetchall()
    return [_course(r) for r in rows]


def _members(conn: Connection, course_id: UUID) -> list[AdminMember]:
    rows = conn.execute(
        """
        select m.user_id, m.role::text, p.role::text, p.display_name, u.email, m.created_at
          from public.course_memberships m
          join public.profiles p on p.id = m.user_id
          left join auth.users u on u.id = m.user_id
         where m.course_id = %s
         order by m.role desc, m.created_at, m.user_id
        """,
        (course_id,),
    ).fetchall()
    return [
        AdminMember(
            user_id=r[0],
            role=r[1],
            profile_role=r[2],
            display_name=r[3],
            email=r[4],
            joined_at=r[5],
        )
        for r in rows
    ]


def course_detail(conn: Connection, course_id: UUID) -> AdminCourseDetail:
    row = conn.execute(_COURSE_SELECT + " where c.id = %s", (course_id,)).fetchone()
    if row is None:
        raise LookupNotFoundError(course_id)
    jobs = list_jobs(conn, job_type=JOB_BOOTSTRAP_COURSE_GRAPH, entity_id=course_id, limit=1).jobs
    return AdminCourseDetail(
        course=_course(row),
        members=_members(conn, course_id),
        bootstrap_job=jobs[0] if jobs else None,
    )


@dataclass(frozen=True)
class MemberOutcome:
    member: AdminMember
    created: bool
    replayed: bool
    audit_event_id: UUID


def add_member(
    conn: Connection, actor: Actor, course_id: UUID, request: CourseMemberAddRequest, key: str
) -> MemberOutcome:
    digest = request_hash(
        f"POST /v1/admin/courses/{course_id}/members", request.model_dump(mode="json")
    )
    with conn.transaction():
        replay = begin_request(conn, actor, key, digest)
        if replay is not None:
            user_id = UUID(replay.metadata["user_id"])
            member = next(m for m in _members(conn, course_id) if m.user_id == user_id)
            return MemberOutcome(member, bool(replay.metadata.get("created")), True, replay.id)

        if (
            conn.execute("select 1 from public.courses where id = %s", (course_id,)).fetchone()
            is None
        ):
            raise LookupNotFoundError(course_id)
        if request.user_id is not None:
            found = conn.execute(
                "select id from public.profiles where id = %s", (request.user_id,)
            ).fetchone()
        else:
            found = conn.execute(
                """
                select p.id from auth.users u join public.profiles p on p.id = u.id
                 where lower(u.email) = lower(%s)
                """,
                (request.email,),
            ).fetchone()
        if found is None:
            raise MembershipError("USER_NOT_FOUND", "No SkillMirror account matches.", 404)
        user_id = found[0]
        existing = conn.execute(
            "select role::text from public.course_memberships "
            "where course_id = %s and user_id = %s",
            (course_id, user_id),
        ).fetchone()
        if existing is not None and existing[0] != request.role:
            raise MembershipError(
                "MEMBER_EXISTS",
                f"This account is already a {existing[0]} member of the course.",
                409,
            )
        created = False
        if existing is None:
            try:
                with conn.transaction():
                    conn.execute(
                        """
                        insert into public.course_memberships (course_id, user_id, role)
                        values (%s, %s, %s::public.course_member_role)
                        """,
                        (course_id, user_id, request.role),
                    )
            except psycopg.errors.CheckViolation as exc:
                raise MembershipError(
                    "TEACHER_ROLE_REQUIRED",
                    "A TEACHER membership needs a TEACHER or ADMIN profile "
                    "(scripts/grant_role.py).",
                    422,
                ) from exc
            created = True
        audit = record(
            conn,
            actor=actor,
            action="COURSE_MEMBER_ADD",
            entity_type="course",
            entity_id=course_id,
            metadata={"user_id": str(user_id), "role": request.role, "created": created},
            key=key,
            request_digest=digest,
        )
        member = next(m for m in _members(conn, course_id) if m.user_id == user_id)
    return MemberOutcome(member, created, False, audit.id)


_SKILL_SELECT = """
select n.id, n.slug, n.canonical_name, n.node_kind::text, n.status::text, n.difficulty_band,
       n.source::text,
       (select count(*) from public.course_skills cs where cs.skill_id = n.id and cs.active),
       (select count(*) from public.skill_aliases a
         where a.skill_id = n.id and a.alias_kind <> 'CANONICAL'),
       exists (select 1 from public.skill_embeddings e where e.skill_id = n.id),
       n.description
  from public.skill_nodes n
"""


def _skill(row: tuple) -> AdminSkill:
    return AdminSkill(
        id=row[0],
        slug=row[1],
        canonical_name=row[2],
        node_kind=row[3],
        status=row[4],
        difficulty_band=row[5],
        source=row[6],
        course_count=row[7],
        alias_count=row[8],
        embedded=row[9],
    )


def search_skills(conn: Connection, q: str | None, limit: int = 25) -> list[AdminSkill]:
    rows = conn.execute(
        _SKILL_SELECT  # noqa: S608 - constant SQL fragments; values are bound
        + """
         where %(q)s::text is null or n.canonical_name ilike %(q)s or n.slug ilike %(q)s
               or n.id::text = %(raw)s
               or exists (select 1 from public.skill_aliases a
                           where a.skill_id = n.id and a.alias ilike %(q)s)
         order by n.status = 'ACTIVE' desc, n.canonical_name limit %(limit)s
        """,
        {"q": _like(q), "raw": (q or "").strip().lower(), "limit": limit},
    ).fetchall()
    return [_skill(r) for r in rows]


def skill_detail(conn: Connection, skill_id: UUID) -> AdminSkillDetail:
    row = conn.execute(_SKILL_SELECT + " where n.id = %s", (skill_id,)).fetchone()
    if row is None:
        raise LookupNotFoundError(skill_id)
    aliases = [
        r[0]
        for r in conn.execute(
            "select alias from public.skill_aliases "
            "where skill_id = %s and alias_kind <> 'CANONICAL' order by alias",
            (skill_id,),
        ).fetchall()
    ]
    courses = conn.execute(
        """
        select c.id, c.name, cs.importance from public.course_skills cs
          join public.courses c on c.id = cs.course_id
         where cs.skill_id = %s and cs.active order by c.name
        """,
        (skill_id,),
    ).fetchall()

    def related(edge_type: str) -> list[SkillRef]:
        rows = conn.execute(
            """
            select n.id, n.canonical_name from public.skill_edges e
              join public.skill_nodes n on n.id = e.from_skill_id
             where e.to_skill_id = %s and e.edge_type = %s::public.skill_edge_type
             order by n.canonical_name
            """,
            (skill_id, edge_type),
        ).fetchall()
        return [SkillRef(id=r[0], name=r[1]) for r in rows]

    candidates = conn.execute(
        "select id, canonical_name from public.skill_candidates where resolved_skill_id = %s "
        "order by reviewed_at",
        (skill_id,),
    ).fetchall()
    return AdminSkillDetail(
        skill=_skill(row),
        description=row[10],
        aliases=aliases,
        courses=[AdminSkillCourse(id=c[0], name=c[1], importance=float(c[2])) for c in courses],
        parents=related("PARENT"),
        prerequisites=related("PREREQUISITE"),
        candidates=[SkillRef(id=c[0], name=c[1]) for c in candidates],
    )
