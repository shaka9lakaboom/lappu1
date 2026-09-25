"""Attribution / evidence consistency end to end (ADR 0008; the 2026-09-25 hosted defect).

The hosted learner asked "how to write a for loop" (the AI answered with a canonical example),
later wrote the same two lines as "my own code" with their OWN explanation of it, and asked the AI
to check the explanation. The attributor said STUDENT; the copy guard recorded the evidence as the
AI's (the code was in the earlier AI answer). Three things went wrong: the activity chip showed the
attributor's claim ("Actor: You") next to the evidence's "The AI did it"; the reclassified evidence
counted as a second delegation of the one AI answer (actionable debt, VERIFY); and the learner's own
explanation was lost because the attributor was never told which text was reused.

Every case runs the production pipeline (scripted model outputs) and then reads the SAME mapped
skill back through GET /v1/activity and GET /v1/skills/{id}: the activity actor, the evidence
actor and the skill timeline actor must be one value. Synthetic text only.

On the critical gate, gemini-3.5-flash-lite still quoted the reused loop under the v2 prompt, so
the attribution validator now refuses a learner span the copy guard would reclassify (one
repair). The copy guard stays as the safety net; the rows it wrote before - the hosted one - are
covered by the pre-validator case.
"""

from uuid import UUID

import pytest

from app.intelligence.processing.pipeline import process_raw_message_job
from app.jobs.queue import JOB_PROCESS_RAW_MESSAGE
from tests.conftest import api_client, job_for
from tests.fakes import FakeProvider, attribute_all, route_responder, skill_ids_in
from tests.test_courses_api import auth
from tests.test_evidence_pipeline_db import skill_id, turn_mapping
from tests.test_pipeline_db import LOOP_SKILL, bootstrapped_course, fetch, gateway_for, ingest_turn

pytestmark = pytest.mark.db

CODE = "for n in range(3):\r\n    print(n)"
EXPLANATION = "range(3) gives 0, 1 and 2, and the body runs once for each of them"
OWN_WORK = (
    "I'm practising loops.\r\n\r\nHere is my own code:\r\n\r\n"
    f"{CODE}\r\n\r\nMy understanding is that {EXPLANATION}.\r\n\r\n"
    "Can you check whether my explanation is correct?"
)
CONFIRMATION = (
    "Your explanation is correct.\n\nfor n in range(3):\nprint(n)\n"
    "range(3) produces 0, 1, 2 and the loop body runs once per value."
)
HOW_TO = "How do I write a for loop in Python?"
AI_EXAMPLE = "A basic for loop looks like this:\n\nfor n in range(3): print(n)\n\nOutput: 0 1 2"


@pytest.fixture
def client(db_pool, verifier):
    return api_client(verifier, db_pool)


def run_turn(pool, learner, user, reply, attribution, *, conversation, index):
    """Ingest one turn, process it once; returns (provider, anchor raw id)."""
    user_id, assistant_id = ingest_turn(
        pool, learner, user, reply, conversation=conversation, base_index=index
    )
    provider = FakeProvider(
        route_responder(turn=turn_mapping(user, (LOOP_SKILL, 0.91)), attribution=attribution)
    )
    job = job_for(pool, assistant_id, JOB_PROCESS_RAW_MESSAGE)
    process_raw_message_job(pool, gateway_for(pool, provider), job)
    return provider, user_id


def ask_how_to(pool, learner, conversation):
    """The genuine delegation: the AI shows the canonical loop."""
    return run_turn(
        pool,
        learner,
        HOW_TO,
        AI_EXAMPLE,
        attribute_all(
            actor="AI",
            evidence_type="OBSERVATION",
            outcome="NOT_APPLICABLE",
            ai_span="for n in range(3): print(n)",
            reason_code="EXPLANATION_REQUESTED",
        ),
        conversation=conversation,
        index=0,
    )


