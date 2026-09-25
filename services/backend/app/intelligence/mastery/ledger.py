"""The Verified Skill Ledger: a rebuildable projection of EvidenceEvents (architecture §7.2, §10).

`recompute_ledger` re-derives mastery (mastery/engine.py) and AI Assistance Debt
(debt/engine.py) for a learner's skills from ALL their evidence - never
incrementally - and upserts `skill_ledger`. It is deterministic and idempotent:
the same evidence and `as_of` give the same row, and an unchanged row is not
rewritten (its ledger_version stays). Recomputations of one learner are
serialized by an advisory lock, so a slower job can never overwrite a newer
result with an older evidence snapshot. `rebuild_ledger` recomputes every skill
with evidence, e.g. after the table was dropped or an algorithm changed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.intelligence.contracts import LedgerResponse, SkillLedgerSummary
from app.intelligence.debt.engine import DebtResult, compute_debt
from app.intelligence.mastery.engine import (
    ALGORITHM_VERSION,
    EvidenceRecord,
    MasteryResult,
    compute_mastery,
)
from app.intelligence.policy import IntelligencePolicy


@dataclass(frozen=True)
class LedgerRow:
    skill_id: UUID
    mastery: MasteryResult
    debt: DebtResult
    importance: float


def load_records(
    conn: Connection, learner_id: UUID, skill_ids: Sequence[UUID] | None = None
) -> dict[UUID, list[EvidenceRecord]]:
    rows = conn.execute(
        """
        select e.id, e.skill_id, e.source_type::text, e.evidence_type::text, e.actor::text,
               e.outcome_signal::text, e.outcome, e.strength, e.evidence_confidence, e.occurred_at,
               e.excluded, a.rationale_code, s.learning_relevance::text, m.status::text
          from public.evidence_events e
          left join public.attributions a on a.id = e.attribution_id
          left join public.activity_segments s on s.id = e.segment_id
          left join public.skill_mappings m on m.id = e.mapping_id
         where e.learner_id = %s and (%s::uuid[] is null or e.skill_id = any(%s::uuid[]))
         order by e.occurred_at, e.id
        """,
        (learner_id, skill_ids, skill_ids),
    ).fetchall()
    records: dict[UUID, list[EvidenceRecord]] = {}
    for row in rows:
        records.setdefault(row[1], []).append(EvidenceRecord(*row))
    return records


def skill_importance(
    conn: Connection, learner_id: UUID, skill_ids: Sequence[UUID], default: float
) -> dict[UUID, float]:
    """The highest importance of the skill in the learner's active courses; else the default."""
    rows = conn.execute(
        """
        select cs.skill_id, max(cs.importance)
          from public.course_skills cs
          join public.course_memberships m on m.course_id = cs.course_id and m.user_id = %s
          join public.courses c on c.id = cs.course_id and c.status = 'ACTIVE'
         where cs.active and cs.skill_id = any(%s)
         group by cs.skill_id
        """,
        (learner_id, list(skill_ids)),
    ).fetchall()
    found = {r[0]: float(r[1]) for r in rows}
    return {s: found.get(s, default) for s in skill_ids}


def derive(
    records: Sequence[EvidenceRecord],
    *,
    importance: float,
    policy: IntelligencePolicy,
    as_of: datetime,
) -> tuple[MasteryResult, DebtResult]:
    mastery = compute_mastery(records, policy.mastery, as_of)
    debt = compute_debt(records, mastery, importance=importance, policy=policy.debt, as_of=as_of)
    return mastery, debt


def _policy_snapshot(policy: IntelligencePolicy) -> dict:
    return {
        "mastery": policy.mastery.model_dump(mode="json"),
        "debt": policy.debt.model_dump(mode="json"),
    }


def _r(value: float) -> float:
    return round(value, 6)


def recompute_ledger(
    conn: Connection,
    learner_id: UUID,
    skill_ids: Sequence[UUID],
    *,
    policy: IntelligencePolicy,
    as_of: datetime | None = None,
) -> list[LedgerRow]:
    skill_ids = list(dict.fromkeys(skill_ids))
    if not skill_ids:
        return []
    as_of = as_of or datetime.now(UTC)
    snapshot = Jsonb(_policy_snapshot(policy))
    results: list[LedgerRow] = []
    with conn.transaction():
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"ledger:{learner_id}",)
        )
        records = load_records(conn, learner_id, skill_ids)
        importance = skill_importance(
            conn, learner_id, skill_ids, policy.skill_graph.default_importance
        )
        for skill_id in skill_ids:
            mastery, debt = derive(
                records.get(skill_id, []),
                importance=importance[skill_id],
                policy=policy,
                as_of=as_of,
            )
            results.append(LedgerRow(skill_id, mastery, debt, importance[skill_id]))
            conn.execute(
                """
                insert into public.skill_ledger as l (
                    learner_id, skill_id, alpha, beta, mastery_mean, support, mastery_state,
                    debt_score, debt_eligible, debt_components, evidence_count,
                    performance_evidence_count, recent_delegation_count, last_evidence_at,
                    computed_as_of, algorithm_version, policy_snapshot
                ) values (
                    %s, %s, %s, %s, %s, %s, %s::public.mastery_state, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                on conflict (learner_id, skill_id) do update set
                    alpha = excluded.alpha, beta = excluded.beta,
                    mastery_mean = excluded.mastery_mean, support = excluded.support,
                    mastery_state = excluded.mastery_state, debt_score = excluded.debt_score,
                    debt_eligible = excluded.debt_eligible,
                    debt_components = excluded.debt_components,
                    evidence_count = excluded.evidence_count,
                    performance_evidence_count = excluded.performance_evidence_count,
                    recent_delegation_count = excluded.recent_delegation_count,
                    last_evidence_at = excluded.last_evidence_at,
                    computed_as_of = excluded.computed_as_of,
                    algorithm_version = excluded.algorithm_version,
                    policy_snapshot = excluded.policy_snapshot,
                    ledger_version = l.ledger_version + 1
                where (l.alpha, l.beta, l.mastery_mean, l.support, l.mastery_state, l.debt_score,
                       l.debt_eligible, l.debt_components, l.evidence_count,
                       l.performance_evidence_count, l.recent_delegation_count,
                       l.last_evidence_at, l.algorithm_version, l.policy_snapshot)
                      is distinct from
                      (excluded.alpha, excluded.beta, excluded.mastery_mean, excluded.support,
                       excluded.mastery_state, excluded.debt_score, excluded.debt_eligible,
                       excluded.debt_components, excluded.evidence_count,
                       excluded.performance_evidence_count, excluded.recent_delegation_count,
                       excluded.last_evidence_at, excluded.algorithm_version,
                       excluded.policy_snapshot)
                """,
                (
                    learner_id,
                    skill_id,
                    _r(mastery.alpha),
                    _r(mastery.beta),
                    _r(mastery.alpha) / (_r(mastery.alpha) + _r(mastery.beta)),
                    _r(mastery.support),
                    mastery.state,
                    debt.score,
                    debt.eligible,
                    Jsonb(debt.components),
                    mastery.evidence_count,
                    mastery.performance_evidence_count,
                    debt.recent_delegation_count,
                    mastery.last_evidence_at,
                    as_of,
                    ALGORITHM_VERSION,
                    snapshot,
                ),
            )
    return results


