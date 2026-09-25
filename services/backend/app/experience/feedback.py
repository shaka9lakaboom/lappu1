"""The correction loop (architecture §5.2, §7.2, §16 "Feedback"; ADR 0006). No model call.

    DONT_COUNT / WRONG_SKILL:
        feedback -> one-way exclusion of the derived evidence in scope
                 -> ledger recompute of the affected skills -> recommendation refresh
    EVALUATION:
        feedback only (evidence never changes)

Everything happens in ONE transaction, so a correction is applied completely or not at all,
and the response already carries the recomputed ledger rows and recommendations.

* Scope: a correction of an evidence event or a skill mapping covers that mapping (the
  skill as mapped from that task unit); DONT_COUNT of an activity segment covers the whole
  task unit. WRONG_SKILL does not invent a replacement skill (a remap stays a later workflow).
* Nothing is deleted. raw_messages, activity_segments, mapping_decisions, skill_mappings,
  attributions and evidence_events keep their rows; the exclusion is the one-way
  evidence_events.excluded of migration 0005, and the mapping's correction is the feedback
  row itself. Evidence written later from a corrected scope is born excluded (0007 trigger).
* Only evidence from captured activity (AI_ACTIVITY) can be corrected; verification,
  assessment and teacher evidence cannot be excluded by the learner.
* Idempotency: the Idempotency-Key is unique per learner; a replay returns the original
  feedback, and the same key with a different request is a conflict. Repeating a correction
  of the same target (with a new key) returns the existing one.
* Concurrency: the learner's feedback is serialized, and the segment's attribution lock
  (the one the pipeline takes before writing evidence) orders a correction against a job
  writing evidence for the same task unit.
"""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from app.experience.models import (
    FeedbackRequest,
    FeedbackResponse,
    FeedbackSummary,
    Recommendation,
)
from app.intelligence.contracts import LedgerEntry
from app.intelligence.mastery.ledger import read_ledger, recompute_ledger
from app.intelligence.policy import IntelligencePolicy
from app.intelligence.recommendations.service import (
    list_recommendations,
    refresh_recommendations,
)

CORRECTIONS = ("WRONG_SKILL", "DONT_COUNT")
EXCLUSION_REASONS = {"WRONG_SKILL": "LEARNER_WRONG_SKILL", "DONT_COUNT": "LEARNER_DONT_COUNT"}

FEEDBACK_COLUMNS = (
    "f.id, f.action::text, f.target_type::text, f.target_id, f.skill_id, f.mapping_id, "
    "f.segment_id, f.verdict::text, f.note, f.excluded_evidence_ids, f.recomputed_skill_ids, "
    "f.created_at"
)


def feedback_summary(row: tuple) -> FeedbackSummary:
    return FeedbackSummary(
        id=row[0],
        action=row[1],
        target_type=row[2],
        target_id=row[3],
        skill_id=row[4],
        mapping_id=row[5],
        segment_id=row[6],
        verdict=row[7],
        note=row[8],
        excluded_evidence_ids=tuple(row[9]),
        recomputed_skill_ids=tuple(row[10]),
        created_at=row[11],
    )


def correction_for(
    corrections: list[FeedbackSummary],
    *,
    evidence_id: UUID | None = None,
    mapping_id: UUID | None = None,
    segment_id: UUID | None = None,
) -> FeedbackSummary | None:
    """The learner's correction covering this evidence / mapping (WRONG_SKILL first)."""
    matches = [
        f
        for f in corrections
        if f.action in CORRECTIONS
        and (
            (evidence_id is not None and evidence_id in f.excluded_evidence_ids)
            or (f.mapping_id is not None and f.mapping_id == mapping_id)
            or (f.mapping_id is None and f.segment_id is not None and f.segment_id == segment_id)
        )
    ]
    matches.sort(key=lambda f: (f.action != "WRONG_SKILL", f.created_at))
    return matches[0] if matches else None


class FeedbackTargetNotFoundError(LookupError):
    pass


class NotCorrectableError(ValueError):
    pass


class IdempotencyConflictError(ValueError):
    pass


@dataclass(frozen=True)
class _Target:
    skill_id: UUID | None
    mapping_id: UUID | None
    segment_id: UUID | None