def chain(pool, client, token, learner, anchor: UUID, skill: UUID) -> dict:
    """Every recorded view of the anchor's mapped skill: attribution, evidence, activity chip,
    skill timeline, ledger, recommendation."""
    ((attributed, decision),) = fetch(
        pool,
        "select a.actor::text, a.evidence_decision from public.attributions a "
        "join public.activity_segments s on s.id = a.segment_id where s.anchor_message_id = %s",
        anchor,
    )
    evidence = fetch(
        pool,
        "select e.id, e.actor::text, e.evidence_type::text, e.qualification_reason, e.strength "
        "from public.evidence_events e join public.activity_segments s on s.id = e.segment_id "
        "where s.anchor_message_id = %s",
        anchor,
    )
    items = client.get("/v1/activity?limit=50", headers=auth(token)).json()["items"]
    (row,) = [i for i in items if i["id"] == str(anchor)]
    (chip,) = [m for s in row["segments"] for m in s["mappings"] if m["skill_id"] == str(skill)]
    detail = client.get(f"/v1/skills/{skill}", headers=auth(token)).json()
    timeline = {t["event"]["id"]: t for t in detail["evidence"]}
    ledger = fetch(
        pool,
        "select mastery_state::text, alpha, debt_eligible, debt_score, recent_delegation_count "
        "from public.skill_ledger where learner_id = %s and skill_id = %s",
        learner,
        skill,
    )[0]
    recommendation = fetch(
        pool,
        "select type::text from public.recommendations where learner_id = %s and skill_id = %s "
        "and state = 'ACTIVE'",
        learner,
        skill,
    )
    return {
        "attributed": attributed,
        "decision": decision,
        "evidence": evidence,
        "chip": chip,
        "timeline": timeline,
        "ledger": ledger,
        "recommendation": recommendation[0][0] if recommendation else None,
    }


def assert_one_actor(view: dict) -> str:
    """Activity actor = evidence actor = skill timeline actor, for the same evidence."""
    ((evidence_id, actor, *_),) = view["evidence"]
    assert view["chip"]["evidence_id"] == str(evidence_id)
    assert view["chip"]["actor"] == actor
    assert view["timeline"][str(evidence_id)]["event"]["actor"] == actor
    assert view["chip"]["attributed_actor"] == view["attributed"]
    return actor


def test_own_code_and_explanation_checked_by_the_ai_stays_the_learner_s(
    db_pool, registry, new_learner, client, make_token
) -> None:
    """No earlier AI example: the confirmation (which repeats the code) never erases the work."""
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    provider, anchor = run_turn(
        db_pool,
        learner,
        OWN_WORK,
        CONFIRMATION,
        attribute_all(student_span=CODE, reason_code="STUDENT_WROTE_CODE"),
        conversation="own-work",
        index=0,
    )
    assert "Reused assistant text" in provider.calls[-1]["messages"][0].content
    assert "<<<\n(none)\n>>>" in provider.calls[-1]["messages"][0].content
    skill = skill_id(db_pool, registry, LOOP_SKILL)
    view = chain(db_pool, client, make_token(learner), learner, anchor, skill)
    assert assert_one_actor(view) == "STUDENT" == view["attributed"]
    ((_, _, etype, reason, strength),) = view["evidence"]
    assert (etype, reason) == ("INDEPENDENT_APPLICATION", "QUALIFIED") and strength > 0
    assert view["chip"]["qualification_reason"] == "QUALIFIED"
    state, alpha, eligible, score, _ = view["ledger"]
    assert alpha > 1.0 and (eligible, score) == (False, 0.0)


