"""The recommendation queue: a rebuildable, deterministic projection of the ledger (ADR 0006).

`refresh_recommendations` re-derives the next action of EVERY skill in the learner's scope
(active course skills plus any skill with a ledger row) with the Engine 16 rules and
reconciles `public.recommendations`:

* the same action (type, reason, related skill) keeps its row; only its priority, the
  mastery state it read and its inputs are updated when they changed;
* a different action supersedes the ACTIVE row and inserts a new one;
* a skill that left the scope has its ACTIVE row superseded.

The whole scope is refreshed each time because the VERIFY burden cap ranks skills against
each other. Refreshes of one learner are serialized by an advisory lock, writes happen only
on change, and the same ledger always yields the same queue. No model call.
"""

from collections.abc import Sequence
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.experience.models import Recommendation
from app.intelligence.policy import IntelligencePolicy
from app.intelligence.recommendations.engine import (
    ALGORITHM_VERSION,
    PrerequisiteSignal,
    RecommendationDecision,
    SkillSignal,
    recommend_all,
)

# Same scope as GET /v1/ledger: the learner's active course skills plus every ledger row.
_SCOPE_SQL = """
with scope as (
    select cs.skill_id, max(cs.importance) as importance
      from public.course_skills cs
      join public.course_memberships m on m.course_id = cs.course_id and m.user_id = %(learner)s
      join public.courses c on c.id = cs.course_id and c.status = 'ACTIVE'
     where cs.active
     group by cs.skill_id
)
select n.id, sc.importance, l.mastery_state::text, l.mastery_mean, l.support, l.debt_eligible,
       l.debt_score, l.performance_evidence_count, l.ledger_version
  from public.skill_nodes n
  left join scope sc on sc.skill_id = n.id
  left join public.skill_ledger l on l.skill_id = n.id and l.learner_id = %(learner)s
 where n.node_kind in ('SKILL', 'SUBSKILL')
   and ((sc.skill_id is not null and n.status = 'ACTIVE') or l.skill_id is not null)
"""


def load_signals(
    conn: Connection, learner_id: UUID, policy: IntelligencePolicy
) -> list[SkillSignal]:
    rows = conn.execute(_SCOPE_SQL, {"learner": learner_id}).fetchall()
    skill_ids = [r[0] for r in rows]
    prerequisites: dict[UUID, list[PrerequisiteSignal]] = {}
    if skill_ids:
        for to_id, from_id, state, mean in conn.execute(
            """
            select e.to_skill_id, e.from_skill_id, l.mastery_state::text, l.mastery_mean
              from public.skill_edges e
              join public.skill_nodes p on p.id = e.from_skill_id and p.status = 'ACTIVE'
              left join public.skill_ledger l on l.skill_id = e.from_skill_id and l.learner_id = %s
             where e.edge_type = 'PREREQUISITE' and e.to_skill_id = any(%s)
             order by e.to_skill_id, e.from_skill_id
            """,
            (learner_id, skill_ids),
        ).fetchall():
            known = state is not None and state != "UNKNOWN"
            prerequisites.setdefault(to_id, []).append(
                PrerequisiteSignal(
                    from_id, state or "UNKNOWN", float(mean) if known and mean is not None else None
                )
            )
    default_importance = policy.skill_graph.default_importance
    signals = []
    for r in rows:
        has_row = r[2] is not None
        signals.append(
            SkillSignal(
                skill_id=r[0],
                mastery_state=r[2] if has_row else "UNKNOWN",
                mastery_mean=float(r[3]) if has_row else None,
                support=float(r[4]) if has_row else 0.0,
                importance=float(r[1]) if r[1] is not None else default_importance,
                debt_eligible=bool(r[5]) if has_row else False,
                debt_score=float(r[6]) if has_row else 0.0,
                performance_evidence_count=int(r[7]) if has_row else 0,
                ledger_version=r[8],
                prerequisites=tuple(prerequisites.get(r[0], ())),
            )
        )
    return signals


def _insert(conn: Connection, learner_id: UUID, d: RecommendationDecision) -> None:
    conn.execute(
        """
        insert into public.recommendations (
            learner_id, skill_id, type, priority, reason_code, related_skill_id, mastery_state,
            inputs, algorithm_version
        ) values (%s, %s, %s::public.recommendation_type, %s, %s, %s, %s::public.mastery_state,
                  %s, %s)
        """,
        (
            learner_id,
            d.skill_id,
            d.type,
            d.priority,
            d.reason_code,
            d.related_skill_id,
            d.mastery_state,
            Jsonb(d.inputs),
            ALGORITHM_VERSION,
        ),
    )


