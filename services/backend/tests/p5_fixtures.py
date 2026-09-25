"""Deterministic P5 fixtures: one learner's course, skill graph and evidence, with NO model call.

Used by the P5 database tests and by scripts/acceptance_p5.py. The rows are synthetic but the
evidence is produced by the production code, so it is exactly what the pipeline would write:

* P3A rows (raw messages, their processing jobs, segments, mapping decisions, ACCEPTED
  mappings) are inserted directly - no model runs exist for them;
* attributions and EvidenceEvents go through the production evidence qualification
  (`qualify_attribution`: span grounding, strength, exposure guards) and persistence
  (`persist_attribution`: the 0005 provenance triggers re-check every link);
* the ledger and the recommendation queue go through `recompute_ledger` and
  `refresh_recommendations`, and the seeded exclusion through the real correction service.

The seeded learner (Appendix-B strengths; band 2 = difficulty multiplier 0.875):

    for_loops       4 x independent application CORRECT (+1 INCORRECT excluded by DONT_COUNT)
                    -> DEMONSTRATED (support 3.15, mean 0.806)
    while_loops     no evidence -> UNKNOWN (no ledger row)
    comprehensions  3 x AI performed it (OBSERVATION)   -> UNKNOWN, debt eligible (MODERATE, ~20)
    variables       2 x independent application INCORRECT (band 1) -> EMERGING
    indexing        2 x CORRECT + 1 x INCORRECT; prerequisite `variables` is EMERGING
                    -> DEVELOPING with a prerequisite gap
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.experience.feedback import submit_feedback
from app.experience.models import FeedbackRequest
from app.ingestion.fingerprint import content_hash
from app.intelligence.attribution.engine import AcceptedSkill, AttributionResult
from app.intelligence.contracts import AttributionItem
from app.intelligence.evidence.engine import Qualification, qualify_attribution
from app.intelligence.evidence.persist import PendingSegment, persist_attribution
from app.intelligence.mastery.ledger import rebuild_ledger
from app.intelligence.policy import IntelligencePolicy, load_policy
from app.intelligence.recommendations.service import refresh_recommendations

# key -> (canonical name, description, node kind, difficulty band, parent topic key)
GRAPH: dict[str, tuple[str, str, str, int | None, str | None]] = {
    "loops": (
        "Loops and iteration",
        "Repeating work over sequences and conditions.",
        "TOPIC",
        None,
        None,
    ),
    "data": (
        "Working with data",
        "Storing, updating and reading values and lists.",
        "TOPIC",
        None,
        None,
    ),
    "for_loops": (
        "Writing for loops over sequences",
        "Iterate over every item of a list with a for loop.",
        "SKILL",
        2,
        "loops",
    ),
    "while_loops": (
        "Writing while loops with a stop condition",
        "Repeat work until a condition becomes false.",
        "SKILL",
        2,
        "loops",
    ),
    "comprehensions": (
        "Building lists with comprehensions",
        "Create a new list from an iterable in one expression.",
        "SKILL",
        3,
        "loops",
    ),
    "variables": (
        "Assigning and updating variables",
        "Store a value in a name and update it correctly.",
        "SKILL",
        1,
        "data",
    ),
    "indexing": (
        "Indexing and slicing lists",
        "Read list items and sub-lists by position.",
        "SKILL",
        2,
        "data",
    ),
}
# (prerequisite, skill)
PREREQUISITES = (
    ("variables", "indexing"),
    ("indexing", "comprehensions"),
    ("for_loops", "comprehensions"),
)


@dataclass
class SeededTurn:
    user_message_id: UUID
    assistant_message_id: UUID
    segment_id: UUID
    mapping_id: UUID
    attribution_id: UUID | None
    evidence_id: UUID | None
    # What the evidence stage persists (kept so a deferred turn can be finished later).
    pending: PendingSegment | None = None
    item: AttributionItem | None = None
    qualification: Qualification | None = None


@dataclass
class P5Seed:
    learner_id: UUID
    course_id: UUID
    skills: dict[str, UUID]
    turns: dict[str, list[SeededTurn]] = field(default_factory=dict)
    excluded_evidence_id: UUID | None = None

    def evidence(self, key: str) -> list[UUID]:
        return [t.evidence_id for t in self.turns.get(key, []) if t.evidence_id is not None]


def _hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def seed_graph(conn: Connection, learner_id: UUID, prefix: str) -> tuple[UUID, dict[str, UUID]]:
    """A READY course owned by the learner with the GRAPH skills, topics and edges."""
    slug_prefix = _hex(prefix)[:10]
    (course_id,) = conn.execute(
        """
        insert into public.courses (owner_id, name, subject, level, graph_status, graph_version,
                                    graph_generated_at)
        values (%s, %s, 'Computer Science', 'Beginner', 'READY', 1, now()) returning id
        """,
        (learner_id, f"{prefix}Introduction to Python"),
    ).fetchone()
    conn.execute(
        "insert into public.course_memberships (course_id, user_id, role) values (%s, %s, 'STUDENT')",
        (course_id, learner_id),
    )
    ids: dict[str, UUID] = {}
    for key, (name, description, kind, band, _parent) in GRAPH.items():
        full = f"{prefix}{name}"
        (ids[key],) = conn.execute(
            """
            insert into public.skill_nodes (slug, canonical_name, normalized_name, description,
                                            node_kind, status, difficulty_band, assessment_types,
                                            source, source_course_id)
            values (%s, %s, %s, %s, %s::public.skill_node_kind, 'ACTIVE', %s, %s, 'SEED', %s)
            returning id
            """,
            (
                f"p5-{slug_prefix}-{key.replace('_', '-')}",
                full,
                full.lower().strip(),
                description,
                kind,
                band,
                ["code"] if kind == "SKILL" else [],
                course_id,
            ),
        ).fetchone()
        conn.execute(
            "insert into public.course_skills (course_id, skill_id, importance, source, graph_version) "
            "values (%s, %s, 0.5, 'SEED', 1)",
            (course_id, ids[key]),
        )
    for key, (*_, parent) in GRAPH.items():
        if parent:
            conn.execute(
                "insert into public.skill_edges (from_skill_id, to_skill_id, edge_type, source, course_id) "
                "values (%s, %s, 'PARENT', 'SEED', %s)",
                (ids[parent], ids[key], course_id),
            )
    for prerequisite, skill in PREREQUISITES:
        conn.execute(
            "insert into public.skill_edges (from_skill_id, to_skill_id, edge_type, source, course_id) "
            "values (%s, %s, 'PREREQUISITE', 'SEED', %s)",
            (ids[prerequisite], ids[skill], course_id),
        )
    return course_id, ids


def seed_turn(
    conn: Connection,
    learner_id: UUID,
    *,
    skill_id: UUID,
    user_text: str,
    assistant_text: str,
    occurred_at: datetime,
    policy: IntelligencePolicy,
    actor: str = "STUDENT",
    evidence_type: str = "INDEPENDENT_APPLICATION",
    outcome_signal: str = "CORRECT",
    student_span: str | None = None,
    ai_span: str | None = None,
    reason_code: str = "STUDENT_WROTE_CODE",
    mapping_confidence: float = 0.9,
    attribution_confidence: float = 0.95,
    attribute: bool = True,
) -> SeededTurn:
    """One captured turn mapped to one skill, attributed and qualified like the pipeline does."""
    tag = uuid4().hex[:12]
    (conversation_id,) = conn.execute(
        "insert into public.conversations (learner_id, source_provider, external_id, first_seen_at, "
        "last_seen_at) values (%s, 'chatgpt', %s, %s, %s) returning id",
        (learner_id, f"p5-conv-{tag}", occurred_at, occurred_at),
    ).fetchone()
    message_ids = []
    for index, (role, text) in enumerate((("user", user_text), ("assistant", assistant_text))):
        external = f"p5-{role[0]}-{tag}"
        (message_id,) = conn.execute(
            """
            insert into public.raw_messages (
                learner_id, conversation_id, source_provider, source_method, external_message_id,
                external_parent_message_id, message_index, role, content_text, content_format,
                content_hash, fingerprint, revision_index, occurred_at, captured_at,
                context_incomplete, client_event_uuid, client_event_id)
            values (%s, %s, 'chatgpt', 'browser_extension', %s, %s, %s, %s::public.message_role, %s,
                    'text', %s, %s, 0, %s, %s, false, %s, %s)
            returning id
            """,
            (
                learner_id,
                conversation_id,
                external,
                f"p5-u-{tag}" if role == "assistant" else None,
                index,
                role,
                text,
                content_hash(text),
                _hex(f"{learner_id}:{external}"),
                occurred_at,
                occurred_at,
                uuid4(),
                f"chatgpt:{external}:r0",
            ),
        ).fetchone()
        message_ids.append(message_id)
        conn.execute(
            """
            insert into public.processing_jobs (job_type, entity_type, entity_id, learner_id, state,
                                                attempts, outcome, completed_at)
            values ('PROCESS_RAW_MESSAGE', 'raw_message', %s, %s, 'COMPLETED', 1, %s, %s)
            """,
            (
                message_id,
                learner_id,
                "EVIDENCE_RECORDED" if role == "user" else "ALREADY_ANALYZED",
                occurred_at,
            ),
        )
    user_id, assistant_id = message_ids
    (segment_id,) = conn.execute(
        """
        insert into public.activity_segments (
            learner_id, conversation_id, anchor_message_id, user_message_id, assistant_message_id,
            source_message_ids, segment_index, segment_count, text, context, intent,
            learning_relevance, relevance_confidence, skill_bearing, skill_bearing_confidence,
            reason_code, route, route_reason, context_incomplete, prompt_version, analysis_version)
        values (%s, %s, %s, %s, %s, %s, 0, 1, %s, 'academic', %s::public.segment_intent, 'high', 0.9,
                true, 0.9, 'CODE_REQUEST', 'MAP', 'LEARNING_SKILL_BEARING', false,
                'turn-analysis/v1', 'p3a-v1')
        returning id
        """,
        (
            learner_id,
            conversation_id,
            user_id,
            user_id,
            assistant_id,
            [user_id, assistant_id],
            user_text,
            "delegate" if actor == "AI" else "practice",
        ),
    ).fetchone()
    (decision_id,) = conn.execute(
        """
        insert into public.mapping_decisions (segment_id, learner_id, outcome, retrieval_candidates,
                                              mapper_candidate_ids, prompt_versions, policy_snapshot,
                                              mapper_version)
        values (%s, %s, 'MAPPED', '[]'::jsonb, %s, %s, '{}'::jsonb, 'mapper/p3a-turn-v1')
        returning id
        """,
        (segment_id, learner_id, [skill_id], Jsonb({"turn_analysis": "turn-analysis/v1"})),
    ).fetchone()
    mapping_span = student_span or ai_span
    (mapping_id,) = conn.execute(
        """
        insert into public.skill_mappings (decision_id, segment_id, learner_id, skill_id, status,
                                           confidence, first_pass_confidence, reason_code,
                                           status_reason, evidence_span, mapper_version)
        values (%s, %s, %s, %s, 'ACCEPTED', %s, %s, 'IMPLEMENTATION', 'FIRST_PASS_ACCEPTED', %s,
                'mapper/p3a-turn-v1')
        returning id
        """,
        (
            decision_id,
            segment_id,
            learner_id,
            skill_id,
            mapping_confidence,
            mapping_confidence,
            mapping_span,
        ),
    ).fetchone()
    name, description, band = conn.execute(
        "select canonical_name, description, difficulty_band from public.skill_nodes where id = %s",
        (skill_id,),
    ).fetchone()
    accepted = AcceptedSkill(
        mapping_id=mapping_id,
        skill_id=skill_id,
        canonical_name=name,
        description=description,
        mapping_confidence=mapping_confidence,
        mapping_span=mapping_span,
        mapping_reason="IMPLEMENTATION",
        difficulty_band=band,
    )
    item = AttributionItem(
        skill_id=str(skill_id),
        actor=actor,
        confidence=attribution_confidence,
        student_evidence_span=student_span,
        ai_evidence_span=ai_span,
        evidence_type=evidence_type,
        outcome_signal=outcome_signal,
        reason_code=reason_code,
    )
    qualification = qualify_attribution(
        item,
        mapping_confidence=mapping_confidence,
        difficulty_band=band,
        learner_text=user_text,
        prior_assistant_texts=[],
        attribution_policy=policy.attribution,
        evidence_policy=policy.evidence,
    )
    segment = PendingSegment(
        segment_id=segment_id,
        decision_id=decision_id,
        learner_id=learner_id,
        segment_index=0,
        segment_count=1,
        text=user_text,
        learning_relevance="high",
        source_message_ids=(user_id, assistant_id),
        occurred_at=occurred_at,
        model_run_ids=(),
        skills=(accepted,),
    )
    turn = SeededTurn(
        user_id, assistant_id, segment_id, mapping_id, None, None, segment, item, qualification
    )
    if attribute:
        finish_turn(conn, turn, policy)
    return turn


def finish_turn(conn: Connection, turn: SeededTurn, policy: IntelligencePolicy) -> SeededTurn:
    """Write the turn's attribution and evidence, as the (resumed) evidence stage does."""
    assert turn.pending and turn.item and turn.qualification
    key = turn.item.skill_id
    persist_attribution(
        conn,
        turn.pending,
        AttributionResult(items={key: turn.item}, run=None),
        {key: turn.qualification},
        policy_snapshot={
            "attribution": policy.attribution.model_dump(mode="json"),
            "evidence": policy.evidence.model_dump(mode="json"),
        },
        processing_job_id=None,
    )
    (turn.attribution_id,) = conn.execute(
        "select id from public.attributions where mapping_id = %s", (turn.mapping_id,)
    ).fetchone()
    evidence = conn.execute(
        "select id from public.evidence_events where attribution_id = %s", (turn.attribution_id,)
    ).fetchone()
    turn.evidence_id = evidence[0] if evidence else None
    return turn


