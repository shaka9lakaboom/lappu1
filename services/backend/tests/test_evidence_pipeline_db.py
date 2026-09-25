"""P3B + P4 end to end against PostgreSQL (ADR 0005):

    captured turn -> P3A mapping -> ONE attribution call per mapped segment
      -> deterministic evidence qualification -> immutable EvidenceEvents
      -> deterministic ledger (mastery + AI Assistance Debt)

Model calls go to a scripted fake provider.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
import pytest

from app.intelligence.mastery.ledger import rebuild_ledger, recompute_ledger
from app.intelligence.policy import load_policy
from app.intelligence.processing.pipeline import process_raw_message_job
from app.jobs.queue import JOB_PROCESS_RAW_MESSAGE
from app.jobs.worker import build_worker
from app.model_gateway import ProviderError, QuotaPolicy, RequestBudget
from app.model_gateway.gateway import ModelGateway
from app.model_gateway.recorder import DbModelRunRecorder
from tests.conftest import job_for
from tests.fakes import (
    FakeProvider,
    attribute_all,
    attribution,
    candidate_ids_in,
    proposal,
    route_responder,
    skill_ids_in,
    turn_segment,
)
from tests.test_pipeline_db import (
    LOOP_SKILL,
    bootstrapped_course,
    cached_gateway_for,
    fetch,
    gateway_for,
    id_for,
    ingest_turn,
    job_runs,
)

pytestmark = pytest.mark.db

INDEX_SKILL = "List Indexing"
LOOP_CODE = "for name in names: print(name)"
STUDENT_TURN = (
    f"I wrote this loop myself: {LOOP_CODE} and I used names[0] for the first one. Is it right?"
)
REPLY = "Yes, both are correct. names[0] is the first element."


def turn_mapping(text: str, *mapped: tuple[str, float]):
    """TURN_ANALYSIS answer mapping each (skill name, confidence)."""

    def respond(messages):
        ids = candidate_ids_in(messages)
        props = [proposal(id_for(messages, name), conf, span=text[:40]) for name, conf in mapped]
        chosen = [p["skill_id"] for p in props]
        ranked = chosen + [i for i in ids if i not in chosen]
        return {
            "segments": [turn_segment(text, ranked=ranked[:8], mappings=props, intent="practice")]
        }

    return respond


def skill_id(pool, prefix: str, name: str) -> UUID:
    return fetch(
        pool, "select id from public.skill_nodes where canonical_name = %s", f"{prefix}{name}"
    )[0][0]


def attribute_by_name(pool, prefix: str, answers: dict[str, dict]):
    """SKILL_ATTRIBUTION answer: per accepted skill name, the given attribution fields."""

    def respond(messages):
        listed = skill_ids_in(messages)
        by_id = {str(skill_id(pool, prefix, name)): fields for name, fields in answers.items()}
        return {"attributions": [attribution(i, **by_id[i]) for i in listed]}

    return respond


def job_state(pool, raw_id):
    return fetch(
        pool,
        "select state::text, attempts, outcome from public.processing_jobs where entity_id = %s "
        "and job_type = 'PROCESS_RAW_MESSAGE'",
        raw_id,
    )[0]


def ledger(pool, learner):
    return {
        r[0]: r[1:]
        for r in fetch(
            pool,
            "select skill_id, mastery_state::text, alpha, beta, support, debt_eligible, debt_score, "
            "evidence_count, performance_evidence_count, recent_delegation_count, ledger_version "
            "from public.skill_ledger where learner_id = %s",
            learner,
        )
    }


def counts(pool, learner):
    return fetch(
        pool,
        "select (select count(*) from public.attributions where learner_id = %s), "
        "(select count(*) from public.evidence_events where learner_id = %s), "
        "(select count(*) from public.skill_ledger where learner_id = %s)",
        learner,
        learner,
        learner,
    )[0]


def mixed_turn_provider(pool, prefix):
    return FakeProvider(
        route_responder(
            turn=turn_mapping(STUDENT_TURN, (LOOP_SKILL, 0.91), (INDEX_SKILL, 0.85)),
            attribution=attribute_by_name(
                pool,
                prefix,
                {
                    LOOP_SKILL: {"student_span": LOOP_CODE},
                    INDEX_SKILL: {
                        "actor": "AI",
                        "evidence_type": "OBSERVATION",
                        "outcome": "NOT_APPLICABLE",
                        "ai_span": "names[0] is the first element",
                        "reason_code": "AI_EXPLAINED",
                    },
                },
            ),
        )
    )


def test_mapped_turn_becomes_typed_evidence_with_complete_provenance_and_a_ledger(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    user_id, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    provider = mixed_turn_provider(db_pool, registry)
    build_worker(db_pool, gateway_for(db_pool, provider), batch_size=10).drain()

    assert job_state(db_pool, assistant_id) == ("COMPLETED", 1, "EVIDENCE_RECORDED")
    # One TURN_ANALYSIS + ONE attribution call for both accepted skills.
    assert len(provider.calls) == 2
    loop_id, index_id = (
        skill_id(db_pool, registry, LOOP_SKILL),
        skill_id(db_pool, registry, INDEX_SKILL),
    )
    assert set(skill_ids_in(provider.calls[1]["messages"])) == {str(loop_id), str(index_id)}
    job = job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE)
    runs = job_runs(db_pool, job.id)
    assert [(r[1], r[2]) for r in runs] == [
        ("EMBED_QUERY", "retrieval-query/v1"),
        ("TURN_ANALYSIS", "turn-analysis/v1"),
        ("SKILL_ATTRIBUTION", "skill-attribution/v2"),
    ]
    attribution_run = runs[2][0]

    rows = fetch(
        db_pool,
        """select e.skill_id, e.evidence_type::text, e.actor::text, e.outcome_signal::text, e.outcome,
                  e.strength, e.difficulty, e.difficulty_multiplier, e.independence, e.base_weight,
                  e.mapping_confidence, e.attribution_confidence, e.evidence_confidence,
                  e.raw_message_ids, e.model_run_ids, e.source_type::text, e.source_id = e.attribution_id,
                  a.status::text, a.actor::text, a.evidence_decision, a.model_run_id, a.prompt_version,
                  m.status::text, m.id = a.mapping_id, d.id = m.decision_id, s.id = d.segment_id,
                  s.source_message_ids, s.qualification_model_run_id, d.query_model_run_id,
                  e.evidence_span, e.qualifier_version, e.excluded
             from public.evidence_events e
             join public.attributions a on a.id = e.attribution_id
             join public.skill_mappings m on m.id = e.mapping_id
             join public.mapping_decisions d on d.id = e.decision_id
             join public.activity_segments s on s.id = e.segment_id
            where e.learner_id = %s""",
        learner,
    )
    by_skill = {r[0]: r for r in rows}
    assert set(by_skill) == {loop_id, index_id}

    loop = by_skill[loop_id]
    assert loop[1:5] == ("INDEPENDENT_APPLICATION", "STUDENT", "CORRECT", 1.0)
    # strength = base 1.0 * (0.75 + 0.5 * difficulty 0.25) * independence 1.0 * min(0.91, 0.9)
    assert loop[6:10] == (0.25, 0.875, 1.0, 1.0)
    assert loop[5] == pytest.approx(0.875 * 0.9, rel=1e-6)
    assert loop[10] == pytest.approx(0.91, abs=1e-6) and loop[11] == pytest.approx(0.9, abs=1e-6)
    assert loop[12] == pytest.approx(0.9, abs=1e-6)
    assert loop[29] == {"student": LOOP_CODE, "ai": None, "mapping": STUDENT_TURN[:40]}
    assert loop[30] == "evidence/p8-v1" and loop[31] is False

    index = by_skill[index_id]
    # Exposure/observation: no strength, no outcome - reading the AI answer is not mastery.
    assert index[1:6] == ("OBSERVATION", "AI", "NOT_APPLICABLE", None, 0.0)

    for r in rows:
        # raw_messages -> segment -> decision -> ACCEPTED mapping -> attribution -> evidence -> model runs
        assert r[13] == [user_id, assistant_id] == r[26]
        assert r[15] == "AI_ACTIVITY" and r[16] is True
        assert r[17:20] == ("ATTRIBUTED", r[2], "EVIDENCE_CREATED")
        assert r[20] == attribution_run and r[21] == "skill-attribution/v2"
        assert r[22] == "ACCEPTED" and r[23] and r[24] and r[25]
        assert set(r[14]) == {r[27], r[28], attribution_run}  # turn run, query run, attribution run

    led = ledger(db_pool, learner)
    # One piece of evidence each: below the support gate -> UNKNOWN, never weak.
    assert led[loop_id][0] == "UNKNOWN" and led[loop_id][1] == pytest.approx(1 + 0.7875, rel=1e-5)
    assert led[loop_id][3] == pytest.approx(0.7875, rel=1e-5)
    assert led[index_id][0:4] == ("UNKNOWN", 1.0, 1.0, 0.0)
    assert led[index_id][6:9] == (1, 0, 1)  # one evidence event, no performance, one delegation
    assert led[index_id][4:6] == (False, 0.0)  # a single AI interaction never creates debt

    with db_pool.connection() as conn, pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        conn.execute(
            "update public.evidence_events set strength = 5 where learner_id = %s", (learner,)
        )
    with db_pool.connection() as conn, pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        conn.execute(
            "update public.attributions set actor = 'STUDENT' where learner_id = %s", (learner,)
        )


def test_only_accepted_mappings_are_attributed(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    seen = []

    def attribute(messages):
        seen.append(skill_ids_in(messages))
        return attribute_all(student_span=LOOP_CODE)(messages)

    provider = FakeProvider(
        route_responder(
            turn=turn_mapping(STUDENT_TURN, (LOOP_SKILL, 0.91), (INDEX_SKILL, 0.5)),
            attribution=attribute,
        )
    )
    build_worker(db_pool, gateway_for(db_pool, provider), batch_size=10).drain()
    loop_id = skill_id(db_pool, registry, LOOP_SKILL)
    assert seen == [[str(loop_id)]]  # the abstained mapping never reaches attribution
    assert fetch(
        db_pool,
        "select m.status::text, count(a.id) from public.skill_mappings m "
        "left join public.attributions a on a.mapping_id = m.id where m.learner_id = %s "
        "group by m.status order by 1",
        learner,
    ) == [("ABSTAINED", 0), ("ACCEPTED", 1)]

    # The database refuses an attribution of a mapping that is not ACCEPTED.
    (abstained,) = fetch(
        db_pool,
        "select id, decision_id, segment_id, skill_id from public.skill_mappings "
        "where learner_id = %s and status = 'ABSTAINED'",
        learner,
    )
    with db_pool.connection() as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into public.attributions (learner_id, mapping_id, decision_id, segment_id, skill_id, "
            "status, actor, confidence, proposed_evidence_type, outcome_signal, rationale_code, "
            "evidence_decision, prompt_version, attributor_version, attribution_version) values "
            "(%s, %s, %s, %s, %s, 'ATTRIBUTED', 'STUDENT', 0.9, 'INDEPENDENT_APPLICATION', 'CORRECT', "
            "'X_TEST', 'EVIDENCE_CREATED', 'skill-attribution/v1', 'attributor/p3b-v1', 'p3b-test')",
            (learner, *abstained),
        )


@pytest.mark.parametrize(
    ("fields", "decision"),
    [
        ({"actor": "UNKNOWN"}, "ACTOR_UNKNOWN"),
        ({"confidence": 0.6, "outcome": "INCORRECT"}, "LOW_ATTRIBUTION_CONFIDENCE"),
        ({"student_span": "code the learner never wrote"}, "STUDENT_SPAN_NOT_GROUNDED"),
    ],
)
def test_uncertain_attribution_abstains_and_writes_no_evidence(
    db_pool, registry, new_learner, fields, decision
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    fields = {"student_span": LOOP_CODE, **fields}
    provider = FakeProvider(
        route_responder(
            turn=turn_mapping(STUDENT_TURN, (LOOP_SKILL, 0.91)),
            attribution=attribute_all(**fields),
        )
    )
    build_worker(db_pool, gateway_for(db_pool, provider), batch_size=10).drain()
    assert job_state(db_pool, assistant_id) == ("COMPLETED", 1, "MAPPED_NO_EVIDENCE")
    assert fetch(
        db_pool,
        "select status::text, evidence_decision from public.attributions where learner_id = %s",
        learner,
    ) == [("ATTRIBUTED", decision)]
    assert counts(db_pool, learner) == (1, 0, 0)  # no evidence, no ledger row: still UNKNOWN


def test_invalid_attribution_output_is_repaired_once_then_abstains(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    provider = FakeProvider(
        route_responder(
            turn=turn_mapping(STUDENT_TURN, (LOOP_SKILL, 0.91), (INDEX_SKILL, 0.85)),
            attribution=["{not json", {"attributions": []}],
        )
    )
    build_worker(db_pool, gateway_for(db_pool, provider), batch_size=10).drain()
    assert job_state(db_pool, assistant_id) == ("COMPLETED", 1, "MAPPED_NO_EVIDENCE")
    assert len(provider.calls) == 3  # turn + attribution + exactly one repair
    rows = fetch(
        db_pool,
        "select a.status::text, a.actor, a.rationale_code, a.evidence_decision, r.status::text, r.attempt "
        "from public.attributions a join public.model_runs r on r.id = a.model_run_id "
        "where a.learner_id = %s",
        learner,
    )
    assert (
        rows
        == [
            ("ABSTAINED", None, "MODEL_OUTPUT_INVALID", "MODEL_OUTPUT_INVALID", "INVALID_OUTPUT", 2)
        ]
        * 2
    )
    # The mappings stay valid; no evidence is written.
    assert fetch(
        db_pool,
        "select count(*) from public.skill_mappings where learner_id = %s and status = 'ACCEPTED'",
        learner,
    ) == [(2,)]
    assert counts(db_pool, learner) == (2, 0, 0)


def test_a_paste_from_an_earlier_reply_outside_the_context_window_is_the_ai_s_work(
    db_pool, registry, new_learner
) -> None:
    """H8 (ADR 0008): the copy guard searches the conversation's earlier assistant messages, not
    only the 4-message recent context. The pasted code was the AI's, eight messages earlier."""
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    conversation = "copy-guard-conversation"
    code = "for position, name in enumerate(names): print(position, name)"
    ingest_turn(
        db_pool,
        learner,
        "How do I print names with positions?",
        f"Try this: {code}",
        conversation=conversation,
        base_index=0,
    )
    for i in range(1, 4):
        ingest_turn(
            db_pool,
            learner,
            f"Unrelated question number {i}.",
            f"Unrelated answer {i}.",
            conversation=conversation,
            base_index=2 * i,
        )
    pasted = f"I wrote this loop myself: {code}"
    _, assistant_id = ingest_turn(
        db_pool, learner, pasted, "Looks right.", conversation=conversation, base_index=8
    )
    provider = FakeProvider(
        route_responder(
            turn=turn_mapping(pasted, (LOOP_SKILL, 0.91)),
            attribution=attribute_all(student_span=code),
        )
    )
    job = job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE)
    assert process_raw_message_job(db_pool, gateway_for(db_pool, provider), job).outcome == (
        "EVIDENCE_RECORDED"
    )
    assert fetch(
        db_pool,
        "select actor::text, evidence_type::text, strength, qualification_reason, "
        "qualifier_version from public.evidence_events where learner_id = %s",
        learner,
    ) == [("AI", "OBSERVATION", 0.0, "COPIED_FROM_AI", "evidence/p8-v1")]