def rebuild_ledger(
    conn: Connection,
    learner_id: UUID,
    *,
    policy: IntelligencePolicy,
    as_of: datetime | None = None,
) -> list[LedgerRow]:
    """Recompute every skill the learner has evidence for (the ledger is only a cache)."""
    skill_ids = [
        r[0]
        for r in conn.execute(
            "select distinct skill_id from public.evidence_events where learner_id = %s",
            (learner_id,),
        ).fetchall()
    ]
    return recompute_ledger(conn, learner_id, skill_ids, policy=policy, as_of=as_of)


class CourseNotFoundError(LookupError):
    pass


def read_ledger(
    conn: Connection,
    learner_id: UUID,
    *,
    policy: IntelligencePolicy,
    course_id: UUID | None = None,
) -> LedgerResponse:
    """The learner's course skills (UNKNOWN when without evidence) plus every ledger row.

    With `course_id`, only that course's skills (the learner must be a member)."""
    if (
        course_id is not None
        and not conn.execute(
            "select 1 from public.course_memberships where course_id = %s and user_id = %s",
            (course_id, learner_id),
        ).fetchone()
    ):
        raise CourseNotFoundError(course_id)
    rows = conn.execute(
        """
        with scope as (
            select cs.skill_id, array_agg(cs.course_id order by cs.course_id) as course_ids,
                   max(cs.importance) as importance
              from public.course_skills cs
              join public.course_memberships m on m.course_id = cs.course_id and m.user_id = %(learner)s
              join public.courses c on c.id = cs.course_id and c.status = 'ACTIVE'
             where cs.active and (%(course)s::uuid is null or cs.course_id = %(course)s::uuid)
             group by cs.skill_id
        )
        select n.id, n.slug, n.canonical_name, n.description, n.node_kind::text, n.difficulty_band,
               coalesce(sc.course_ids, '{}'), sc.importance,
               l.mastery_state::text, l.mastery_mean, l.alpha, l.beta, l.support, l.debt_score,
               l.debt_eligible, l.evidence_count, l.performance_evidence_count,
               l.recent_delegation_count, l.last_evidence_at, l.ledger_version, l.computed_as_of
          from public.skill_nodes n
          left join scope sc on sc.skill_id = n.id
          left join public.skill_ledger l on l.skill_id = n.id and l.learner_id = %(learner)s
         where n.node_kind in ('SKILL', 'SUBSKILL')
           and ((sc.skill_id is not null and n.status = 'ACTIVE')
                or (%(course)s::uuid is null and l.skill_id is not null))
         order by l.skill_id is null, n.canonical_name
        """,
        {"learner": learner_id, "course": course_id},
    ).fetchall()
    prior = policy.mastery
    default_importance = policy.skill_graph.default_importance
    skills = []
    for r in rows:
        has_row = r[8] is not None
        state = r[8] if has_row else "UNKNOWN"
        eligible = bool(r[14]) if has_row else False
        score = float(r[13]) if has_row else 0.0
        skills.append(
            SkillLedgerSummary(
                skill_id=r[0],
                slug=r[1],
                canonical_name=r[2],
                description=r[3],
                node_kind=r[4],
                difficulty_band=r[5],
                course_ids=tuple(r[6]),
                importance=float(r[7]) if r[7] is not None else default_importance,
                mastery_state=state,
                # Unknown is not weak: no mean is shown without sufficient support.
                mastery_mean=float(r[9]) if has_row and state != "UNKNOWN" else None,
                alpha=float(r[10]) if has_row else prior.prior_alpha,
                beta=float(r[11]) if has_row else prior.prior_beta,
                support=float(r[12]) if has_row else 0.0,
                debt_score=score,
                debt_eligible=eligible,
                debt_actionable=eligible and score >= policy.debt.actionable_min_score,
                evidence_count=r[15] or 0,
                performance_evidence_count=r[16] or 0,
                recent_delegation_count=r[17] or 0,
                last_evidence_at=r[18],
                ledger_version=r[19],
                computed_as_of=r[20],
            )
        )
    return LedgerResponse(algorithm_version=ALGORITHM_VERSION, skills=skills)
