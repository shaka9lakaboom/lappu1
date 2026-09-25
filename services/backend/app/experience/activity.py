"""GET /v1/activity: captured activity plus what SkillMirror derived from it (architecture §12.1
Activity, §13; ADR 0006). No model call.

One row per captured message (as the P1 activity_feed view, which stays in place), newest
first. The anchor message of an analysed turn carries its task units: the route decision,
the mapping outcome, each mapped skill with its attribution actor, evidence type and
exclusion state, and the learner's corrections. The other message of the turn points to its
anchor through `analyzed_in`.

Pages never split a capture batch: the page boundary extends to every message received at
the same instant, and `next_before` continues strictly before it. Captured text is returned
as a short preview for plain-text rendering only.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from psycopg import Connection

from app.experience.feedback import correction_for, list_feedback
from app.experience.models import (
    ActivityMappedSkill,
    ActivityResponse,
    ActivityRow,
    ActivitySegment,
    FeedbackSummary,
)

PREVIEW_CHARS = 280

_ROWS_SQL = """
select m.id, m.conversation_id, c.external_id, m.source_provider::text, m.role::text,
       m.message_index, m.revision_index, m.captured_at, m.received_at, m.context_incomplete,
       left(m.content_text, %(preview)s), char_length(m.content_text),
       j.state::text, j.attempts, j.outcome,
       (select s.anchor_message_id from public.activity_segments s
         where s.learner_id = m.learner_id and m.id = any(s.source_message_ids)
         order by s.created_at, s.segment_index limit 1)
  from public.raw_messages m
  join public.conversations c on c.id = m.conversation_id
  left join public.processing_jobs j on j.entity_id = m.id and j.job_type = 'PROCESS_RAW_MESSAGE'
 where m.learner_id = %(learner)s
   and (%(before)s::timestamptz is null or m.received_at < %(before)s::timestamptz)
   and (%(boundary)s::timestamptz is null or m.received_at >= %(boundary)s::timestamptz)
   and (%(ids)s::uuid[] is null or m.id = any(%(ids)s::uuid[]))
 order by m.received_at desc, m.captured_at desc, m.message_index desc nulls last, m.id desc
"""


def _page_boundary(
    conn: Connection, learner_id: UUID, before: datetime | None, limit: int
) -> tuple[datetime | None, bool]:
    """The received_at of the page's last row, and whether older rows exist beyond it."""
    row = conn.execute(
        """
        select received_at from public.raw_messages
         where learner_id = %s and (%s::timestamptz is null or received_at < %s::timestamptz)
         order by received_at desc
         offset %s limit 1
        """,
        (learner_id, before, before, limit - 1),
    ).fetchone()
    if row is None:
        return None, False
    boundary = row[0]
    older = conn.execute(
        "select 1 from public.raw_messages where learner_id = %s and received_at < %s limit 1",
        (learner_id, boundary),
    ).fetchone()
    return boundary, older is not None


