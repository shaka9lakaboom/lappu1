"""Deterministic P6 fixtures: verification sessions, challenges and graded results, with NO model call.

Used by the P6 database tests and by scripts/acceptance_p6*.py. Since migration 0008, VERIFICATION
evidence must name a real verification result, so every fixture builds the full chain
(session -> item -> result) through the same state transitions the backend uses.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.intelligence.mastery.ledger import rebuild_ledger
from app.intelligence.policy import IntelligencePolicy, load_policy
from app.intelligence.recommendations.service import refresh_recommendations
from tests.p5_fixtures import _application, seed_turn


def fake_model_run(conn: Connection, *, task_type: str, prompt_version: str) -> UUID:
    """A model_runs row standing for a scripted (fake) provider call - never a real request."""
    (run_id,) = conn.execute(
        """
        insert into public.model_runs (trace_id, task_type, provider, model, prompt_version,
                                       input_hash, latency_ms, status)
        values ('fixture:p6', %s, 'fake', 'fixture-model', %s, %s, 1, 'SUCCEEDED')
        returning id
        """,
        (task_type, prompt_version, hashlib.sha256(uuid4().bytes).hexdigest()),
    ).fetchone()
    return run_id


def graded_verification(
    conn: Connection,
    learner_id: UUID,
    skill_id: UUID,
    *,
    outcome_signal: str = "CORRECT",
    grading_confidence: float = 1.0,
    difficulty: float = 0.5,
) -> UUID:
    """An EVALUATED verification of the skill; returns the verification_results id.

    grading_confidence 1.0 is a deterministic MCQ grade; anything lower an AI rubric grade."""
    generation = fake_model_run(
        conn, task_type="VERIFICATION_GENERATION", prompt_version="verification-generation/v1"
    )
    (session_id,) = conn.execute(
        """
        insert into public.verification_sessions (
            learner_id, skill_id, trigger_type, reason_code, planned_difficulty, difficulty_min,
            difficulty_max, plan_day, plan_timezone, planner_version, planning_inputs)
        values (%s, %s, 'VERIFY', 'REPEATED_DELEGATION_UNVERIFIED', %s, %s, %s, current_date,
                'UTC', 'verification-planner/fixture-v1', '{}')
        returning id
        """,
        (
            learner_id,
            skill_id,
            difficulty,
            max(0.0, difficulty - 0.15),
            min(1.0, difficulty + 0.15),
        ),
    ).fetchone()
    deterministic = grading_confidence >= 1.0
    (item_id,) = conn.execute(
        """
        insert into public.verification_items (
            session_id, learner_id, skill_id, assessment_type, grader_type, prompt, choices,
            expected_answer, rubric, difficulty, transfer_distance, estimated_minutes,
            generator_version, prompt_version, generation_model_run_id, generation_attempt,
            prompt_fingerprint, validator_version, validation)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'medium', 3,
                'verification-generator/fixture-v1', 'verification-generation/v1', %s, 1, %s,
                'verification-validator/fixture-v1', '{}')
        returning id
        """,
        (
            session_id,
            learner_id,
            skill_id,
            "mcq" if deterministic else "short_response",
            "MCQ_EXACT" if deterministic else "RUBRIC_AI",
            "A fixture challenge that checks the target skill in a fresh context.",
            Jsonb(
                [{"key": "A", "text": "First option"}, {"key": "B", "text": "Second option"}]
                if deterministic
                else []
            ),
            "B" if deterministic else "A concise model answer.",
            Jsonb([] if deterministic else [{"criterion": "Applies the skill", "points": 1}]),
            difficulty,
            generation,
            hashlib.sha256(uuid4().bytes).hexdigest(),
        ),
    ).fetchone()
    response = {"selected": ["B"]} if deterministic else {"answer": "fixture answer"}
    for update in (
        "set state = 'READY', ready_at = now()",
        "set state = 'IN_PROGRESS', started_at = now()",
    ):
        conn.execute(f"update public.verification_sessions {update} where id = %s", (session_id,))  # noqa: S608 - fixed text
    conn.execute(
        """
        update public.verification_sessions
           set state = 'SUBMITTED', submitted_at = now(), submitted_response = %s,
               submission_idempotency_key = %s, submission_request_hash = %s
         where id = %s
        """,
        (Jsonb(response), f"fixture-{session_id}", "c" * 64, session_id),
    )
    score, passed, outcome = {
        "CORRECT": (1.0, True, 1.0),
        "PARTIAL": (0.5, False, 0.5),
        "INCORRECT": (0.0, False, 0.0),
    }[outcome_signal]
    evaluator_run = (
        None
        if deterministic
        else fake_model_run(
            conn, task_type="VERIFICATION_EVALUATION", prompt_version="verification-evaluation/v1"
        )
    )
    (result_id,) = conn.execute(
        """
        insert into public.verification_results (
            item_id, session_id, learner_id, skill_id, response, score, pass, outcome_signal,
            outcome, evaluation, feedback, grading_confidence, evaluator_type, evaluator_version,
            evaluator_model_run_id, evaluator_prompt_version, policy_snapshot)
        values (%s, %s, %s, %s, %s, %s, %s, %s::public.outcome_signal, %s, '{}', 'Graded.', %s,
                %s::public.verification_evaluator_type, %s, %s, %s, '{}')
        returning id
        """,
        (
            item_id,
            session_id,
            learner_id,
            skill_id,
            Jsonb(response),
            score,
            passed,
            outcome_signal,
            outcome,
            grading_confidence,
            "DETERMINISTIC" if deterministic else "AI_RUBRIC",
            "grader/mcq-exact-v1" if deterministic else "evaluator/rubric-ai-v1",
            evaluator_run,
            None if deterministic else "verification-evaluation/v1",
        ),
    ).fetchone()
    conn.execute(
        "update public.verification_sessions set state = 'EVALUATED', evaluated_at = now() "
        "where id = %s",
        (session_id,),
    )
    return result_id


# --- The deterministic P6 learner ------------------------------------------------------------
#
# One course graph with a high-importance, hard skill the AI repeatedly performed for the
# learner (actionable debt -> an ACTIVE VERIFY recommendation) and just enough prior independent
# evidence that ONE passed verification at the planned difficulty crosses every VERIFIED gate:
#
#   left_join  band 5, importance 1.0: 2 independent applications (strength 1.1875 each: support
#              2.375, mean 0.771 -> DEVELOPING) + 8 AI delegations (debt ~21.7, MODERATE,
#              actionable) -> VERIFY. A pass at difficulty 0.9 (strength 1.8) gives support 4.175
#              and mean 0.838 -> VERIFIED, and the debt verification factor 0.2.
#   join_keys  a prerequisite of left_join without evidence -> UNKNOWN (NO_ACTION)
#   filtering  2 incorrect independent attempts -> EMERGING -> PRACTICE
#   sorting    4 correct independent applications -> DEMONSTRATED -> NO_ACTION
#   aliases    ONE AI delegation -> no debt -> NO_ACTION (a single AI use never plans a check)

# key -> (canonical name, description, node kind, band, importance, assessment types, parent)
P6_GRAPH: dict[str, tuple[str, str, str, int | None, float, list[str], str | None]] = {
    "data": (
        "Querying related tables",
        "Combine rows from related tables.",
        "TOPIC",
        None,
        0.5,
        [],
        None,
    ),
    "left_join": (
        "Choosing LEFT JOIN for optional matches",
        "Choose LEFT JOIN when rows without a match must still be kept.",
        "SKILL",
        5,
        1.0,
        ["mcq", "short_response"],
        "data",
    ),
    "join_keys": (
        "Matching rows on join keys",
        "Match the rows of two tables on their key columns.",
        "SKILL",
        2,
        0.6,
        ["mcq"],
        "data",
    ),
    "filtering": (
        "Filtering rows with WHERE",
        "Keep only the rows that satisfy a condition.",
        "SKILL",
        2,
        0.5,
        ["mcq", "numeric"],
        "data",
    ),
    "sorting": (
        "Sorting query results",
        "Order query results by one or more columns.",
        "SKILL",
        2,
        0.5,
        ["mcq"],
        "data",
    ),
    "aliases": (
        "Using table aliases",
        "Shorten qualified column names with table aliases.",
        "SKILL",
        2,
        0.5,
        ["mcq"],
        "data",
    ),
}
P6_PREREQUISITES = (("join_keys", "left_join"),)
CONFIDENT = {"mapping_confidence": 0.95, "attribution_confidence": 0.95}
LEFT_JOIN_APPLICATIONS = (
    "SELECT m.name, l.title FROM members m LEFT JOIN loans l ON l.member_id = m.id",
    "SELECT c.name, o.total FROM customers c LEFT JOIN orders o ON o.customer_id = c.id",
)


@dataclass
class P6Seed:
    learner_id: UUID
    course_id: UUID
    skills: dict[str, UUID]
    evidence_ids: list[UUID] = field(default_factory=list)


def seed_p6_graph(conn: Connection, learner_id: UUID, prefix: str) -> tuple[UUID, dict[str, UUID]]:
    """A READY course owned by the learner with the P6 graph (registry rows carry the prefix)."""
    slug = hashlib.sha256(prefix.encode()).hexdigest()[:10]
    (course_id,) = conn.execute(
        """
        insert into public.courses (owner_id, name, subject, level, graph_status, graph_version,
                                    graph_generated_at)
        values (%s, %s, 'Databases', 'Beginner', 'READY', 1, now()) returning id
        """,
        (learner_id, f"{prefix}Relational databases"),
    ).fetchone()
    conn.execute(
        "insert into public.course_memberships (course_id, user_id, role) "
        "values (%s, %s, 'STUDENT')",
        (course_id, learner_id),
    )
    ids: dict[str, UUID] = {}
    for key, (name, description, kind, band, importance, types, _parent) in P6_GRAPH.items():
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
                f"p6-{slug}-{key.replace('_', '-')}",
                full,
                full.lower(),
                description,
                kind,
                band,
                types,
                course_id,
            ),
        ).fetchone()
        conn.execute(
            "insert into public.course_skills (course_id, skill_id, importance, source, "
            "graph_version) values (%s, %s, %s, 'SEED', 1)",
            (course_id, ids[key], importance),
        )
    for key, (*_, parent) in P6_GRAPH.items():
        if parent:
            conn.execute(
                "insert into public.skill_edges (from_skill_id, to_skill_id, edge_type, source, "
                "course_id) values (%s, %s, 'PARENT', 'SEED', %s)",
                (ids[parent], ids[key], course_id),
            )
    for prerequisite, skill in P6_PREREQUISITES:
        conn.execute(
            "insert into public.skill_edges (from_skill_id, to_skill_id, edge_type, source, "
            "course_id) values (%s, %s, 'PREREQUISITE', 'SEED', %s)",
            (ids[prerequisite], ids[skill], course_id),
        )
    return course_id, ids


def delegation_turn(code: str, n: int) -> dict:
    """The learner asked the AI to perform the skill, and it did (OBSERVATION, actor AI)."""
    return {
        "user_text": f"Write the SQL query for report {n} for me.",
        "assistant_text": f"Here it is: `{code}`",
        "actor": "AI",
        "evidence_type": "OBSERVATION",
        "outcome_signal": "NOT_APPLICABLE",
        "ai_span": code,
        "reason_code": "AI_WROTE_SOLUTION",
        **CONFIDENT,
    }


def application_turn(code: str, outcome: str = "CORRECT", **confidence) -> dict:
    return {**_application(code, outcome), **(confidence or CONFIDENT)}


def seed_turns(
    pool: ConnectionPool,
    learner_id: UUID,
    plan: list[tuple[UUID, dict]],
    *,
    policy: IntelligencePolicy,
    now: datetime,
) -> list[UUID]:
    """Captured turns through the production qualification and persistence (no model call)."""
    evidence = []
    with pool.connection() as conn:
        for index, (skill_id, fields) in enumerate(plan):
            with conn.transaction():
                turn = seed_turn(
                    conn,
                    learner_id,
                    skill_id=skill_id,
                    occurred_at=now - timedelta(seconds=len(plan) - index),
                    policy=policy,
                    **fields,
                )
            if turn.evidence_id is not None:
                evidence.append(turn.evidence_id)
    return evidence


def left_join_plan(skill_id: UUID, *, applications: int = 2, delegations: int = 8) -> list:
    plan: list[tuple[UUID, dict]] = [
        (skill_id, application_turn(LEFT_JOIN_APPLICATIONS[n % 2] + f" -- {n}"))
        for n in range(applications)
    ]
    plan += [
        (
            skill_id,
            delegation_turn(
                f"SELECT s.name, e.grade FROM students s LEFT JOIN exams e ON e.sid = s.id -- {n}",  # noqa: S608 - learner text
                n,
            ),
        )
        for n in range(delegations)
    ]
    return plan


def seed_p6_evidence(
    pool: ConnectionPool,
    learner_id: UUID,
    course_id: UUID,
    skills: dict[str, UUID],
    *,
    now: datetime | None = None,
) -> P6Seed:
    now = now or datetime.now(UTC)
    with pool.connection() as conn:
        policy = load_policy(conn)
    plan = left_join_plan(skills["left_join"])
    plan += [
        (skills["filtering"], application_turn("SELECT * FROM t WHERE x = NULL", "INCORRECT")),
        (skills["filtering"], application_turn("SELECT * FROM t WHERE x => 3", "INCORRECT")),
    ]
    plan += [
        (skills["sorting"], application_turn(f"SELECT * FROM t ORDER BY c{n} DESC"))  # noqa: S608
        for n in range(4)
    ]
    plan.append((skills["aliases"], delegation_turn("SELECT a.x FROM alpha a", 99)))
    seed = P6Seed(learner_id, course_id, skills)
    seed.evidence_ids = seed_turns(pool, learner_id, plan, policy=policy, now=now)
    with pool.connection() as conn:
        rebuild_ledger(conn, learner_id, policy=policy, as_of=now)
        refresh_recommendations(conn, learner_id, policy=policy)
    return seed


def seed_p6_learner(
    pool: ConnectionPool, learner_id: UUID, prefix: str, *, now: datetime | None = None
) -> P6Seed:
    """The learner described above, on a new course and graph (a disposable database only)."""
    with pool.connection() as conn, conn.transaction():
        course_id, skills = seed_p6_graph(conn, learner_id, prefix)
    return seed_p6_evidence(pool, learner_id, course_id, skills, now=now)
