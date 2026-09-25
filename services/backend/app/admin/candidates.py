"""Skill-candidate review (architecture §8.2; ADR 0008). No model call.

A NEW_SKILL_CANDIDATE proposal never becomes a registry node by itself. An ADMIN decides:

    APPROVE  a new ACTIVE skill (source CANDIDATE_APPROVAL): the second controlled path to a
             node besides the course bootstrap. Its name must not already resolve to a skill
             (409: merge instead). Optional parent edge (the candidate's parent), optional
             course overlay (the candidate's first course unless another is named), and an
             EMBED_SKILL job so retrieval can find it. Earlier turns are not remapped.
    MERGE    the name becomes a VARIANT alias of an existing ACTIVE skill, which is re-embedded
             (its embedded text lists the aliases).
    REJECT   the name stays out of the registry; later proposals count on the same row
             (processing/persist.py).

Review, registry writes, job and audit event are one transaction, keyed by the Idempotency-Key.
The candidate guard of migration 0009 re-checks that an approved / merged name resolves to its
skill and that the reviewer is an ADMIN.
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from app.admin.audit import begin_request, record, request_hash
from app.admin.models import (
    AdminSkillCandidate,
    AdminSkillCandidatesResponse,
    CandidateReviewRequest,
    CourseRef,
    SimilarSkill,
    SkillRef,
)
from app.auth.roles import Actor
from app.intelligence.skill_graph.registry import add_edge, resolve_keys, unique_slug
from app.jobs.queue import JOB_EMBED_SKILL, enqueue_or_rearm_job

ASSESSABLE_KINDS = ("SKILL", "SUBSKILL")


class CandidateNotFoundError(LookupError):
    pass


class CandidateReviewError(ValueError):
    """The review cannot be applied as asked (HTTP 409 or 422 by `status`)."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


_CANDIDATE_SELECT = """
select c.id, c.canonical_name, c.normalized_name, c.description, c.status::text, c.occurrences,
       p.id, p.canonical_name, fc.id, fc.name, r.id, r.canonical_name, c.reviewed_at,
       c.review_note,
       (select count(*) from public.mapping_decisions d where d.new_skill_candidate_id = c.id),
       c.created_at, c.updated_at, c.first_model_run_id, c.parent_candidate_id, c.first_course_id
  from public.skill_candidates c
  left join public.skill_nodes p on p.id = c.parent_candidate_id
  left join public.courses fc on fc.id = c.first_course_id
  left join public.skill_nodes r on r.id = c.resolved_skill_id
"""


def similar_skills(conn: Connection, name: str, limit: int = 3) -> list[SimilarSkill]:
    """Existing ACTIVE skills sharing words with the name (lexical, the retrieval document)."""
    rows = conn.execute(
        """
        with q as (
            select nullif(replace(plainto_tsquery('english', %s)::text, '&', '|'), '')::tsquery as q
        )
        select n.id, n.canonical_name, ts_rank(n.search_document, q.q)
          from public.skill_nodes n, q
         where q.q is not null and n.status = 'ACTIVE' and n.node_kind in ('SKILL', 'SUBSKILL')
           and n.search_document @@ q.q
         order by 3 desc, n.canonical_name
         limit %s
        """,
        (name, limit),
    ).fetchall()
    return [SimilarSkill(id=r[0], name=r[1], score=round(float(r[2]), 4)) for r in rows]


def _candidate(conn: Connection, row: tuple) -> AdminSkillCandidate:
    return AdminSkillCandidate(
        id=row[0],
        canonical_name=row[1],
        normalized_name=row[2],
        description=row[3],
        status=row[4],
        occurrences=row[5],
        parent=SkillRef(id=row[6], name=row[7]) if row[6] else None,
        first_course=CourseRef(id=row[8], name=row[9]) if row[8] else None,
        resolved_skill=SkillRef(id=row[10], name=row[11]) if row[10] else None,
        reviewed_at=row[12],
        review_note=row[13],
        decision_count=row[14],
        similar_skills=similar_skills(conn, row[1]) if row[4] == "PENDING_REVIEW" else [],
        created_at=row[15],
        updated_at=row[16],
    )