def test_reused_ai_code_with_an_own_explanation_is_credited_as_the_explanation(
    db_pool, registry, new_learner, client, make_token
) -> None:
    """The live shape, attributed as skill-attribution/v2 asks: the attributor is told which text
    is reused and quotes the learner's own explanation instead."""
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    ask_how_to(db_pool, learner, "reused-explained")
    provider, anchor = run_turn(
        db_pool,
        learner,
        OWN_WORK,
        CONFIRMATION,
        attribute_all(
            student_span=EXPLANATION,
            evidence_type="INDEPENDENT_EXPLANATION",
            reason_code="STUDENT_EXPLAINED_REASONING",
        ),
        conversation="reused-explained",
        index=2,
    )
    request = provider.calls[-1]["messages"][0].content
    assert "Reused assistant text" in request and "- for n in range(3): print(n)" in request
    skill = skill_id(db_pool, registry, LOOP_SKILL)
    assert set(skill_ids_in(provider.calls[-1]["messages"])) == {str(skill)}
    view = chain(db_pool, client, make_token(learner), learner, anchor, skill)
    assert assert_one_actor(view) == "STUDENT" == view["attributed"]
    ((_, _, etype, reason, strength),) = view["evidence"]
    assert (etype, reason) == ("INDEPENDENT_EXPLANATION", "QUALIFIED") and strength > 0
    state, alpha, eligible, score, delegations = view["ledger"]
    # The one genuine delegation (the how-to question) is not debt; the explanation is mastery.
    assert alpha > 1.0 and (eligible, score, delegations) == (False, 0.0, 1)
    assert view["recommendation"] != "VERIFY"


CITES_CODE = {"student_span": CODE, "reason_code": "STUDENT_WROTE_CODE"}
CITES_EXPLANATION = {
    "student_span": EXPLANATION,
    "evidence_type": "INDEPENDENT_EXPLANATION",
    "reason_code": "STUDENT_EXPLAINED_REASONING",
}


def test_reused_ai_code_cited_as_own_is_repaired_to_the_learner_s_explanation(
    db_pool, registry, new_learner, client, make_token
) -> None:
    """The live shape as gemini-3.5-flash-lite answered it on the critical gate (skill-attribution
    v2 still quoting the reused loop): the validator refuses the span the copy guard would
    reclassify, and the single repair quotes the learner's own explanation."""
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    ask_how_to(db_pool, learner, "reused-repaired")
    provider, anchor = run_turn(
        db_pool,
        learner,
        OWN_WORK,
        CONFIRMATION,
        [attribute_all(**CITES_CODE), attribute_all(**CITES_EXPLANATION)],
        conversation="reused-repaired",
        index=2,
    )
    repair = provider.calls[-1]["messages"][-1].content
    assert "quotes text the assistant already wrote earlier" in repair
    skill = skill_id(db_pool, registry, LOOP_SKILL)
    view = chain(db_pool, client, make_token(learner), learner, anchor, skill)
    assert assert_one_actor(view) == "STUDENT" == view["attributed"]
    ((_, _, etype, reason, strength),) = view["evidence"]
    assert (etype, reason) == ("INDEPENDENT_EXPLANATION", "QUALIFIED") and strength > 0
    state, alpha, eligible, score, delegations = view["ledger"]
    assert alpha > 1.0 and (eligible, score, delegations) == (False, 0.0, 1)
    assert view["recommendation"] != "VERIFY"