def _supersede(conn: Connection, recommendation_id: UUID) -> None:
    conn.execute(
        "update public.recommendations set state = 'SUPERSEDED', resolved_at = now() "
        "where id = %s and state = 'ACTIVE'",
        (recommendation_id,),
    )


def refresh_recommendations(
    conn: Connection, learner_id: UUID, *, policy: IntelligencePolicy
) -> list[RecommendationDecision]:
    """Reconcile the learner's ACTIVE recommendations with the rules; returns the decisions."""
    with conn.transaction():
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"recommendations:{learner_id}",),
        )
        decisions = recommend_all(load_signals(conn, learner_id, policy), policy=policy)
        active = {
            r[1]: r
            for r in conn.execute(
                """
                select id, skill_id, type::text, reason_code, related_skill_id, priority,
                       mastery_state::text, inputs, algorithm_version
                  from public.recommendations
                 where learner_id = %s and state = 'ACTIVE'
                """,
                (learner_id,),
            ).fetchall()
        }
        for d in decisions:
            current = active.pop(d.skill_id, None)
            if current is not None and (current[2], current[3], current[4], current[8]) == (
                *d.identity,
                ALGORITHM_VERSION,
            ):
                if (current[5], current[6], current[7]) != (d.priority, d.mastery_state, d.inputs):
                    conn.execute(
                        "update public.recommendations set priority = %s, "
                        "mastery_state = %s::public.mastery_state, inputs = %s "
                        "where id = %s and state = 'ACTIVE'",
                        (d.priority, d.mastery_state, Jsonb(d.inputs), current[0]),
                    )
                continue
            if current is not None:
                _supersede(conn, current[0])
            _insert(conn, learner_id, d)
        for stale in active.values():  # skills that left the learner's scope
            _supersede(conn, stale[0])
    return decisions


_READ_SQL = """
with scope as (
    select cs.skill_id, array_agg(distinct cs.course_id) as course_ids
      from public.course_skills cs
      join public.course_memberships m on m.course_id = cs.course_id and m.user_id = %(learner)s
      join public.courses c on c.id = cs.course_id and c.status = 'ACTIVE'
     where cs.active
     group by cs.skill_id
)
select r.id, r.skill_id, n.canonical_name, r.type::text, r.priority, r.reason_code, r.state::text,
       r.mastery_state::text, r.related_skill_id, rn.canonical_name, r.inputs,
       coalesce(sc.course_ids, '{}'), r.created_at, r.updated_at
  from public.recommendations r
  join public.skill_nodes n on n.id = r.skill_id
  left join public.skill_nodes rn on rn.id = r.related_skill_id
  left join scope sc on sc.skill_id = r.skill_id
 where r.learner_id = %(learner)s and r.state = 'ACTIVE'
   and (%(course)s::uuid is null or %(course)s::uuid = any(sc.course_ids))
   and (%(skills)s::uuid[] is null or r.skill_id = any(%(skills)s::uuid[]))
   and (%(no_action)s or r.type <> 'NO_ACTION')
 order by r.priority desc, n.canonical_name, r.skill_id
 limit %(limit)s
"""


def list_recommendations(
    conn: Connection,
    learner_id: UUID,
    *,
    course_id: UUID | None = None,
    skill_ids: Sequence[UUID] | None = None,
    include_no_action: bool = False,
    limit: int = 50,
) -> list[Recommendation]:
    """The learner's ACTIVE recommendations, highest priority first."""
    rows = conn.execute(
        _READ_SQL,
        {
            "learner": learner_id,
            "course": course_id,
            "skills": list(skill_ids) if skill_ids is not None else None,
            "no_action": include_no_action,
            "limit": limit,
        },
    ).fetchall()
    return [
        Recommendation(
            id=r[0],
            skill_id=r[1],
            canonical_name=r[2],
            type=r[3],
            priority=r[4],
            reason_code=r[5],
            state=r[6],
            mastery_state=r[7],
            related_skill_id=r[8],
            related_skill_name=r[9],
            debt_band=r[10].get("debt_band", "NONE"),
            verify_deferred=bool(r[10].get("verify_deferred", False)),
            course_ids=tuple(r[11]),
            created_at=r[12],
            updated_at=r[13],
        )
        for r in rows
    ]
