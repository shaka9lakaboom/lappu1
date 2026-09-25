"""GET /v1/skills/{skill_id}: the learner-facing explanation package (architecture §12.1 Skill
Detail, §13; ADR 0006). No model call.

Every value is read from stored rows or derived by the same deterministic engines that built
the ledger: the canonical skill and its course context, the ledger row (UNKNOWN when there is
none - never zero mastery), the mastery explanation and its gates, the debt band and factors
(only when eligible), every EvidenceEvent with its provenance back to the captured activity
(excluded ones included, marked), and the current recommendation.

The mastery "Why?" re-derives per-evidence weights at the ledger's computed_as_of, so the
explanation matches the stored row. Nothing here returns a prompt, a policy snapshot or raw
model output; captured text is a short plain-text preview.
"""

from datetime import UTC, datetime
from uuid import UUID

from psycopg import Connection

from app.experience.feedback import correction_for, list_feedback
from app.experience.models import (
    DebtExplanation,
    EvidenceSource,
    EvidenceTimelineItem,
    MasteryExplanation,
    SkillCourseContext,
    SkillDetailResponse,
    SkillInfo,
    SkillPrerequisite,
)
from app.intelligence.contracts import EvidenceEvent, LedgerEntry
from app.intelligence.debt.engine import is_delegation
from app.intelligence.explanation import (
    debt_band,
    debt_factors,
    mastery_explanation_code,
    mastery_gates,
)
from app.intelligence.mastery.engine import (
    ALGORITHM_VERSION,
    age_days,
    compute_mastery,
    recency_multiplier,
)
from app.intelligence.mastery.ledger import load_records, read_ledger
from app.intelligence.policy import IntelligencePolicy
from app.intelligence.recommendations.service import (
    list_recommendations,
    refresh_recommendations,
)


class SkillNotFoundError(LookupError):
    pass


def _r(value: float | None) -> float | None:
    """Stored `real` confidences come back as float32; present them rounded."""
    return None if value is None else round(float(value), 6)


def _skill(conn: Connection, skill_id: UUID) -> SkillInfo | None:
    row = conn.execute(
        """
        select n.id, n.slug, n.canonical_name, n.description, n.node_kind::text, n.status::text,
               n.difficulty_band, n.assessment_types,
               coalesce((select array_agg(a.alias order by a.alias) from public.skill_aliases a
                          where a.skill_id = n.id and a.alias_kind <> 'CANONICAL'), '{}')
          from public.skill_nodes n
         where n.id = %s and n.node_kind in ('SKILL', 'SUBSKILL')
        """,
        (skill_id,),
    ).fetchone()
    if row is None:
        return None
    return SkillInfo(
        skill_id=row[0],
        slug=row[1],
        canonical_name=row[2],
        description=row[3],
        node_kind=row[4],
        status=row[5],
        difficulty_band=row[6],
        assessment_types=list(row[7]),
        aliases=list(row[8]),
    )


def _courses(conn: Connection, learner_id: UUID, skill_id: UUID) -> list[SkillCourseContext]:
    rows = conn.execute(
        """
        select c.id, c.name, cs.importance, t.id, t.canonical_name
          from public.course_skills cs
          join public.course_memberships m
            on m.course_id = cs.course_id and m.user_id = %(learner)s and m.role = 'STUDENT'
          join public.courses c on c.id = cs.course_id and c.status = 'ACTIVE'
          left join lateral (
              select tn.id, tn.canonical_name
                from public.skill_edges e
                join public.skill_nodes tn
                  on tn.id = e.from_skill_id and tn.node_kind in ('DOMAIN', 'SUBJECT', 'TOPIC')
                join public.course_skills tcs
                  on tcs.course_id = cs.course_id and tcs.skill_id = tn.id and tcs.active
               where e.edge_type = 'PARENT' and e.to_skill_id = cs.skill_id
               order by tn.canonical_name
               limit 1
          ) t on true
         where cs.skill_id = %(skill)s and cs.active
         order by c.name, c.id
        """,
        {"learner": learner_id, "skill": skill_id},
    ).fetchall()
    return [
        SkillCourseContext(
            course_id=r[0], name=r[1], importance=float(r[2]), topic_id=r[3], topic_name=r[4]
        )
        for r in rows
    ]