def get_candidate(conn: Connection, candidate_id: UUID) -> AdminSkillCandidate | None:
    row = conn.execute(_CANDIDATE_SELECT + " where c.id = %s", (candidate_id,)).fetchone()
    return _candidate(conn, row) if row else None


def list_candidates(
    conn: Connection, *, status: str | None = "PENDING_REVIEW", limit: int = 100
) -> AdminSkillCandidatesResponse:
    rows = conn.execute(
        _CANDIDATE_SELECT
        + """
         where (%(status)s::text is null or c.status::text = %(status)s)
         order by c.occurrences desc, c.updated_at desc, c.id
         limit %(limit)s
        """,
        {"status": status, "limit": limit},
    ).fetchall()
    counts = dict(
        conn.execute(
            "select status::text, count(*) from public.skill_candidates group by status"
        ).fetchall()
    )
    return AdminSkillCandidatesResponse(
        counts=counts, candidates=[_candidate(conn, r) for r in rows]
    )


@dataclass(frozen=True)
class ReviewOutcome:
    candidate: AdminSkillCandidate
    skill: SkillRef | None
    embed_job_id: UUID | None
    replayed: bool
    audit_event_id: UUID


def _active_skill(conn: Connection, skill_id: UUID) -> tuple | None:
    return conn.execute(
        """
        select id, canonical_name, node_kind::text from public.skill_nodes
         where id = %s and status = 'ACTIVE' and node_kind in ('SKILL', 'SUBSKILL')
        """,
        (skill_id,),
    ).fetchone()


def _approve(
    conn: Connection, candidate: tuple, request: CandidateReviewRequest, default_importance: float
) -> tuple[UUID, str, dict]:
    name, key, description = candidate[1], candidate[2], request.description or candidate[3]
    if key in resolve_keys(conn, [key]):
        raise CandidateReviewError(
            "NAME_TAKEN", "This name already resolves to a registry skill: merge it instead."
        )
    if not description or len(description.strip()) < 8:
        raise CandidateReviewError(
            "DESCRIPTION_REQUIRED",
            "The candidate has no description: give one (8-600 characters) to approve it.",
            status=422,
        )
    parent = (
        conn.execute(
            """
        select id, node_kind::text from public.skill_nodes
         where id = %s and status = 'ACTIVE'
           and node_kind in ('DOMAIN', 'SUBJECT', 'TOPIC', 'SKILL', 'SUBSKILL')
        """,
            (candidate[18],),
        ).fetchone()
        if candidate[18]
        else None
    )
    kind = "SUBSKILL" if parent and parent[1] in ASSESSABLE_KINDS else "SKILL"
    course_id = request.course_id or candidate[19]
    course = None
    if course_id is not None:
        course = conn.execute(
            "select id, graph_version from public.courses where id = %s", (course_id,)
        ).fetchone()
        if course is None and request.course_id is not None:
            raise CandidateReviewError("COURSE_NOT_FOUND", "Course not found.", status=422)
    (skill_id,) = conn.execute(
        """
        insert into public.skill_nodes (
            slug, canonical_name, normalized_name, description, node_kind, status,
            difficulty_band, source, source_course_id, source_model_run_id
        ) values (%s, %s, %s, %s, %s::public.skill_node_kind, 'ACTIVE', %s, 'CANDIDATE_APPROVAL',
                  %s, %s)
        returning id
        """,
        (
            unique_slug(conn, name),
            name,
            key,
            description.strip(),
            kind,
            request.difficulty_band,
            course[0] if course else None,
            candidate[17],
        ),
    ).fetchone()
    edge = None
    if parent:
        edge = add_edge(
            conn,
            parent[0],
            skill_id,
            "PARENT",
            course_id=course[0] if course else None,
            source="CANDIDATE_APPROVAL",
        )
    overlay = False
    if course and course[1] >= 1:
        overlay = (
            conn.execute(
                """
                insert into public.course_skills
                    (course_id, skill_id, importance, source, graph_version)
                values (%s, %s, %s, 'CANDIDATE_APPROVAL', %s)
                on conflict (course_id, skill_id) do nothing
                returning skill_id
                """,
                (
                    course[0],
                    skill_id,
                    request.importance if request.importance is not None else default_importance,
                    course[1],
                ),
            ).fetchone()
            is not None
        )
    return (
        skill_id,
        name,
        {
            "skill_id": str(skill_id),
            "node_kind": kind,
            "parent_edge": edge,
            "course_id": str(course[0]) if course else None,
            "course_overlay": overlay,
        },
    )


