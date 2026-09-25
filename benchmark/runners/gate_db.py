"""Database side of the critical gate (benchmark/runners/critical_gate.py): the frozen fixture
course graphs, per-case benchmark learners, and turn cases run through the PRODUCTION pipeline.

    seed fixtures (deterministic uuid5 ids) -> embed their skills (the run's gateway)
    per turn case: a fresh learner, a STUDENT membership in the case's course, the turn(s)
        ingested through the ingestion service -> process_raw_message_job (P3A mapping, P3B
        attribution / evidence, P4 ledger, P5 recommendations) -> outcomes read back
    replay safety: every job is run a second time; it must make 0 provider requests and write
        no row
    cleanup: learners (Auth cascade), candidates the run created, courses, registry rows

Only ever a LOCAL / CI database: the fixture registry would otherwise enter the retrieval of real
learners (critical_gate.py refuses a non-local DATABASE_URL).
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool

from app.experience.activity import list_activity
from app.ingestion.models import EventBatchClientInfo, RawActivityEnvelope
from app.ingestion.service import ingest_batch
from app.intelligence.mastery.ledger import read_ledger
from app.intelligence.policy import IntelligencePolicy
from app.intelligence.processing.pipeline import process_raw_message_job
from app.intelligence.skill_graph.canonical import skill_key
from app.intelligence.skill_graph.embedding import embed_skills
from app.intelligence.skill_graph.registry import add_alias, add_edge
from app.jobs.queue import ClaimedJob
from app.model_gateway import ModelGateway, RunContext
from tests.conftest import make_envelope

NAMESPACE = "skillmirror-critical-gate:"
CLIENT = EventBatchClientInfo(extension_version="0.3.0", adapter_version="chatgpt-2")
# Turn tables whose rows a replay must never add to.
TURN_TABLES = (
    "activity_segments",
    "mapping_decisions",
    "skill_mappings",
    "attributions",
    "evidence_events",
)


def node_id(key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, NAMESPACE + key)


@dataclass
class Fixtures:
    courses: dict[str, uuid.UUID]
    course_names: dict[str, str]
    skills: dict[str, uuid.UUID]  # assessable skill key -> id
    topics: dict[str, uuid.UUID]
    names: dict[uuid.UUID, str]  # id -> canonical name
    keys: dict[uuid.UUID, str]  # id -> key
    spec: dict[str, dict[str, Any]]  # skill key -> fixture entry (+ course key)
    owner: uuid.UUID

    def key_of(self, skill_id: uuid.UUID | str) -> str | None:
        return self.keys.get(uuid.UUID(str(skill_id)))


def load_fixture_spec(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fixtures_from_spec(spec: dict[str, Any]) -> Fixtures:
    courses, names, skills, topics, keys, entries = {}, {}, {}, {}, {}, {}
    course_names = {}
    for course in spec["courses"]:
        courses[course["key"]] = node_id("course:" + course["key"])
        course_names[course["key"]] = course["name"]
        for topic in course["topics"]:
            topics[topic["key"]] = node_id(topic["key"])
            names[topics[topic["key"]]] = topic["name"]
            keys[topics[topic["key"]]] = topic["key"]
        for skill in course["skills"]:
            skills[skill["key"]] = node_id(skill["key"])
            names[skills[skill["key"]]] = skill["name"]
            keys[skills[skill["key"]]] = skill["key"]
            entries[skill["key"]] = {**skill, "course": course["key"]}
    if len(keys) != len(skills) + len(topics):
        # Topic and skill keys share one id space (uuid5 of the key).
        raise ValueError("fixture keys must be unique across topics and skills")
    return Fixtures(courses, course_names, skills, topics, names, keys, entries, node_id("owner"))


def foreign_registry_rows(conn: Connection, fixtures: Fixtures) -> int:
    """ACTIVE registry nodes that are not fixture nodes (they would enter retrieval)."""
    ids = list(fixtures.skills.values()) + list(fixtures.topics.values())
    return conn.execute(
        "select count(*) from public.skill_nodes where status = 'ACTIVE' and not (id = any(%s))",
        (ids,),
    ).fetchone()[0]


def seed_fixtures(
    pool: ConnectionPool, gateway: ModelGateway, spec: dict[str, Any], fixtures: Fixtures
) -> int:
    """Insert the frozen graphs (idempotent) and embed their skills. Returns skills embedded."""
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            "insert into auth.users (id, email) values (%s, %s) on conflict (id) do nothing",
            (fixtures.owner, "critical-gate-owner@benchmark.invalid"),
        )
        for course in spec["courses"]:
            course_id = fixtures.courses[course["key"]]
            conn.execute(
                """
                insert into public.courses (id, owner_id, name, subject, level, graph_status,
                                            graph_version, graph_generated_at)
                values (%s, %s, %s, %s, %s, 'READY', 1, now()) on conflict (id) do nothing
                """,
                (course_id, fixtures.owner, course["name"], course["subject"], course["level"]),
            )
            nodes = [(t, "TOPIC") for t in course["topics"]] + [
                (s, s.get("kind", "SKILL")) for s in course["skills"]
            ]
            for node, kind in nodes:
                conn.execute(
                    """
                    insert into public.skill_nodes (id, slug, canonical_name, normalized_name,
                        description, node_kind, status, difficulty_band, assessment_types, source)
                    values (%s, %s, %s, %s, %s, %s::public.skill_node_kind, 'ACTIVE', %s, %s,
                            'SEED')
                    on conflict (id) do nothing
                    """,
                    (
                        node_id(node["key"]),
                        "cg-" + node["key"],
                        node["name"],
                        skill_key(node["name"]),
                        node["description"],
                        kind,
                        node.get("band"),
                        node.get("assessment_types", []),
                    ),
                )
                conn.execute(
                    """
                    insert into public.course_skills (course_id, skill_id, importance, source,
                                                      graph_version)
                    values (%s, %s, %s, 'SEED', 1) on conflict do nothing
                    """,
                    (course_id, node_id(node["key"]), node.get("importance", 0.5)),
                )
            for skill in course["skills"]:
                for alias in skill.get("aliases", []):
                    add_alias(conn, node_id(skill["key"]), alias, source="SEED")
                add_edge(
                    conn,
                    node_id(skill.get("parent") or skill["topic"]),
                    node_id(skill["key"]),
                    "PARENT",
                    course_id=course_id,
                    source="SEED",
                )
                for prerequisite in skill.get("prerequisites", []):
                    add_edge(
                        conn,
                        node_id(prerequisite),
                        node_id(skill["key"]),
                        "PREREQUISITE",
                        course_id=course_id,
                        source="SEED",
                    )
    with pool.connection() as conn:
        report = embed_skills(
            conn, gateway, list(fixtures.skills.values()), RunContext(trace_id="benchmark:seed")
        )
    return report.embedded


def remove_fixtures(pool: ConnectionPool, fixtures: Fixtures, learners: list[uuid.UUID]) -> None:
    ids = list(fixtures.skills.values()) + list(fixtures.topics.values())
    with pool.connection() as conn, conn.transaction():
        candidates = [
            r[0]
            for r in conn.execute(
                "select distinct new_skill_candidate_id from public.mapping_decisions "
                "where learner_id = any(%s) and new_skill_candidate_id is not null",
                (learners,),
            ).fetchall()
        ]
        conn.execute("delete from auth.users where id = any(%s)", (learners,))
        conn.execute(
            "delete from public.skill_candidates "
            "where id = any(%s) or parent_candidate_id = any(%s)",
            (candidates, ids),
        )
        conn.execute(
            "delete from public.courses where id = any(%s)", (list(fixtures.courses.values()),)
        )
        conn.execute(
            "delete from public.skill_edges where from_skill_id = any(%s) or to_skill_id = any(%s)",
            (ids, ids),
        )
        conn.execute("delete from public.skill_embeddings where skill_id = any(%s)", (ids,))
        conn.execute("delete from public.skill_aliases where skill_id = any(%s)", (ids,))
        conn.execute("delete from public.skill_nodes where id = any(%s)", (ids,))
        conn.execute("delete from auth.users where id = %s", (fixtures.owner,))


# --- turn cases ------------------------------------------------------------------------------


def new_learner(pool: ConnectionPool, case_id: str, course_ids: list[uuid.UUID]) -> uuid.UUID:
    learner = uuid.uuid4()
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            "insert into auth.users (id, email) values (%s, %s)",
            (learner, f"cg-{case_id.lower()}-{learner.hex[:8]}@benchmark.invalid"),
        )
        for course_id in course_ids:
            conn.execute(
                "insert into public.course_memberships (course_id, user_id, role) "
                "values (%s, %s, 'STUDENT')",
                (course_id, learner),
            )
    return learner


def ingest_turn(
    pool: ConnectionPool,
    learner: uuid.UUID,
    turn: dict[str, Any],
    *,
    conversation: str,
    index: int,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """The turn as the extension sends it (chatgpt-2), captured `minutes_ago` minutes ago."""
    when = (datetime.now(UTC) - timedelta(minutes=turn.get("minutes_ago", 0))).isoformat()
    user_ext = f"u-{uuid.uuid4().hex[:12]}"
    user = make_envelope(
        learner,
        content_text=turn["user"],
        external_message_id=user_ext,
        external_conversation_id=conversation,
        message_index=index,
        captured_at=when,
    )
    if turn.get("attachment"):
        user["attachment_metadata"] = [
            {
                "kind": "image",
                "filename": "screenshot.png",
                "mime_type": "image/png",
                "content_available": False,
            }
        ]
    if turn.get("attachment") or turn.get("context_incomplete"):
        user["context_incomplete"] = True
    events = [user]
    if turn.get("assistant") is not None:
        events.append(
            make_envelope(
                learner,
                content_text=turn["assistant"],
                external_message_id=f"a-{uuid.uuid4().hex[:12]}",
                external_parent_message_id=user_ext,
                external_conversation_id=conversation,
                message_index=index + 1,
                role="assistant",
                captured_at=when,
            )
        )
    with pool.connection() as conn:
        stored = ingest_batch(
            conn, learner, [RawActivityEnvelope.model_validate(e) for e in events], CLIENT
        )
    return stored[0].raw_message_id, (stored[1].raw_message_id if len(stored) > 1 else None)


def claimed_job(pool: ConnectionPool, entity_id: uuid.UUID) -> ClaimedJob:
    with pool.connection() as conn:
        row = conn.execute(
            "select id, job_type, entity_type, entity_id, learner_id, attempts, max_attempts "
            "from public.processing_jobs where entity_id = %s and job_type = 'PROCESS_RAW_MESSAGE'",
            (entity_id,),
        ).fetchone()
    return ClaimedJob(*row)


def row_counts(conn: Connection, learner: uuid.UUID) -> dict[str, int]:
    return {
        t: conn.execute(
            f"select count(*) from public.{t} where learner_id = %s",  # noqa: S608 - constant
            (learner,),
        ).fetchone()[0]
        for t in TURN_TABLES
    }


@dataclass
class TurnOutcome:
    segments: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    mapped: list[str] = field(default_factory=list)  # ACCEPTED mapping skill keys (or "?id")
    ranked: list[list[str]] = field(default_factory=list)  # mapper candidates per decision
    candidates: list[str] = field(default_factory=list)  # new_skill_candidate names
    attributions: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    ledger: dict[str, dict[str, Any]] = field(default_factory=dict)
    recommendations: dict[str, str] = field(default_factory=dict)
    invented: list[str] = field(default_factory=list)
    # Activity chips whose actor / qualification differ from their recorded evidence.
    actor_mismatches: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)  # job outcomes (first run)
    replay_outcomes: list[str] = field(default_factory=list)
    replay_requests: int = 0
    replay_new_rows: dict[str, int] = field(default_factory=dict)
    adjudicated: bool = False


def run_turn_case(
    pool: ConnectionPool,
    gateway: ModelGateway,
    provider: Any,
    case: dict[str, Any],
    fixtures: Fixtures,
    policy: IntelligencePolicy,
    *,
    use_script: str | None,
    learners: list[uuid.UUID],
) -> TurnOutcome:
    """Run every turn of the case in order (one conversation), then replay every job."""
    course_ids = [fixtures.courses[c] for c in case.get("courses", [case["course"]])]
    learner = new_learner(pool, case["id"], course_ids)
    learners.append(learner)
    conversation = f"cg-{case['id'].lower()}-{uuid.uuid4().hex[:8]}"
    out = TurnOutcome()
    jobs = []
    for index, turn in enumerate(case["turns"]):
        if use_script:
            provider.set_script(turn.get(use_script) or turn.get("ideal") or {})
        # Earlier turns were captured earlier (5 minutes apart unless the case says otherwise).
        turn = {"minutes_ago": 5 * (len(case["turns"]) - 1 - index), **turn}
        user_id, assistant_id = ingest_turn(
            pool, learner, turn, conversation=conversation, index=index * 2
        )
        job = claimed_job(pool, assistant_id or user_id)
        jobs.append(job)
        out.outcomes.append(process_raw_message_job(pool, gateway, job).outcome)

    with pool.connection() as conn:
        before = row_counts(conn, learner)
    requests = provider.requests
    for job in jobs:
        out.replay_outcomes.append(process_raw_message_job(pool, gateway, job).outcome)
    out.replay_requests = provider.requests - requests
    with pool.connection() as conn:
        after = row_counts(conn, learner)
        out.replay_new_rows = {
            t: after[t] - before[t] for t in TURN_TABLES if after[t] != before[t]
        }
        read_outcomes(conn, learner, fixtures, policy, out)
    return out


def read_outcomes(
    conn: Connection,
    learner: uuid.UUID,
    fixtures: Fixtures,
    policy: IntelligencePolicy,
    out: TurnOutcome,
) -> None:
    def key(skill_id) -> str:
        found = fixtures.key_of(skill_id)
        if found is None:
            out.invented.append(str(skill_id))
            return f"?{skill_id}"
        return found

    for route, reason, text in conn.execute(
        "select route::text, reason_code, text from public.activity_segments "
        "where learner_id = %s order by created_at, anchor_message_id, segment_index",
        (learner,),
    ).fetchall():
        out.segments.append({"route": route, "reason": reason, "text": text[:80]})
    for outcome, reason, candidate, ranked, adjudicated in conn.execute(
        """
        select d.outcome::text, d.abstain_reason, c.canonical_name, d.mapper_candidate_ids,
               d.adjudication_model_run_id is not null
          from public.mapping_decisions d
          left join public.skill_candidates c on c.id = d.new_skill_candidate_id
         where d.learner_id = %s order by d.created_at
        """,
        (learner,),
    ).fetchall():
        out.decisions.append({"outcome": outcome, "reason": reason})
        out.ranked.append([fixtures.key_of(i) or f"?{i}" for i in ranked])
        out.adjudicated = out.adjudicated or adjudicated
        if candidate:
            out.candidates.append(candidate)
    for skill_id, status in conn.execute(
        "select skill_id, status::text from public.skill_mappings where learner_id = %s "
        "order by created_at, confidence desc",
        (learner,),
    ).fetchall():
        if status == "ACCEPTED":
            out.mapped.append(key(skill_id))
    for skill_id, status, actor, decision in conn.execute(
        "select skill_id, status::text, actor::text, evidence_decision from public.attributions "
        "where learner_id = %s order by created_at",
        (learner,),
    ).fetchall():
        out.attributions.append(
            {"skill": key(skill_id), "status": status, "actor": actor, "decision": decision}
        )
    for evidence_id, skill_id, actor, etype, signal, strength, excluded, reason in conn.execute(
        "select id, skill_id, actor::text, evidence_type::text, outcome_signal::text, strength, "
        "excluded, qualification_reason from public.evidence_events where learner_id = %s "
        "order by occurred_at, id",
        (learner,),
    ).fetchall():
        out.evidence.append(
            {
                "id": str(evidence_id),
                "skill": key(skill_id),
                "actor": actor,
                "type": etype,
                "outcome": signal,
                "strength": float(strength),
                "excluded": excluded,
                "reason": reason,
            }
        )
    # The learner-facing activity feed must show the recorded evidence's actor and reason
    # (ADR 0008 §24): the attribution / evidence consistency gate.
    by_id = {e["id"]: e for e in out.evidence}
    for row in list_activity(conn, learner, limit=200).items:
        for segment in row.segments:
            for chip in segment.mappings:
                if chip.evidence_id is None:
                    continue
                recorded = by_id.get(str(chip.evidence_id))
                if (
                    recorded is None
                    or chip.actor != recorded["actor"]
                    or chip.qualification_reason != recorded["reason"]
                    or chip.evidence_type != recorded["type"]
                ):
                    out.actor_mismatches.append(
                        f"{key(chip.skill_id)}: chip {chip.actor}/{chip.qualification_reason} "
                        f"vs evidence {recorded and (recorded['actor'], recorded['reason'])}"
                    )
    ledger = read_ledger(conn, learner, policy=policy)
    for entry in ledger.skills:
        k = fixtures.key_of(entry.skill_id)
        if k is None:
            continue
        out.ledger[k] = {
            "state": entry.mastery_state,
            "mean": entry.mastery_mean,
            "gain": (entry.alpha or 0) > policy.mastery.prior_alpha,
            "debt_eligible": entry.debt_eligible,
            "debt_actionable": entry.debt_actionable,
            "debt_score": entry.debt_score,
            "verified_evidence": False,
        }
    for skill_id, rtype in conn.execute(
        "select skill_id, type::text from public.recommendations "
        "where learner_id = %s and state = 'ACTIVE'",
        (learner,),
    ).fetchall():
        k = fixtures.key_of(skill_id)
        if k is not None:
            out.recommendations[k] = rtype
