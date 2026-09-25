"""Teacher course overview (architecture §12.3; ADR 0008). Read-only, no model call.

Aggregates are computed here, over the backend's service connection, from STUDENT members only:
teachers get no RLS access to learner rows. Everything is scoped to (the course's STUDENT members)
x (the course's ACTIVE assessable skills), like the ledger, which is per learner and skill (not
per course): the same skill in two courses has one state. Counting rules:

* mastery states come from the (rebuildable) ledger; a student with no ledger row for a skill is
  UNKNOWN, a neutral "not enough evidence yet" count;
* evidence counts include only non-excluded evidence of the students' own work and of
  verifications (models.IndependentEvidenceType + VERIFICATION);
* common mapped skills count distinct students with ACCEPTED mappings in the window, minus the
  mappings the learner corrected (WRONG_SKILL / DONT_COUNT, P5);
* verification needs count distinct students with an ACTIVE VERIFY / REVERIFY recommendation.

Below the minimum cohort the whole course is suppressed (cohort size only).
"""

from typing import get_args
from uuid import UUID

from psycopg import Connection

from app.auth.roles import Actor
from app.intelligence.contracts import MasteryState
from app.intelligence.policy import PolicyConfigError
from app.teacher.models import (
    IndependentEvidenceType,
    TeacherCohort,
    TeacherCourse,
    TeacherCourseOverview,
    TeacherEvidenceCounts,
    TeacherMappedSkill,
    TeacherSkillRow,
    TeacherVerificationNeed,
    TeacherViewPolicy,
)

MASTERY_STATES: tuple[str, ...] = get_args(MasteryState)
INDEPENDENT_TYPES: list[str] = list(get_args(IndependentEvidenceType))
COUNTED_TYPES: list[str] = [*INDEPENDENT_TYPES, "VERIFICATION"]


class TeacherCourseNotFoundError(LookupError):
    """Not a course this account teaches (the course's existence is not revealed)."""


def load_teacher_view_policy(conn: Connection) -> TeacherViewPolicy:
    row = conn.execute(
        "select value from public.policy_config "
        "where scope_type = 'global' and key = 'teacher_view'"
    ).fetchone()
    if row is None:
        raise PolicyConfigError("policy_config is missing the global key teacher_view (0009)")
    try:
        return TeacherViewPolicy.model_validate(row[0])
    except ValueError as exc:
        raise PolicyConfigError(f"policy_config teacher_view is invalid: {exc}") from exc


_COURSE_SELECT = """
select c.id, c.name, c.subject, c.level, c.graph_status::text, c.graph_version,
       (select count(*) from public.course_skills cs
          join public.skill_nodes n on n.id = cs.skill_id
         where cs.course_id = c.id and cs.active and n.status = 'ACTIVE'
           and n.node_kind in ('SKILL', 'SUBSKILL')),
       (select count(*) from public.course_memberships s
         where s.course_id = c.id and s.role = 'STUDENT')
  from public.courses c
"""


def _course(row: tuple, min_cohort: int) -> TeacherCourse:
    return TeacherCourse(
        id=row[0],
        name=row[1],
        subject=row[2],
        level=row[3],
        graph_status=row[4],
        graph_version=row[5],
        skill_count=row[6],
        student_count=row[7],
        suppressed=row[7] < min_cohort,
    )


def list_taught_courses(
    conn: Connection, actor: Actor, policy: TeacherViewPolicy
) -> list[TeacherCourse]:
    """The courses this account holds a TEACHER membership of."""
    rows = conn.execute(
        _COURSE_SELECT
        + """
          join public.course_memberships t
            on t.course_id = c.id and t.user_id = %s and t.role = 'TEACHER'
         where c.status = 'ACTIVE'
         order by c.name, c.id
        """,
        (actor.id,),
    ).fetchall()
    return [_course(r, policy.min_cohort) for r in rows]


def _authorized_course(conn: Connection, actor: Actor, course_id: UUID) -> tuple | None:
    """A course the actor holds a TEACHER membership of, whatever the profile role: an ADMIN
    without that membership gets nothing here (admins use the admin course lookup)."""
    return conn.execute(
        _COURSE_SELECT  # noqa: S608 - constant SQL fragments; values are bound
        + """
         where c.id = %(course)s
           and exists (select 1 from public.course_memberships t
                        where t.course_id = c.id and t.user_id = %(actor)s
                          and t.role = 'TEACHER')
        """,
        {"course": course_id, "actor": actor.id},
    ).fetchone()


_SCOPE = """
with students as (
    select m.user_id from public.course_memberships m
     where m.course_id = %(course)s and m.role = 'STUDENT'
), skills as (
    select cs.skill_id from public.course_skills cs
      join public.skill_nodes n on n.id = cs.skill_id
     where cs.course_id = %(course)s and cs.active and n.status = 'ACTIVE'
       and n.node_kind in ('SKILL', 'SUBSKILL')
), counted as (
    select e.learner_id, e.skill_id, e.evidence_type::text as evidence_type, e.occurred_at
      from public.evidence_events e
     where e.learner_id in (select user_id from students)
       and e.skill_id in (select skill_id from skills)
       and not e.excluded
       and e.evidence_type::text = any(%(types)s)
)
"""