def _prerequisites(conn: Connection, learner_id: UUID, skill_id: UUID) -> list[SkillPrerequisite]:
    rows = conn.execute(
        """
        select p.id, p.canonical_name, coalesce(l.mastery_state::text, 'UNKNOWN')
          from public.skill_edges e
          join public.skill_nodes p on p.id = e.from_skill_id and p.status = 'ACTIVE'
          left join public.skill_ledger l on l.skill_id = p.id and l.learner_id = %s
         where e.edge_type = 'PREREQUISITE' and e.to_skill_id = %s
         order by p.canonical_name
        """,
        (learner_id, skill_id),
    ).fetchall()
    return [SkillPrerequisite(skill_id=r[0], canonical_name=r[1], mastery_state=r[2]) for r in rows]


_EVIDENCE_SQL = """
select e.id, e.learner_id, e.skill_id, e.source_type::text, e.source_id, e.attribution_id,
       e.mapping_id, e.decision_id, e.segment_id, e.raw_message_ids, e.evidence_type::text,
       e.actor::text, e.outcome_signal::text, e.outcome, e.difficulty, e.independence, e.strength,
       e.mapping_confidence, e.attribution_confidence, e.grading_confidence, e.evidence_confidence,
       e.evidence_span, e.model_run_ids, e.qualification_reason, e.excluded, e.exclusion_reason,
       e.excluded_at, e.occurred_at, e.created_at,
       a.rationale_code, s.conversation_id, coalesce(am.occurred_at, am.captured_at),
       left(um.content_text, 280)
  from public.evidence_events e
  left join public.attributions a on a.id = e.attribution_id
  left join public.activity_segments s on s.id = e.segment_id
  left join public.raw_messages am on am.id = s.anchor_message_id
  left join public.raw_messages um on um.id = s.user_message_id
 where e.learner_id = %s and e.skill_id = %s
 order by e.occurred_at desc, e.created_at desc, e.id
"""


def _event(r: tuple) -> EvidenceEvent:
    return EvidenceEvent(
        id=r[0],
        learner_id=r[1],
        skill_id=r[2],
        source_type=r[3],
        source_id=r[4],
        attribution_id=r[5],
        mapping_id=r[6],
        decision_id=r[7],
        segment_id=r[8],
        raw_message_ids=tuple(r[9]),
        evidence_type=r[10],
        actor=r[11],
        outcome_signal=r[12],
        outcome=r[13],
        difficulty=r[14],
        independence=r[15],
        strength=r[16],
        mapping_confidence=_r(r[17]),
        attribution_confidence=_r(r[18]),
        grading_confidence=_r(r[19]),
        evidence_confidence=_r(r[20]),
        evidence_span={str(k): (None if v is None else str(v)) for k, v in r[21].items()},
        model_run_ids=tuple(r[22]),
        qualification_reason=r[23],
        excluded=r[24],
        exclusion_reason=r[25],
        excluded_at=r[26],
        occurred_at=r[27],
        created_at=r[28],
    )


def _unknown_entry(skill: SkillInfo, policy: IntelligencePolicy) -> LedgerEntry:
    """A skill in scope with no ledger row: UNKNOWN, no mean, no debt (never zero mastery)."""
    return LedgerEntry(
        skill_id=skill.skill_id,
        slug=skill.slug,
        canonical_name=skill.canonical_name,
        description=skill.description,
        node_kind=skill.node_kind,  # type: ignore[arg-type]  # SKILL / SUBSKILL only
        difficulty_band=skill.difficulty_band,
        course_ids=(),
        importance=policy.skill_graph.default_importance,
        mastery_state="UNKNOWN",
        mastery_mean=None,
        alpha=policy.mastery.prior_alpha,
        beta=policy.mastery.prior_beta,
        support=0.0,
        debt_score=0.0,
        debt_eligible=False,
        debt_actionable=False,
        debt_band="NONE",
        evidence_count=0,
        performance_evidence_count=0,
        recent_delegation_count=0,
        last_evidence_at=None,
        ledger_version=None,
        computed_as_of=None,
    )