def _application(code: str, outcome: str) -> dict:
    ask = "Is it right?" if outcome == "CORRECT" else "Why does it fail?"
    return {
        "user_text": f"I wrote this myself: `{code}`. {ask}",
        "assistant_text": "Yes, that is correct."
        if outcome == "CORRECT"
        else "Not quite: check it again.",
        "student_span": code,
        "outcome_signal": outcome,
    }


def seed_p5_learner(
    pool: ConnectionPool, learner_id: UUID, prefix: str, *, now: datetime | None = None
) -> P5Seed:
    """Seed the learner described in the module docstring on a new course and skill graph."""
    with pool.connection() as conn, conn.transaction():
        course_id, skills = seed_graph(conn, learner_id, prefix)
    return seed_evidence(pool, learner_id, course_id, skills, now=now)


ROLES = ("for_loops", "while_loops", "comprehensions", "variables", "indexing")


def seed_p5_learner_on_course(
    pool: ConnectionPool,
    learner_id: UUID,
    course_id: UUID,
    roles: dict[str, UUID],
    *,
    now: datetime | None = None,
) -> P5Seed:
    """The same evidence on an EXISTING course graph, creating no registry row.

    The learner joins the course as a STUDENT (a learner-owned membership) and every other row
    is the learner's own, so deleting the learner removes all of it. Each role must be an ACTIVE
    assessable skill of the course, and `variables` must be a PREREQUISITE of `indexing`.
    """
    if set(roles) != set(ROLES):
        raise ValueError(f"roles must be exactly {ROLES}")
    with pool.connection() as conn, conn.transaction():
        found = {
            r[0]
            for r in conn.execute(
                """
                select cs.skill_id from public.course_skills cs
                  join public.skill_nodes n on n.id = cs.skill_id
                 where cs.course_id = %s and cs.active and n.status = 'ACTIVE'
                   and n.node_kind in ('SKILL', 'SUBSKILL') and cs.skill_id = any(%s)
                """,
                (course_id, list(roles.values())),
            ).fetchall()
        }
        missing = [k for k, v in roles.items() if v not in found]
        if missing:
            raise ValueError(f"not active assessable skills of course {course_id}: {missing}")
        if not conn.execute(
            "select 1 from public.skill_edges where edge_type = 'PREREQUISITE' "
            "and from_skill_id = %s and to_skill_id = %s",
            (roles["variables"], roles["indexing"]),
        ).fetchone():
            raise ValueError("`variables` must be a PREREQUISITE of `indexing` in the graph")
        conn.execute(
            "insert into public.course_memberships (course_id, user_id, role) "
            "values (%s, %s, 'STUDENT') on conflict do nothing",
            (course_id, learner_id),
        )
    return seed_evidence(pool, learner_id, course_id, dict(roles), now=now)