def test_reused_ai_code_insisted_on_abstains_and_is_never_a_delegation(
    db_pool, registry, new_learner, client, make_token
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    ask_how_to(db_pool, learner, "reused-insisted")
    provider, anchor = run_turn(
        db_pool,
        learner,
        OWN_WORK,
        CONFIRMATION,
        [attribute_all(**CITES_CODE), attribute_all(**CITES_CODE)],
        conversation="reused-insisted",
        index=2,
    )
    assert fetch(
        db_pool,
        "select a.status::text, a.evidence_decision from public.attributions a "
        "join public.activity_segments s on s.id = a.segment_id where s.anchor_message_id = %s",
        anchor,
    ) == [("ABSTAINED", "MODEL_OUTPUT_INVALID")]
    assert (
        fetch(
            db_pool,
            "select count(*) from public.evidence_events e join public.activity_segments s "
            "on s.id = e.segment_id where s.anchor_message_id = %s",
            anchor,
        )[0][0]
        == 0
    )
    skill = skill_id(db_pool, registry, LOOP_SKILL)
    items = client.get("/v1/activity?limit=50", headers=auth(make_token(learner))).json()["items"]
    (row,) = [i for i in items if i["id"] == str(anchor)]
    (chip,) = [m for seg in row["segments"] for m in seg["mappings"] if m["skill_id"] == str(skill)]
    # Nothing was recorded, so the chip names no actor (never the attributor's claim).
    assert (chip["actor"], chip["evidence_id"]) == (None, None)
    ledger = fetch(
        db_pool,
        "select debt_eligible, debt_score, recent_delegation_count from public.skill_ledger "
        "where learner_id = %s and skill_id = %s",
        learner,
        skill,
    )[0]
    assert ledger == (False, 0.0, 1)


def test_a_pre_validator_copied_from_ai_row_is_the_ai_s_and_never_a_second_delegation(
    db_pool, registry, new_learner, client, make_token, monkeypatch
) -> None:
    """Rows written before the validator existed - like the hosted one - carry COPIED_FROM_AI
    evidence (the attributor's STUDENT claim reclassified by the copy guard). They are shown as
    the AI's everywhere, with the reason, and they are not a second delegation."""
    from app.intelligence.attribution import engine as attribution_engine

    monkeypatch.setattr(attribution_engine, "copied_from_ai", lambda *args: False)
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    ask_how_to(db_pool, learner, "reused-historic")
    _, anchor = run_turn(
        db_pool,
        learner,
        OWN_WORK,
        CONFIRMATION,
        attribute_all(**CITES_CODE),
        conversation="reused-historic",
        index=2,
    )
    skill = skill_id(db_pool, registry, LOOP_SKILL)
    view = chain(db_pool, client, make_token(learner), learner, anchor, skill)
    assert assert_one_actor(view) == "AI"
    assert view["attributed"] == "STUDENT"  # the attributor's claim stays as provenance
    ((evidence_id, _, etype, reason, strength),) = view["evidence"]
    assert (etype, reason, strength) == ("OBSERVATION", "COPIED_FROM_AI", 0.0)
    assert view["chip"]["qualification_reason"] == "COPIED_FROM_AI"
    item = view["timeline"][str(evidence_id)]
    assert (item["counts_toward_mastery"], item["counts_toward_debt"]) == (False, False)
    state, alpha, eligible, score, delegations = view["ledger"]
    assert (state, alpha, eligible, score, delegations) == ("UNKNOWN", 1.0, False, 0.0, 1)
    assert view["recommendation"] != "VERIFY"


@pytest.mark.parametrize(
    "attribution",
    [
        # The honest answer: the AI wrote the loop.
        {
            "actor": "AI",
            "evidence_type": "OBSERVATION",
            "outcome": "NOT_APPLICABLE",
            "ai_span": "for n in range(3): print(n)",
            "reason_code": "EXPLANATION_REQUESTED",
        },
        # A wrong answer claiming the learner wrote it: the code is not in the learner's message.
        {"student_span": "for n in range(3): print(n)", "reason_code": "STUDENT_WROTE_CODE"},
    ],
    ids=["ai-observation", "claimed-student"],
)
def test_a_genuine_how_to_question_is_the_ai_s_with_no_mastery_credit(
    attribution, db_pool, registry, new_learner, client, make_token
) -> None:
    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    provider, anchor = run_turn(
        db_pool,
        learner,
        HOW_TO,
        AI_EXAMPLE,
        attribute_all(**attribution),
        conversation="how-to",
        index=0,
    )
    skill = skill_id(db_pool, registry, LOOP_SKILL)
    evidence = fetch(
        db_pool,
        "select actor::text, evidence_type::text, strength from public.evidence_events "
        "where learner_id = %s",
        learner,
    )
    assert all(actor == "AI" and strength == 0.0 for actor, _, strength in evidence)
    if evidence:
        view = chain(db_pool, client, make_token(learner), learner, anchor, skill)
        assert assert_one_actor(view) == "AI"
    ledger = fetch(
        db_pool,
        "select alpha, mastery_state::text from public.skill_ledger where learner_id = %s",
        learner,
    )
    assert all(alpha == 1.0 and state == "UNKNOWN" for alpha, state in ledger)