def test_replay_never_duplicates_evidence_or_calls(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    provider = mixed_turn_provider(db_pool, registry)
    gateway = gateway_for(db_pool, provider)
    job = job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE)
    assert process_raw_message_job(db_pool, gateway, job).outcome == "EVIDENCE_RECORDED"
    before_calls, before = len(provider.calls), ledger(db_pool, learner)

    for _ in range(2):
        assert process_raw_message_job(db_pool, gateway, job).outcome == "ALREADY_ANALYZED"
    assert len(provider.calls) == before_calls
    assert counts(db_pool, learner) == (2, 2, 2)
    assert ledger(db_pool, learner) == before  # same values, same ledger_version


def test_attribution_503_defers_and_the_retry_resumes_without_repeating_p3a(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    outage = iter([ProviderError("unavailable", "HTTP_503")])

    def attribute(messages):
        return next(outage, None) or attribute_all(student_span=LOOP_CODE)(messages)

    # Only ONE turn-analysis answer is scripted: repeating P3A would fail the job.
    provider = FakeProvider(
        route_responder(
            turn=[turn_mapping(STUDENT_TURN, (LOOP_SKILL, 0.91))], attribution=attribute
        )
    )
    worker = build_worker(db_pool, gateway_for(db_pool, provider), batch_size=10)
    worker.drain()
    assert job_state(db_pool, assistant_id) == ("PENDING", 0, "MODEL_BACKPRESSURE")
    # P3A is committed and consistent; attribution is simply pending.
    assert fetch(
        db_pool, "select count(*) from public.skill_mappings where learner_id = %s", learner
    ) == [(1,)]
    assert counts(db_pool, learner) == (0, 0, 0)

    with db_pool.connection() as conn:
        conn.execute(
            "update public.processing_jobs set available_at = now() where entity_id = %s",
            (assistant_id,),
        )
    worker.drain()
    assert job_state(db_pool, assistant_id) == ("COMPLETED", 1, "EVIDENCE_RECORDED")
    tasks = [
        (r[1], r[3])
        for r in job_runs(db_pool, job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE).id)
    ]
    assert tasks == [
        ("EMBED_QUERY", "SUCCEEDED"),
        ("TURN_ANALYSIS", "SUCCEEDED"),
        ("SKILL_ATTRIBUTION", "UNAVAILABLE"),
        ("SKILL_ATTRIBUTION", "SUCCEEDED"),
    ]
    assert counts(db_pool, learner) == (1, 1, 1)


