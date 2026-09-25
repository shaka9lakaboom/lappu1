"""P3B persistence: attributions and EvidenceEvents (architecture §7.2, §10.1; ADR 0005).

The P3A analysis of a unit is committed first (processing/persist.py). Each of its
MAPPED segments is then attributed and written in ONE transaction: an attribution
row for every ACCEPTED mapping (ATTRIBUTED or ABSTAINED) plus the evidence events
that qualified - all or nothing, never a partial segment. A MAPPED segment without
attribution rows is simply pending (its job is not COMPLETED): a replayed or
recovered job resumes there without re-running the P3A model calls.

Replay safety: a per-segment advisory lock, the (mapping, attribution_version)
unique key and the unique attribution per evidence event make a second write a
no-op. Database triggers re-check that attributions refer to ACCEPTED mappings and
that evidence copies its provenance exactly.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.intelligence.attribution.engine import (
    ATTRIBUTOR_VERSION,
    PROMPT_VERSION,
    AcceptedSkill,
    AttributionResult,
)
from app.intelligence.evidence.engine import QUALIFIER_VERSION, Qualification
from app.intelligence.processing.persist import ANALYSIS_VERSION

# Idempotency key of a segment's attribution.
ATTRIBUTION_VERSION = "p3b-v1"


@dataclass(frozen=True)
class PendingSegment:
    """A MAPPED segment of the unit whose accepted mappings are not attributed yet."""

    segment_id: UUID
    decision_id: UUID
    learner_id: UUID
    segment_index: int
    segment_count: int
    text: str
    learning_relevance: str | None
    source_message_ids: tuple[UUID, ...]
    occurred_at: datetime
    # Every P3A model run behind the decision (turn/qualification, query, rerank, mapping, adjudication).
    model_run_ids: tuple[UUID, ...]
    skills: tuple[AcceptedSkill, ...]


def pending_segments(conn: Connection, anchor_id: UUID) -> list[PendingSegment]:
    rows = conn.execute(
        """
        select s.id, d.id, s.learner_id, s.segment_index, s.segment_count, s.text,
               s.learning_relevance::text, s.source_message_ids,
               coalesce(rm.occurred_at, rm.captured_at),
               array[s.qualification_model_run_id, d.query_model_run_id, d.rerank_model_run_id,
                     d.mapping_model_run_id, d.adjudication_model_run_id]
          from public.activity_segments s
          join public.mapping_decisions d on d.segment_id = s.id and d.outcome = 'MAPPED'
          join public.raw_messages rm on rm.id = s.anchor_message_id
         where s.anchor_message_id = %s and s.analysis_version = %s
           and not exists (select 1 from public.attributions a
                            where a.segment_id = s.id and a.attribution_version = %s)
         order by s.segment_index
        """,
        (anchor_id, ANALYSIS_VERSION, ATTRIBUTION_VERSION),
    ).fetchall()
    pending = []
    for row in rows:
        skills = conn.execute(
            """
            select m.id, m.skill_id, n.canonical_name, n.description, m.confidence,
                   m.evidence_span, m.reason_code, n.difficulty_band
              from public.skill_mappings m join public.skill_nodes n on n.id = m.skill_id
             where m.segment_id = %s and m.status = 'ACCEPTED'
             order by m.confidence desc, n.canonical_name
            """,
            (row[0],),
        ).fetchall()
        if not skills:
            continue
        pending.append(
            PendingSegment(
                segment_id=row[0],
                decision_id=row[1],
                learner_id=row[2],
                segment_index=row[3],
                segment_count=row[4],
                text=row[5],
                learning_relevance=row[6],
                source_message_ids=tuple(row[7]),
                occurred_at=row[8],
                model_run_ids=tuple(dict.fromkeys(r for r in row[9] if r is not None)),
                skills=tuple(AcceptedSkill(*s) for s in skills),
            )
        )
    return pending


def _attributed(conn: Connection, segment_id: UUID) -> bool:
    return (
        conn.execute(
            "select 1 from public.attributions where segment_id = %s and attribution_version = %s "
            "limit 1",
            (segment_id, ATTRIBUTION_VERSION),
        ).fetchone()
        is not None
    )


def persist_attribution(
    conn: Connection,
    segment: PendingSegment,
    result: AttributionResult,
    qualifications: dict[str, Qualification],
    *,
    policy_snapshot: dict,
    processing_job_id: UUID | None,
) -> bool:
    """Write the segment's attributions and evidence. False if already written (no-op)."""
    run_id = result.run.id if result.run else None
    with conn.transaction():
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"attribution:{segment.segment_id}",),
        )
        if _attributed(conn, segment.segment_id):
            return False
        for skill in segment.skills:
            key = str(skill.skill_id)
            item = result.items.get(key) if result.items is not None else None
            qualification = qualifications.get(key)
            if item is None or qualification is None:
                conn.execute(
                    """
                    insert into public.attributions (
                        learner_id, mapping_id, decision_id, segment_id, skill_id, status,
                        rationale_code, evidence_decision, model_run_id, prompt_version,
                        attributor_version, attribution_version, processing_job_id
                    ) values (%s, %s, %s, %s, %s, 'ABSTAINED', %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        segment.learner_id,
                        skill.mapping_id,
                        segment.decision_id,
                        segment.segment_id,
                        skill.skill_id,
                        result.abstain_reason or "MODEL_OUTPUT_INVALID",
                        result.abstain_reason or "MODEL_OUTPUT_INVALID",
                        run_id,
                        PROMPT_VERSION,
                        ATTRIBUTOR_VERSION,
                        ATTRIBUTION_VERSION,
                        processing_job_id,
                    ),
                )
                continue
            (attribution_id,) = conn.execute(
                """
                insert into public.attributions (
                    learner_id, mapping_id, decision_id, segment_id, skill_id, status, actor,
                    confidence, student_span, ai_span, proposed_evidence_type, outcome_signal,
                    rationale_code, evidence_decision, model_run_id, prompt_version,
                    attributor_version, attribution_version, processing_job_id
                ) values (
                    %s, %s, %s, %s, %s, 'ATTRIBUTED', %s::public.evidence_actor, %s, %s, %s, %s,
                    %s::public.outcome_signal, %s, %s, %s, %s, %s, %s, %s
                )
                returning id
                """,
                (
                    segment.learner_id,
                    skill.mapping_id,
                    segment.decision_id,
                    segment.segment_id,
                    skill.skill_id,
                    item.actor,
                    item.confidence,
                    item.student_evidence_span,
                    item.ai_evidence_span,
                    item.evidence_type,
                    item.outcome_signal,
                    item.reason_code,
                    qualification.decision,
                    run_id,
                    PROMPT_VERSION,
                    ATTRIBUTOR_VERSION,
                    ATTRIBUTION_VERSION,
                    processing_job_id,
                ),
            ).fetchone()
            evidence = qualification.evidence
            if evidence is None:
                continue
            model_runs = list(
                dict.fromkeys([*segment.model_run_ids, *([run_id] if run_id else [])])
            )
            conn.execute(
                """
                insert into public.evidence_events (
                    learner_id, skill_id, source_type, source_id, attribution_id, mapping_id,
                    decision_id, segment_id, raw_message_ids, evidence_type, actor,
                    outcome_signal, outcome, difficulty, difficulty_multiplier, independence,
                    base_weight, strength, mapping_confidence, attribution_confidence,
                    evidence_confidence, evidence_span, model_run_ids, qualification_reason,
                    qualifier_version, policy_snapshot, occurred_at, processing_job_id
                ) values (
                    %s, %s, 'AI_ACTIVITY', %s, %s, %s, %s, %s, %s, %s::public.evidence_type,
                    %s::public.evidence_actor, %s::public.outcome_signal, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    segment.learner_id,
                    skill.skill_id,
                    attribution_id,
                    attribution_id,
                    skill.mapping_id,
                    segment.decision_id,
                    segment.segment_id,
                    list(segment.source_message_ids),
                    evidence.evidence_type,
                    evidence.actor,
                    evidence.outcome_signal,
                    evidence.outcome,
                    evidence.difficulty,
                    evidence.difficulty_multiplier,
                    evidence.independence,
                    evidence.base_weight,
                    evidence.strength,
                    evidence.mapping_confidence,
                    evidence.attribution_confidence,
                    evidence.evidence_confidence,
                    Jsonb(
                        {
                            "student": item.student_evidence_span,
                            "ai": item.ai_evidence_span,
                            "mapping": skill.mapping_span,
                        }
                    ),
                    model_runs,
                    evidence.qualification_reason,
                    QUALIFIER_VERSION,
                    Jsonb(policy_snapshot),
                    segment.occurred_at,
                    processing_job_id,
                ),
            )
    return True


def unit_evidence_skills(conn: Connection, anchor_id: UUID) -> list[UUID]:
    """Skills with evidence from this unit's segments (the ledger rows a job recomputes)."""
    rows = conn.execute(
        """
        select distinct e.skill_id from public.evidence_events e
          join public.activity_segments s on s.id = e.segment_id
         where s.anchor_message_id = %s and s.analysis_version = %s
        """,
        (anchor_id, ANALYSIS_VERSION),
    ).fetchall()
    return [r[0] for r in rows]


def unit_is_mapped(conn: Connection, anchor_id: UUID) -> bool:
    return (
        conn.execute(
            """
            select 1 from public.mapping_decisions d
              join public.activity_segments s on s.id = d.segment_id
             where s.anchor_message_id = %s and s.analysis_version = %s and d.outcome = 'MAPPED'
             limit 1
            """,
            (anchor_id, ANALYSIS_VERSION),
        ).fetchone()
        is not None
    )