def get_skill_detail(
    conn: Connection, learner_id: UUID, skill_id: UUID, *, policy: IntelligencePolicy
) -> SkillDetailResponse:
    skill = _skill(conn, skill_id)
    if skill is None:
        raise SkillNotFoundError(skill_id)
    courses = _courses(conn, learner_id, skill_id)
    evidence_rows = conn.execute(_EVIDENCE_SQL, (learner_id, skill_id)).fetchall()
    components_row = conn.execute(
        "select debt_components from public.skill_ledger where learner_id = %s and skill_id = %s",
        (learner_id, skill_id),
    ).fetchone()
    in_courses = bool(courses) and skill.status == "ACTIVE"
    if not in_courses and not evidence_rows and components_row is None:
        # Not a skill of this learner: none of their courses, evidence or ledger.
        raise SkillNotFoundError(skill_id)

    refresh_recommendations(conn, learner_id, policy=policy)
    entries = read_ledger(conn, learner_id, policy=policy, skill_ids=[skill_id]).skills
    ledger = entries[0] if entries else _unknown_entry(skill, policy)

    as_of = ledger.computed_as_of or datetime.now(UTC)
    records = load_records(conn, learner_id, [skill_id]).get(skill_id, [])
    derived = compute_mastery(
        records, policy.mastery, as_of, reverification=policy.verification.reverification
    )
    by_id = {r.id: r for r in records}
    segment_ids = sorted({r[8] for r in evidence_rows if r[8] is not None})
    feedback = list_feedback(conn, learner_id, skill_id=skill_id, segment_ids=segment_ids or None)

    timeline = []
    for row in evidence_rows:
        event = _event(row)
        record = by_id.get(event.id)
        counts = record is not None and record.performance_bearing
        weight = (
            event.strength
            * recency_multiplier(
                age_days(as_of, event.occurred_at), policy.mastery.recency_half_life_days
            )
            if counts
            else 0.0
        )
        timeline.append(
            EvidenceTimelineItem(
                event=event,
                reason_code=row[29],
                counts_toward_mastery=counts,
                counts_toward_debt=record is not None and is_delegation(record, policy.debt, as_of),
                current_weight=round(weight, 6),
                source=(
                    EvidenceSource(
                        conversation_id=row[30],
                        raw_message_ids=event.raw_message_ids,
                        captured_at=row[31],
                        learner_message_preview=row[32],
                    )
                    if event.segment_id is not None
                    else None
                ),
                correction=(
                    correction_for(
                        feedback,
                        evidence_id=event.id,
                        mapping_id=event.mapping_id,
                        segment_id=event.segment_id,
                    )
                    if event.excluded
                    else None
                ),
            )
        )

    state = ledger.mastery_state
    mean = ledger.mastery_mean if ledger.mastery_mean is not None else derived.mastery_mean
    mastery = MasteryExplanation(
        state=state,
        explanation_code=mastery_explanation_code(
            state,
            evidence_count=ledger.evidence_count,
            performance_evidence_count=ledger.performance_evidence_count,
            mastery_mean=mean,
            support=ledger.support,
            has_application=derived.has_application,
            policy=policy.mastery,
            verification_standing=derived.verification_standing,
            recently_verified=derived.recently_verified,
        ),
        support=ledger.support,
        mastery_mean=ledger.mastery_mean,
        evidence_count=ledger.evidence_count,
        performance_evidence_count=ledger.performance_evidence_count,
        excluded_evidence_count=sum(1 for t in timeline if t.event.excluded),
        has_independent_application=derived.has_application,
        gates=mastery_gates(
            state,
            mastery_mean=mean,
            support=ledger.support,
            has_application=derived.has_application,
            policy=policy.mastery,
            verification=derived.recently_verified or derived.previously_verified,
            recently_verified=derived.recently_verified,
        ),
        computed_as_of=ledger.computed_as_of,
        algorithm_version=ALGORITHM_VERSION,
    )

    components = components_row[0] if components_row else {}
    debt = DebtExplanation(
        eligible=ledger.debt_eligible,
        band=debt_band(ledger.debt_eligible, ledger.debt_score, policy.recommendations.debt_bands),
        actionable=ledger.debt_actionable,
        eligibility_code=components.get("eligibility", "NO_DELEGATION"),
        recent_delegation_count=ledger.recent_delegation_count,
        min_recent_delegations=policy.debt.min_recent_delegations,
        verification=components.get("verification") if ledger.debt_eligible else None,
        factors=debt_factors(components) if ledger.debt_eligible else [],
        score=ledger.debt_score,
    )

    recommendation = next(
        iter(list_recommendations(conn, learner_id, skill_ids=[skill_id], include_no_action=True)),
        None,
    )
    return SkillDetailResponse(
        skill=skill,
        courses=courses,
        prerequisites=_prerequisites(conn, learner_id, skill_id),
        ledger=ledger,
        mastery=mastery,
        debt=debt,
        evidence=timeline,
        recommendation=recommendation,
        feedback=feedback,
    )