def test_spent_budget_defers_attribution_before_any_request(db_pool, registry, new_learner) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    model = "budget-evidence-test"
    with db_pool.connection() as conn:
        conn.execute("delete from public.model_runs where model = %s", (model,))
    recorder = DbModelRunRecorder(db_pool)
    provider = FakeProvider(
        route_responder(
            turn=turn_mapping(STUDENT_TURN, (LOOP_SKILL, 0.91)),
            attribution=attribute_all(student_span=LOOP_CODE),
        )
    )
    gateway = ModelGateway(
        provider,
        recorder,
        generation_model=model,
        embedding_model="gemini-embedding-2",
        default_timeout=5,
        budget=RequestBudget(QuotaPolicy({model: 3}, reserve=2), recorder),
    )
    build_worker(db_pool, gateway, batch_size=10).drain()
    assert job_state(db_pool, assistant_id) == ("PENDING", 0, "MODEL_BUDGET_RESERVE")
    assert len(provider.calls) == 1  # the turn analysis; attribution never reached the provider
    assert counts(db_pool, learner) == (0, 0, 0)


def test_identical_repeated_turn_reuses_both_cached_generations(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    provider = mixed_turn_provider(db_pool, registry)
    gateway = cached_gateway_for(db_pool, provider)
    _, first = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    process_raw_message_job(db_pool, gateway, job_for(db_pool, first, JOB_PROCESS_RAW_MESSAGE))
    assert len(provider.calls) == 2
    _, second = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    result = process_raw_message_job(
        db_pool, gateway, job_for(db_pool, second, JOB_PROCESS_RAW_MESSAGE)
    )
    assert result.outcome == "EVIDENCE_RECORDED"
    assert len(provider.calls) == 2  # zero provider requests for the repeat
    runs = job_runs(db_pool, job_for(db_pool, second, JOB_PROCESS_RAW_MESSAGE).id)
    assert [(r[1], r[4] is not None) for r in runs] == [
        ("EMBED_QUERY", True),
        ("TURN_ANALYSIS", True),
        ("SKILL_ATTRIBUTION", True),
    ]
    # A new captured turn is new evidence (the cache saves the call, not the activity).
    assert counts(db_pool, learner) == (4, 4, 2)


def test_repeated_delegation_creates_debt_but_a_single_one_does_not(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    loop_id = skill_id(db_pool, registry, LOOP_SKILL)
    ask = "Write a for loop over my list of names for me."
    provider = FakeProvider(
        route_responder(
            turn=turn_mapping(ask, (LOOP_SKILL, 0.9)),
            attribution=attribute_all(
                actor="AI",
                evidence_type="OBSERVATION",
                outcome="NOT_APPLICABLE",
                ai_span="for name in names:",
                reason_code="SOLUTION_REQUESTED",
            ),
        )
    )
    gateway = gateway_for(db_pool, provider)
    states = []
    for n in range(3):
        _, assistant_id = ingest_turn(
            db_pool, learner, f"{ask} (attempt {n})", "for name in names:\n    print(name)"
        )
        process_raw_message_job(
            db_pool, gateway, job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE)
        )
        row = ledger(db_pool, learner)[loop_id]
        states.append((row[0], row[3], row[4], row[8]))
    # (state, support, debt_eligible, recent delegations)
    assert [s[0] for s in states] == ["UNKNOWN"] * 3  # AI work never raises mastery
    assert [s[1] for s in states] == [0.0] * 3
    assert [s[2] for s in states] == [False, True, True]
    assert [s[3] for s in states] == [1, 2, 3]
    (score, components) = fetch(
        db_pool,
        "select debt_score, debt_components from public.skill_ledger where learner_id = %s and skill_id = %s",
        learner,
        loop_id,
    )[0]
    assert score > 0 and components["eligibility"] == "ELIGIBLE"
    assert components["verification"] == "UNVERIFIED" and components["verification_factor"] == 0.6


def test_exclusion_is_one_way_and_the_rebuilt_ledger_drops_the_evidence(
    db_pool, registry, new_learner
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    provider = mixed_turn_provider(db_pool, registry)
    process_raw_message_job(
        db_pool,
        gateway_for(db_pool, provider),
        job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE),
    )
    loop_id = skill_id(db_pool, registry, LOOP_SKILL)
    as_of = datetime.now(UTC) + timedelta(minutes=1)
    with db_pool.connection() as conn:
        policy = load_policy(conn)
        first = recompute_ledger(conn, learner, [loop_id], policy=policy, as_of=as_of)
        again = recompute_ledger(conn, learner, [loop_id], policy=policy, as_of=as_of)
        assert first == again  # idempotent
        version = ledger(db_pool, learner)[loop_id][-1]
        conn.execute("delete from public.skill_ledger where learner_id = %s", (learner,))
        rebuilt = rebuild_ledger(conn, learner, policy=policy, as_of=as_of)
    assert {r.skill_id for r in rebuilt} == set(ledger(db_pool, learner))
    assert [r for r in rebuilt if r.skill_id == loop_id][0] == first[0]
    assert version >= 1

    with db_pool.connection() as conn:
        conn.execute(
            "update public.evidence_events set excluded = true, exclusion_reason = 'LEARNER_FEEDBACK', "
            "excluded_at = now() where learner_id = %s and skill_id = %s",
            (learner, loop_id),
        )
    with db_pool.connection() as conn:  # a separate transaction: re-inclusion is refused
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            conn.execute(
                "update public.evidence_events set excluded = false, exclusion_reason = null, "
                "excluded_at = null where learner_id = %s and skill_id = %s",
                (learner, loop_id),
            )
    with db_pool.connection() as conn:
        (row,) = recompute_ledger(conn, learner, [loop_id], policy=load_policy(conn), as_of=as_of)
    assert (row.mastery.support, row.mastery.evidence_count, row.mastery.state) == (
        0.0,
        0,
        "UNKNOWN",
    )
    # Provenance is kept: the excluded evidence still exists.
    assert fetch(
        db_pool,
        "select count(*) from public.evidence_events where learner_id = %s and excluded",
        learner,
    ) == [(1,)]