def _segments(
    conn: Connection, learner_id: UUID, anchor_ids: Sequence[UUID]
) -> dict[UUID, list[ActivitySegment]]:
    if not anchor_ids:
        return {}
    seg_rows = conn.execute(
        """
        select s.id, s.anchor_message_id, s.segment_index, s.segment_count, s.route::text,
               s.route_reason, s.learning_relevance::text, s.intent::text, s.context::text,
               s.context_incomplete, d.outcome::text, d.abstain_reason,
               (select count(*) from public.evidence_events e where e.segment_id = s.id),
               (select count(*) from public.evidence_events e
                 where e.segment_id = s.id and e.excluded)
          from public.activity_segments s
          left join public.mapping_decisions d on d.segment_id = s.id
         where s.learner_id = %s and s.anchor_message_id = any(%s)
         order by s.anchor_message_id, s.created_at, s.segment_index
        """,
        (learner_id, list(anchor_ids)),
    ).fetchall()
    segment_ids = [r[0] for r in seg_rows]
    if not segment_ids:
        return {}
    mapping_rows = conn.execute(
        """
        select m.id, m.segment_id, m.skill_id, n.canonical_name, m.status::text, m.confidence,
               m.evidence_span, a.status::text, a.actor::text, a.confidence, a.evidence_decision,
               e.id, e.evidence_type::text, e.outcome_signal::text, coalesce(e.excluded, false),
               e.exclusion_reason
          from public.skill_mappings m
          join public.skill_nodes n on n.id = m.skill_id
          left join lateral (
              select a.status, a.actor, a.confidence, a.evidence_decision
                from public.attributions a where a.mapping_id = m.id
               order by a.created_at desc limit 1
          ) a on true
          left join lateral (
              select e.id, e.evidence_type, e.outcome_signal, e.excluded, e.exclusion_reason
                from public.evidence_events e where e.mapping_id = m.id
               order by e.created_at desc limit 1
          ) e on true
         where m.learner_id = %s and m.segment_id = any(%s)
         order by m.segment_id, (m.status = 'ACCEPTED') desc, m.confidence desc, n.canonical_name
        """,
        (learner_id, segment_ids),
    ).fetchall()
    feedback: list[FeedbackSummary] = list_feedback(conn, learner_id, segment_ids=segment_ids)

    mappings: dict[UUID, list[ActivityMappedSkill]] = {}
    for r in mapping_rows:
        mappings.setdefault(r[1], []).append(
            ActivityMappedSkill(
                mapping_id=r[0],
                skill_id=r[2],
                canonical_name=r[3],
                status=r[4],
                confidence=round(float(r[5]), 6),
                evidence_span=r[6],
                attribution_status=r[7],
                actor=r[8],
                attribution_confidence=round(float(r[9]), 6) if r[9] is not None else None,
                evidence_decision=r[10],
                evidence_id=r[11],
                evidence_type=r[12],
                outcome_signal=r[13],
                excluded=bool(r[14]),
                exclusion_reason=r[15],
                correction=correction_for(
                    feedback, evidence_id=r[11], mapping_id=r[0], segment_id=None
                ),
            )
        )
    result: dict[UUID, list[ActivitySegment]] = {}
    for r in seg_rows:
        segment_correction = next(
            (
                f
                for f in feedback
                if f.action == "DONT_COUNT" and f.mapping_id is None and f.segment_id == r[0]
            ),
            None,
        )
        result.setdefault(r[1], []).append(
            ActivitySegment(
                segment_id=r[0],
                segment_index=r[2],
                segment_count=r[3],
                route=r[4],
                route_reason=r[5],
                learning_relevance=r[6],
                intent=r[7],
                context=r[8],
                context_incomplete=r[9],
                mapping_outcome=r[10],
                abstain_reason=r[11],
                mappings=mappings.get(r[0], []),
                evidence_count=r[12],
                excluded_evidence_count=r[13],
                correction=segment_correction,
            )
        )
    return result


def list_activity(
    conn: Connection,
    learner_id: UUID,
    *,
    limit: int = 50,
    before: datetime | None = None,
    raw_message_ids: Sequence[UUID] | None = None,
) -> ActivityResponse:
    """The learner's captured messages, newest first, with their derived intelligence."""
    boundary, more = (None, False)
    if raw_message_ids is None:
        boundary, more = _page_boundary(conn, learner_id, before, limit)
    rows = conn.execute(
        _ROWS_SQL,
        {
            "preview": PREVIEW_CHARS,
            "learner": learner_id,
            "before": before if raw_message_ids is None else None,
            "boundary": boundary,
            "ids": list(raw_message_ids) if raw_message_ids is not None else None,
        },
    ).fetchall()
    anchors = sorted({r[0] for r in rows if r[15] == r[0]})
    segments = _segments(conn, learner_id, anchors)
    items = [
        ActivityRow(
            id=r[0],
            conversation_id=r[1],
            external_conversation_id=r[2],
            source_provider=r[3],
            role=r[4],
            message_index=r[5],
            revision_index=r[6],
            captured_at=r[7],
            received_at=r[8],
            context_incomplete=r[9],
            preview=r[10],
            content_chars=r[11],
            processing_state=r[12],
            processing_attempts=r[13],
            processing_outcome=r[14],
            analyzed_in=r[15],
            segments=segments.get(r[0], []),
        )
        for r in rows
    ]
    return ActivityResponse(items=items, next_before=boundary if more else None)