def seed_evidence(
    pool: ConnectionPool,
    learner_id: UUID,
    course_id: UUID,
    skills: dict[str, UUID],
    *,
    now: datetime | None = None,
) -> P5Seed:
    """The deterministic turns, ledger, queue and one seeded exclusion for the role skills."""
    now = now or datetime.now(UTC)
    with pool.connection() as conn:
        policy = load_policy(conn)
        seed = P5Seed(learner_id, course_id, skills)
        plan: list[tuple[str, dict]] = [
            ("for_loops", _application("for name in names: print(name)", "CORRECT")),
            ("for_loops", _application("for n in nums: total += n", "CORRECT")),
            ("for_loops", _application("for word in words: print(word.upper())", "CORRECT")),
            ("for_loops", _application("for row in grid: print(len(row))", "CORRECT")),
            ("for_loops", _application("for i in 10: print(i)", "INCORRECT")),
            ("variables", _application("count + 1 = count", "INCORRECT")),
            ("variables", _application("x == 5", "INCORRECT")),
            ("indexing", _application("names[0]", "CORRECT")),
            ("indexing", _application("names[-1]", "CORRECT")),
            ("indexing", _application("names[len(names)]", "INCORRECT")),
        ]
        for i in range(3):
            code = f"squares = [n * n for n in range({i + 3})]"
            plan.append(
                (
                    "comprehensions",
                    {
                        "user_text": f"Write the list comprehension for squares up to {i + 3} for me.",
                        "assistant_text": f"Here it is: `{code}`",
                        "actor": "AI",
                        "evidence_type": "OBSERVATION",
                        "outcome_signal": "NOT_APPLICABLE",
                        "ai_span": code,
                        "reason_code": "AI_WROTE_SOLUTION",
                    },
                )
            )
        for index, (key, fields) in enumerate(plan):
            occurred = now - timedelta(seconds=len(plan) - index)
            with conn.transaction():
                turn = seed_turn(
                    conn,
                    learner_id,
                    skill_id=skills[key],
                    occurred_at=occurred,
                    policy=policy,
                    **fields,
                )
            seed.turns.setdefault(key, []).append(turn)
        # Only skills with evidence get a ledger row: while_loops stays row-less (UNKNOWN).
        rebuild_ledger(conn, learner_id, policy=policy, as_of=now)
        refresh_recommendations(conn, learner_id, policy=policy)
        conn.commit()

        # The learner says the failed for-loop attempt was not theirs: one excluded event.
        wrong_attempt = seed.turns["for_loops"][-1].evidence_id
        submit_feedback(
            conn,
            learner_id,
            FeedbackRequest(
                action="DONT_COUNT",
                target_type="EVIDENCE_EVENT",
                target_id=wrong_attempt,
                note="A classmate typed this attempt.",
            ),
            f"p5-seed-{learner_id.hex[:12]}",
            policy=policy,
        )
        conn.commit()
        seed.excluded_evidence_id = wrong_attempt
    return seed