def request_hash(request: FeedbackRequest) -> str:
    canonical = json.dumps(
        {
            "action": request.action,
            "target_type": request.target_type,
            "target_id": str(request.target_id),
            "verdict": request.verdict,
            "note": request.note,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _skill_in_scope(conn: Connection, learner_id: UUID, skill_id: UUID) -> bool:
    return (
        conn.execute(
            """
            select 1 from public.skill_nodes n
             where n.id = %(skill)s and (
                exists (select 1 from public.course_skills cs
                          join public.course_memberships m
                            on m.course_id = cs.course_id and m.user_id = %(learner)s
                         where cs.skill_id = n.id and cs.active)
                or exists (select 1 from public.evidence_events e
                            where e.learner_id = %(learner)s and e.skill_id = n.id)
                or exists (select 1 from public.skill_ledger l
                            where l.learner_id = %(learner)s and l.skill_id = n.id))
            """,
            {"skill": skill_id, "learner": learner_id},
        ).fetchone()
        is not None
    )


def _resolve_target(conn: Connection, learner_id: UUID, request: FeedbackRequest) -> _Target:
    """The target, scoped to the learner (never another learner's row)."""
    kind, target = request.target_type, request.target_id
    if kind == "EVIDENCE_EVENT":
        row = conn.execute(
            "select skill_id, mapping_id, segment_id, source_type::text "
            "from public.evidence_events where id = %s and learner_id = %s",
            (target, learner_id),
        ).fetchone()
        if row is None:
            raise FeedbackTargetNotFoundError(target)
        if request.action in CORRECTIONS and row[3] != "AI_ACTIVITY":
            raise NotCorrectableError("Only evidence from captured activity can be corrected.")
        return _Target(row[0], row[1], row[2])
    if kind == "SKILL_MAPPING":
        row = conn.execute(
            "select skill_id, segment_id from public.skill_mappings "
            "where id = %s and learner_id = %s",
            (target, learner_id),
        ).fetchone()
        if row is None:
            raise FeedbackTargetNotFoundError(target)
        return _Target(row[0], target, row[1])
    if kind == "ACTIVITY_SEGMENT":
        row = conn.execute(
            "select id from public.activity_segments where id = %s and learner_id = %s",
            (target, learner_id),
        ).fetchone()
        if row is None:
            raise FeedbackTargetNotFoundError(target)
        return _Target(None, None, target)
    if kind == "SKILL":
        if not _skill_in_scope(conn, learner_id, target):
            raise FeedbackTargetNotFoundError(target)
        return _Target(target, None, None)
    row = conn.execute(  # RECOMMENDATION
        "select skill_id from public.recommendations where id = %s and learner_id = %s",
        (target, learner_id),
    ).fetchone()
    if row is None:
        raise FeedbackTargetNotFoundError(target)
    return _Target(row[0], None, None)


def _scope_filter() -> str:
    # A mapping scope when the target names a mapping, else the whole task unit.
    return (
        "learner_id = %(learner)s and source_type = 'AI_ACTIVITY' "
        "and ((%(mapping)s::uuid is not null and mapping_id = %(mapping)s::uuid) "
        "or (%(mapping)s::uuid is null and segment_id = %(segment)s::uuid))"
    )


def _load(conn: Connection, where: str, params: tuple) -> FeedbackSummary | None:
    row = conn.execute(
        f"select {FEEDBACK_COLUMNS} from public.feedback f where {where}",  # noqa: S608 - fixed text
        params,
    ).fetchone()
    return feedback_summary(row) if row else None


def _response(
    conn: Connection,
    learner_id: UUID,
    feedback: FeedbackSummary,
    *,
    created: bool,
    correlation_id: str,
    policy: IntelligencePolicy,
) -> FeedbackResponse:
    skills = [
        s
        for s in dict.fromkeys([*feedback.recomputed_skill_ids, feedback.skill_id])
        if s is not None
    ]
    ledger: list[LedgerEntry] = []
    recommendations: list[Recommendation] = []
    if feedback.recomputed_skill_ids:
        ledger = read_ledger(
            conn, learner_id, policy=policy, skill_ids=list(feedback.recomputed_skill_ids)
        ).skills
    if skills:
        recommendations = list_recommendations(
            conn, learner_id, skill_ids=skills, include_no_action=True
        )
    return FeedbackResponse(
        correlation_id=correlation_id,
        created=created,
        feedback=feedback,
        ledger=ledger,
        recommendations=recommendations,
    )


def submit_feedback(
    conn: Connection,
    learner_id: UUID,
    request: FeedbackRequest,
    idempotency_key: str,
    *,
    policy: IntelligencePolicy,
) -> FeedbackResponse:
    digest = request_hash(request)
    with conn.transaction():
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"feedback:{learner_id}",)
        )
        existing = conn.execute(
            f"select {FEEDBACK_COLUMNS}, f.request_hash from public.feedback f "  # noqa: S608
            "where f.user_id = %s and f.client_request_id = %s",
            (learner_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if existing[-1] != digest:
                raise IdempotencyConflictError(idempotency_key)
            return _response(
                conn,
                learner_id,
                feedback_summary(existing[:-1]),
                created=False,
                correlation_id=idempotency_key,
                policy=policy,
            )
        if request.action in CORRECTIONS:
            repeated = _load(
                conn,
                "f.user_id = %s and f.action = %s::public.feedback_action "
                "and f.target_type = %s::public.feedback_target_type and f.target_id = %s",
                (learner_id, request.action, request.target_type, request.target_id),
            )
            if repeated is not None:
                return _response(
                    conn,
                    learner_id,
                    repeated,
                    created=False,
                    correlation_id=idempotency_key,
                    policy=policy,
                )

        target = _resolve_target(conn, learner_id, request)
        excluded: list[UUID] = []
        recomputed: list[UUID] = []
        if request.action in CORRECTIONS:
            conn.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"attribution:{target.segment_id}",),
            )
            scope = {
                "learner": learner_id,
                "mapping": target.mapping_id,
                "segment": target.segment_id,
                "reason": EXCLUSION_REASONS[request.action],
            }
            excluded = [
                r[0]
                for r in conn.execute(
                    "update public.evidence_events "  # noqa: S608 - fixed text
                    "set excluded = true, exclusion_reason = %(reason)s, excluded_at = now() "
                    f"where not excluded and {_scope_filter()} returning id",
                    scope,
                ).fetchall()
            ]
            recomputed = [
                r[0]
                for r in conn.execute(
                    f"select distinct skill_id from public.evidence_events where {_scope_filter()} "  # noqa: S608
                    "order by skill_id",
                    scope,
                ).fetchall()
            ]
            if recomputed:
                recompute_ledger(conn, learner_id, recomputed, policy=policy)

        (feedback_id,) = conn.execute(
            """
            insert into public.feedback (
                user_id, target_type, target_id, action, verdict, note, excluded_evidence_ids,
                recomputed_skill_ids, client_request_id, request_hash
            ) values (%s, %s::public.feedback_target_type, %s, %s::public.feedback_action,
                      %s::public.feedback_verdict, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                learner_id,
                request.target_type,
                request.target_id,
                request.action,
                request.verdict,
                request.note,
                excluded,
                recomputed,
                idempotency_key,
                digest,
            ),
        ).fetchone()
        if request.action in CORRECTIONS:
            refresh_recommendations(conn, learner_id, policy=policy)
        feedback = _load(conn, "f.id = %s", (feedback_id,))
        if feedback is None:  # pragma: no cover - inserted in this transaction
            raise RuntimeError(f"feedback {feedback_id} vanished after insert")
        return _response(
            conn,
            learner_id,
            feedback,
            created=True,
            correlation_id=idempotency_key,
            policy=policy,
        )


def list_feedback(
    conn: Connection,
    learner_id: UUID,
    *,
    skill_id: UUID | None = None,
    segment_ids: list[UUID] | None = None,
) -> list[FeedbackSummary]:
    """The learner's feedback about a skill and/or some task units, oldest first."""
    rows = conn.execute(
        f"select {FEEDBACK_COLUMNS} from public.feedback f "  # noqa: S608 - fixed text
        "where f.user_id = %(learner)s and ("
        "(%(skill)s::uuid is not null and f.skill_id = %(skill)s::uuid) "
        "or (%(segments)s::uuid[] is not null and f.segment_id = any(%(segments)s::uuid[])))"
        " order by f.created_at, f.id",
        {"learner": learner_id, "skill": skill_id, "segments": segment_ids},
    ).fetchall()
    return [feedback_summary(r) for r in rows]