def course_overview(
    conn: Connection, actor: Actor, course_id: UUID, policy: TeacherViewPolicy
) -> TeacherCourseOverview:
    row = _authorized_course(conn, actor, course_id)
    if row is None:
        raise TeacherCourseNotFoundError(course_id)
    course = _course(row, policy.min_cohort)
    cohort = TeacherCohort(
        student_count=course.student_count,
        min_cohort=policy.min_cohort,
        suppressed=course.suppressed,
    )
    if course.suppressed:
        return TeacherCourseOverview(
            course=course,
            cohort=cohort,
            as_of=None,
            state_totals=None,
            skills=[],
            common_mapped_skills=[],
            verification_needs=[],
            evidence_counts=None,
        )

    params = {
        "course": course_id,
        "types": COUNTED_TYPES,
        "independent": INDEPENDENT_TYPES,
        "window": f"{policy.window_days} days",
        "top": policy.top_n,
    }
    skills = conn.execute(
        """
        select n.id, n.canonical_name, t.canonical_name, cs.importance
          from public.course_skills cs
          join public.skill_nodes n on n.id = cs.skill_id
          left join lateral (
              select tn.canonical_name
                from public.skill_edges e
                join public.skill_nodes tn
                  on tn.id = e.from_skill_id and tn.node_kind in ('DOMAIN', 'SUBJECT', 'TOPIC')
                join public.course_skills tcs
                  on tcs.course_id = cs.course_id and tcs.skill_id = tn.id and tcs.active
               where e.edge_type = 'PARENT' and e.to_skill_id = cs.skill_id
               order by tn.canonical_name
               limit 1
          ) t on true
         where cs.course_id = %(course)s and cs.active and n.status = 'ACTIVE'
           and n.node_kind in ('SKILL', 'SUBSKILL')
         order by t.canonical_name nulls last, cs.importance desc, n.canonical_name
        """,
        params,
    ).fetchall()

    states: dict[UUID, dict[str, int]] = {s[0]: dict.fromkeys(MASTERY_STATES, 0) for s in skills}
    as_of = None
    for skill_id, state, count, oldest in conn.execute(
        _SCOPE  # noqa: S608 - constant SQL fragments; values are bound
        + """
        select sk.skill_id, coalesce(l.mastery_state::text, 'UNKNOWN'), count(*),
               min(l.computed_as_of)
          from skills sk
         cross join students st
          left join public.skill_ledger l on l.learner_id = st.user_id and l.skill_id = sk.skill_id
         group by 1, 2
        """,
        params,
    ).fetchall():
        states[skill_id][state] = count
        if oldest is not None and (as_of is None or oldest < as_of):
            as_of = oldest

    with_evidence = dict(
        conn.execute(
            _SCOPE + "select skill_id, count(distinct learner_id) from counted group by skill_id",  # noqa: S608 - constant SQL fragments; values are bound
            params,
        ).fetchall()
    )
    needs = dict(
        conn.execute(
            _SCOPE  # noqa: S608 - constant SQL fragments; values are bound
            + """
            select r.skill_id, count(distinct r.learner_id)
              from public.recommendations r
             where r.state = 'ACTIVE' and r.type in ('VERIFY', 'REVERIFY')
               and r.learner_id in (select user_id from students)
               and r.skill_id in (select skill_id from skills)
             group by r.skill_id
            """,
            params,
        ).fetchall()
    )
    mapped = conn.execute(
        _SCOPE  # noqa: S608 - constant SQL fragments; values are bound
        + """
        select m.skill_id, count(distinct m.learner_id)
          from public.skill_mappings m
         where m.status = 'ACCEPTED'
           and m.learner_id in (select user_id from students)
           and m.skill_id in (select skill_id from skills)
           and m.created_at >= now() - %(window)s::interval
           and not exists (
               select 1 from public.feedback f
                where f.user_id = m.learner_id and f.action in ('WRONG_SKILL', 'DONT_COUNT')
                  and (f.mapping_id = m.id
                       or (f.mapping_id is null and f.segment_id = m.segment_id)))
         group by m.skill_id
        """,
        params,
    ).fetchall()
    evidence = conn.execute(
        _SCOPE  # noqa: S608 - constant SQL fragments; values are bound
        + """
        select count(*) filter (where evidence_type = any(%(independent)s)),
               count(*) filter (where evidence_type = 'VERIFICATION'),
               count(distinct learner_id),
               count(*) filter (where evidence_type = any(%(independent)s)
                                  and occurred_at >= now() - %(window)s::interval),
               count(*) filter (where evidence_type = 'VERIFICATION'
                                  and occurred_at >= now() - %(window)s::interval)
          from counted
        """,
        params,
    ).fetchone()

    names = {s[0]: s[1] for s in skills}
    rows = [
        TeacherSkillRow(
            skill_id=s[0],
            name=s[1],
            topic=s[2],
            importance=float(s[3]),
            states=states[s[0]],
            students_with_evidence=with_evidence.get(s[0], 0),
            verification_need_students=needs.get(s[0], 0),
        )
        for s in skills
    ]
    totals = dict.fromkeys(MASTERY_STATES, 0)
    for row_states in states.values():
        for state, count in row_states.items():
            totals[state] += count

    def ranked(counts: dict[UUID, int]) -> list[tuple[UUID, int]]:
        # Most students first, then the skill name; never ordered by a weakness measure.
        items = [(k, v) for k, v in counts.items() if v > 0 and k in names]
        return sorted(items, key=lambda kv: (-kv[1], names[kv[0]]))[: policy.top_n]

    return TeacherCourseOverview(
        course=course,
        cohort=cohort,
        as_of=as_of,
        state_totals=totals,
        skills=rows,
        common_mapped_skills=[
            TeacherMappedSkill(skill_id=k, name=names[k], students=v)
            for k, v in ranked(dict(mapped))
        ],
        verification_needs=[
            TeacherVerificationNeed(skill_id=k, name=names[k], students=v) for k, v in ranked(needs)
        ],
        evidence_counts=TeacherEvidenceCounts(
            window_days=policy.window_days,
            independent=evidence[0],
            verification=evidence[1],
            students_with_evidence=evidence[2],
            window_independent=evidence[3],
            window_verification=evidence[4],
        ),
    )