def _merge(conn: Connection, candidate: tuple, target_id: UUID) -> tuple[UUID, str, dict]:
    target = _active_skill(conn, target_id)
    if target is None:
        raise CandidateReviewError(
            "TARGET_NOT_FOUND", "The target must be an ACTIVE assessable skill.", status=422
        )
    key = candidate[2]
    owner = resolve_keys(conn, [key]).get(key)
    if owner is not None and owner != target[0]:
        raise CandidateReviewError(
            "NAME_TAKEN", "This name already resolves to another skill.", status=409
        )
    added = False
    if owner is None:
        added = (
            conn.execute(
                """
                insert into public.skill_aliases
                    (skill_id, alias, normalized_alias, alias_kind, source)
                values (%s, %s, %s, 'VARIANT', 'CANDIDATE_APPROVAL')
                on conflict (normalized_alias) do nothing
                returning id
                """,
                (target[0], candidate[1], key),
            ).fetchone()
            is not None
        )
    return target[0], target[1], {"skill_id": str(target[0]), "alias_added": added}


def review_candidate(
    conn: Connection,
    actor: Actor,
    candidate_id: UUID,
    request: CandidateReviewRequest,
    key: str,
    *,
    default_importance: float,
) -> ReviewOutcome:
    digest = request_hash(
        f"POST /v1/admin/skill-candidates/{candidate_id}/review", request.model_dump(mode="json")
    )
    with conn.transaction():
        replay = begin_request(conn, actor, key, digest)
        if replay is not None:
            candidate = get_candidate(conn, candidate_id)
            if candidate is None:  # pragma: no cover - candidates are never deleted by the API
                raise CandidateNotFoundError(candidate_id)
            job = replay.metadata.get("embed_job_id")
            return ReviewOutcome(
                candidate, candidate.resolved_skill, UUID(job) if job else None, True, replay.id
            )

        row = conn.execute(
            _CANDIDATE_SELECT + " where c.id = %s for update of c", (candidate_id,)
        ).fetchone()
        if row is None:
            raise CandidateNotFoundError(candidate_id)
        if row[4] != "PENDING_REVIEW":
            raise CandidateReviewError("ALREADY_REVIEWED", f"This candidate is already {row[4]}.")

        skill: SkillRef | None = None
        job_id = None
        effect: dict = {}
        if request.action == "REJECT":
            status, resolved = "REJECTED", None
        else:
            if request.action == "APPROVE":
                skill_id, name, effect = _approve(conn, row, request, default_importance)
                status = "APPROVED"
            else:
                skill_id, name, effect = _merge(conn, row, request.target_skill_id)  # type: ignore[arg-type]
                status = "MERGED"
            resolved = skill_id
            skill = SkillRef(id=skill_id, name=name)
            job_id = enqueue_or_rearm_job(
                conn, job_type=JOB_EMBED_SKILL, entity_type="skill_node", entity_id=skill_id
            )
        conn.execute(
            """
            update public.skill_candidates
               set status = %s::public.skill_candidate_status, resolved_skill_id = %s,
                   reviewed_by = %s, reviewed_at = now(), review_note = %s
             where id = %s
            """,
            (status, resolved, actor.id, request.note, candidate_id),
        )
        audit = record(
            conn,
            actor=actor,
            action=f"CANDIDATE_{request.action}",
            entity_type="skill_candidate",
            entity_id=candidate_id,
            metadata={
                "candidate_name": row[1],
                "status": status,
                "embed_job_id": str(job_id) if job_id else None,
                **effect,
            },
            key=key,
            request_digest=digest,
        )
        candidate = get_candidate(conn, candidate_id)
    if candidate is None:  # pragma: no cover - read back in the same transaction
        raise CandidateNotFoundError(candidate_id)
    return ReviewOutcome(candidate, skill, job_id, False, audit.id)
