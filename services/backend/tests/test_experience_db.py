"""P5 against PostgreSQL (ADR 0006): skill detail and its "Why?", the enriched activity feed, the
correction loop and the recommendation queue. Every endpoint is scoped to the authenticated
learner explicitly, and nothing here makes a model call (model_runs never grows).

The learner is seeded by tests/p5_fixtures.py (no model call; production qualification,
persistence, ledger and recommendation code).
"""

import json
from uuid import uuid4

import pytest

from app.intelligence.mastery.ledger import rebuild_ledger
from app.intelligence.policy import load_policy
from app.intelligence.recommendations.service import refresh_recommendations
from tests.conftest import api_client
from tests.p5_fixtures import finish_turn, seed_p5_learner, seed_turn
from tests.p6_fixtures import graded_verification
from tests.test_courses_api import auth
from tests.test_pipeline_db import fetch, ingest_turn

pytestmark = pytest.mark.db

PROVENANCE_TABLES = (
    "raw_messages",
    "activity_segments",
    "mapping_decisions",
    "skill_mappings",
    "attributions",
    "evidence_events",
)


@pytest.fixture
def seeded(db_pool, registry, new_learner):
    return seed_p5_learner(db_pool, new_learner(), registry)


@pytest.fixture
def client(db_pool, verifier):
    return api_client(verifier, db_pool)


def model_runs(pool) -> int:
    return fetch(pool, "select count(*) from public.model_runs")[0][0]


def provenance(pool, learner) -> dict[str, int]:
    return {
        t: fetch(pool, f"select count(*) from public.{t} where learner_id = %s", learner)[0][0]  # noqa: S608
        for t in PROVENANCE_TABLES
    }


def ledger_row(pool, learner, skill):
    rows = fetch(
        pool,
        "select mastery_state::text, ledger_version, debt_eligible, debt_score, evidence_count "
        "from public.skill_ledger where learner_id = %s and skill_id = %s",
        learner,
        skill,
    )
    return rows[0] if rows else None


def active_recommendation(pool, learner, skill):
    return fetch(
        pool,
        "select type::text, reason_code, related_skill_id, priority from public.recommendations "
        "where learner_id = %s and skill_id = %s and state = 'ACTIVE'",
        learner,
        skill,
    )[0]


def post_feedback(client, token, body, key=None):
    return client.post(
        "/v1/feedback",
        json=body,
        headers={**auth(token), "Idempotency-Key": key or f"fb-{uuid4().hex}"},
    )


# --- Ledger + dashboard counts --------------------------------------------------------------


def test_ledger_overlays_the_seeded_states_and_unknown_has_no_mean(seeded, client, make_token):
    body = client.get(
        f"/v1/ledger?course_id={seeded.course_id}", headers=auth(make_token(seeded.learner_id))
    ).json()
    by_id = {s["skill_id"]: s for s in body["skills"]}
    # Topics are not assessable skills; the five skills are all listed.
    assert set(by_id) == {
        str(seeded.skills[k])
        for k in ("for_loops", "while_loops", "comprehensions", "variables", "indexing")
    }
    state = {
        k: by_id[str(seeded.skills[k])] for k in seeded.skills if str(seeded.skills[k]) in by_id
    }
    assert state["for_loops"]["mastery_state"] == "DEMONSTRATED"
    assert state["variables"]["mastery_state"] == "EMERGING"
    assert state["indexing"]["mastery_state"] == "DEVELOPING"
    # Unknown is not weak: no mean, no ledger row, no debt.
    unknown = state["while_loops"]
    assert unknown["mastery_state"] == "UNKNOWN" and unknown["mastery_mean"] is None
    assert unknown["ledger_version"] is None and unknown["debt_band"] == "NONE"
    # Repeated AI delegation without independent evidence: UNKNOWN mastery, eligible debt.
    comp = state["comprehensions"]
    assert comp["mastery_state"] == "UNKNOWN" and comp["mastery_mean"] is None
    assert comp["debt_eligible"] and comp["debt_actionable"] and comp["debt_band"] == "MODERATE"
    assert comp["debt_score"] == pytest.approx(20.0, abs=0.5)
    counts: dict[str, int] = {}
    for s in body["skills"]:
        counts[s["mastery_state"]] = counts.get(s["mastery_state"], 0) + 1
    assert counts == {"DEMONSTRATED": 1, "DEVELOPING": 1, "EMERGING": 1, "UNKNOWN": 2}


# --- Skill detail -----------------------------------------------------------------------------


def test_skill_without_a_ledger_row_is_unknown_not_zero(seeded, client, make_token):
    response = client.get(
        f"/v1/skills/{seeded.skills['while_loops']}", headers=auth(make_token(seeded.learner_id))
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ledger"]["mastery_state"] == "UNKNOWN"
    assert body["ledger"]["mastery_mean"] is None and body["ledger"]["ledger_version"] is None
    assert body["mastery"]["explanation_code"] == "NO_EVIDENCE"
    assert body["mastery"]["mastery_mean"] is None
    # Only the evidence gate is shown while UNKNOWN: no mean is judged.
    assert [g["code"] for g in body["mastery"]["gates"]] == ["ENOUGH_EVIDENCE"]
    assert (
        body["evidence"] == [] and body["debt"]["band"] == "NONE" and body["debt"]["factors"] == []
    )
    assert body["debt"]["eligibility_code"] == "NO_DELEGATION"
    assert body["recommendation"]["type"] == "NO_ACTION"
    assert body["recommendation"]["reason_code"] == "NOT_ENOUGH_EVIDENCE"
    assert body["courses"][0]["topic_name"].endswith("Loops and iteration")
    assert body["skill"]["node_kind"] == "SKILL" and body["skill"]["difficulty_band"] == 2


def test_demonstrated_skill_explains_every_state_input_and_traces_to_activity(
    db_pool, seeded, client, make_token
):
    token = make_token(seeded.learner_id)
    body = client.get(f"/v1/skills/{seeded.skills['for_loops']}", headers=auth(token)).json()
    mastery = body["mastery"]
    assert mastery["state"] == "DEMONSTRATED"
    assert mastery["explanation_code"] == "INDEPENDENT_EVIDENCE_SUPPORTS"
    assert mastery["has_independent_application"] is True
    assert {g["code"]: g["met"] for g in mastery["gates"]} == {
        "ENOUGH_EVIDENCE": True,
        "STRONG_RESULTS": True,
        "SUSTAINED_EVIDENCE": True,
        "INDEPENDENT_APPLICATION": True,
    }
    assert mastery["support"] == pytest.approx(3.15, abs=0.01)
    assert mastery["evidence_count"] == 4 and mastery["excluded_evidence_count"] == 1

    timeline = body["evidence"]
    assert len(timeline) == 5
    counted = [t for t in timeline if t["counts_toward_mastery"]]
    assert len(counted) == 4
    assert all(t["current_weight"] == pytest.approx(0.7875, abs=1e-4) for t in counted)
    excluded = next(t for t in timeline if t["event"]["excluded"])
    assert excluded["event"]["id"] == str(seeded.excluded_evidence_id)
    assert excluded["event"]["exclusion_reason"] == "LEARNER_DONT_COUNT"
    assert excluded["current_weight"] == 0 and excluded["counts_toward_mastery"] is False
    assert excluded["correction"]["action"] == "DONT_COUNT"
    assert excluded["correction"]["note"] == "A classmate typed this attempt."

    item = counted[0]
    event = item["event"]
    assert (event["evidence_type"], event["actor"], event["outcome_signal"]) == (
        "INDEPENDENT_APPLICATION",
        "STUDENT",
        "CORRECT",
    )
    assert event["mapping_confidence"] == 0.9 and event["attribution_confidence"] == 0.95
    assert event["evidence_confidence"] == 0.9 and item["reason_code"] == "STUDENT_WROTE_CODE"
    assert event["evidence_span"]["student"] in item["source"]["learner_message_preview"]
    # Provenance: the evidence traces back to the captured activity rows.
    activity = client.get(
        "/v1/activity?"
        + "&".join(f"raw_message_id={i}" for i in item["source"]["raw_message_ids"]),
        headers=auth(token),
    ).json()["items"]
    assert {a["id"] for a in activity} == set(item["source"]["raw_message_ids"])
    assert {a["role"] for a in activity} == {"user", "assistant"}
    chips = [m for a in activity for s in a["segments"] for m in s["mappings"]]
    assert [c["evidence_id"] for c in chips] == [event["id"]]
    # No prompt, policy snapshot or model output leaks into the explanation.
    text = json.dumps(body)
    assert "policy_snapshot" not in text and "prompt" not in text and '"output"' not in text
    assert body["recommendation"]["type"] == "NO_ACTION"
    assert body["recommendation"]["reason_code"] == "INDEPENDENT_EVIDENCE_SUFFICIENT"


def test_debt_is_explained_as_a_reliance_signal_with_factors(seeded, client, make_token):
    body = client.get(
        f"/v1/skills/{seeded.skills['comprehensions']}",
        headers=auth(make_token(seeded.learner_id)),
    ).json()
    debt = body["debt"]
    assert debt["eligible"] and debt["actionable"] and debt["band"] == "MODERATE"
    assert debt["eligibility_code"] == "ELIGIBLE" and debt["verification"] == "UNVERIFIED"
    assert debt["recent_delegation_count"] == 3 and debt["min_recent_delegations"] == 2
    assert [f["code"] for f in debt["factors"]] == [
        "DELEGATION_PRESSURE",
        "EVIDENCE_GAP",
        "IMPORTANCE",
        "CONFIDENCE",
        "VERIFICATION",
    ]
    levels = {f["code"]: f["level"] for f in debt["factors"]}
    assert levels["EVIDENCE_GAP"] == "HIGH" and levels["DELEGATION_PRESSURE"] == "HIGH"
    # AI performing the skill is never evidence of the learner's own mastery.
    assert body["mastery"]["state"] == "UNKNOWN"
    assert body["mastery"]["explanation_code"] == "NO_INDEPENDENT_PERFORMANCE"
    assert all(t["counts_toward_debt"] and not t["counts_toward_mastery"] for t in body["evidence"])
    assert {t["event"]["actor"] for t in body["evidence"]} == {"AI"}
    assert body["recommendation"]["type"] == "VERIFY"
    assert body["recommendation"]["reason_code"] == "REPEATED_DELEGATION_UNVERIFIED"


def test_prerequisite_gap_recommends_the_weak_prerequisite(seeded, client, make_token):
    body = client.get(
        f"/v1/skills/{seeded.skills['indexing']}", headers=auth(make_token(seeded.learner_id))
    ).json()
    assert body["mastery"]["state"] == "DEVELOPING"
    assert body["mastery"]["explanation_code"] == "MIXED_RESULTS"
    assert [(p["skill_id"], p["mastery_state"]) for p in body["prerequisites"]] == [
        (str(seeded.skills["variables"]), "EMERGING")
    ]
    rec = body["recommendation"]
    assert rec["type"] == "PREREQUISITE" and rec["reason_code"] == "PREREQUISITE_GAP"
    assert rec["related_skill_id"] == str(seeded.skills["variables"])
    assert rec["related_skill_name"].endswith("Assigning and updating variables")


def test_skill_detail_is_scoped_to_the_learner(seeded, client, make_token, new_learner):
    other = make_token(new_learner())
    mine = make_token(seeded.learner_id)
    assert (
        client.get(f"/v1/skills/{seeded.skills['for_loops']}", headers=auth(other)).status_code
        == 404
    )
    assert client.get(f"/v1/skills/{uuid4()}", headers=auth(mine)).status_code == 404
    # A topic is not an assessable skill.
    assert client.get(f"/v1/skills/{seeded.skills['loops']}", headers=auth(mine)).status_code == 404
    assert client.get("/v1/skills/not-a-uuid", headers=auth(mine)).status_code == 422


# --- Activity ---------------------------------------------------------------------------------


def test_activity_is_enriched_with_derived_intelligence(seeded, client, make_token, new_learner):
    items = client.get(
        "/v1/activity?limit=200", headers=auth(make_token(seeded.learner_id))
    ).json()["items"]
    assert len(items) == 26  # 13 turns x (question + answer)
    anchors = [i for i in items if i["segments"]]
    assert len(anchors) == 13 and all(a["role"] == "user" for a in anchors)
    for answer in (i for i in items if i["role"] == "assistant"):
        assert answer["segments"] == [] and answer["analyzed_in"] is not None
        assert answer["processing_state"] == "COMPLETED"
    assert all(a["analyzed_in"] == a["id"] for a in anchors)
    assert {a["processing_outcome"] for a in anchors} == {"EVIDENCE_RECORDED"}

    chips = [m for a in anchors for s in a["segments"] for m in s["mappings"]]
    assert len(chips) == 13 and {c["status"] for c in chips} == {"ACCEPTED"}
    segments = [s for a in anchors for s in a["segments"]]
    assert {(s["route"], s["mapping_outcome"]) for s in segments} == {("MAP", "MAPPED")}
    ai = [c for c in chips if c["actor"] == "AI"]
    assert len(ai) == 3 and {c["evidence_type"] for c in ai} == {"OBSERVATION"}
    excluded = [c for c in chips if c["excluded"]]
    assert [c["evidence_id"] for c in excluded] == [str(seeded.excluded_evidence_id)]
    assert excluded[0]["correction"]["action"] == "DONT_COUNT"
    assert excluded[0]["exclusion_reason"] == "LEARNER_DONT_COUNT"
    # Newest capture first.
    assert items[0]["captured_at"] >= items[-1]["captured_at"]

    other = client.get("/v1/activity", headers=auth(make_token(new_learner()))).json()
    assert other == {"items": [], "next_before": None}


def test_activity_pages_never_split_a_capture_batch(db_pool, client, make_token, new_learner):
    learner = new_learner()
    for n in range(3):
        ingest_turn(db_pool, learner, f"Question number {n} about loops?", f"Answer {n}.")
    token = make_token(learner)
    first = client.get("/v1/activity?limit=2", headers=auth(token)).json()
    assert len(first["items"]) == 2 and first["next_before"] is not None
    second = client.get(
        "/v1/activity", params={"limit": 2, "before": first["next_before"]}, headers=auth(token)
    ).json()
    third = client.get(
        "/v1/activity", params={"limit": 2, "before": second["next_before"]}, headers=auth(token)
    ).json()
    ids = [i["id"] for page in (first, second, third) for i in page["items"]]
    assert len(ids) == 6 and len(set(ids)) == 6 and third["next_before"] is None
    # Not analysed yet: plain capture rows with their job state.
    assert all(i["segments"] == [] and i["analyzed_in"] is None for i in first["items"])
    assert {i["processing_state"] for i in first["items"]} == {"PENDING"}


# --- Correction loop --------------------------------------------------------------------------


def test_dont_count_excludes_recomputes_and_refreshes_the_recommendation(
    db_pool, seeded, client, make_token
):
    learner, skill = seeded.learner_id, seeded.skills["for_loops"]
    token = make_token(learner)
    before = provenance(db_pool, learner)
    runs = model_runs(db_pool)
    state, version, *_ = ledger_row(db_pool, learner, skill)
    assert state == "DEMONSTRATED"
    target = seeded.evidence("for_loops")[0]

    response = post_feedback(
        client,
        token,
        {"action": "DONT_COUNT", "target_type": "EVIDENCE_EVENT", "target_id": str(target)},
        key="dont-count-1",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True and body["correlation_id"] == "dont-count-1"
    assert body["feedback"]["excluded_evidence_ids"] == [str(target)]
    assert body["feedback"]["recomputed_skill_ids"] == [str(skill)]
    assert body["feedback"]["skill_id"] == str(skill)
    # 3 independent applications left: support 2.36 < 3.0 -> DEVELOPING, practice.
    assert [e["mastery_state"] for e in body["ledger"]] == ["DEVELOPING"]
    rec = next(r for r in body["recommendations"] if r["skill_id"] == str(skill))
    assert (rec["type"], rec["reason_code"]) == ("PRACTICE", "DEVELOPING_NEEDS_PRACTICE")

    assert ledger_row(db_pool, learner, skill)[:2] == ("DEVELOPING", version + 1)
    history = fetch(
        db_pool,
        "select type::text, state::text from public.recommendations where learner_id = %s "
        "and skill_id = %s order by created_at",
        learner,
        skill,
    )
    # Seed: 5 attempts (DEVELOPING -> practice), its exclusion (DEMONSTRATED -> no action), now
    # this correction (DEVELOPING -> practice again). Each change is a new row; none is rewritten.
    assert history == [
        ("PRACTICE", "SUPERSEDED"),
        ("NO_ACTION", "SUPERSEDED"),
        ("PRACTICE", "ACTIVE"),
    ]
    # Provenance is kept: nothing deleted, the evidence is only marked excluded.
    assert provenance(db_pool, learner) == before
    assert fetch(
        db_pool,
        "select excluded, exclusion_reason from public.evidence_events where id = %s",
        target,
    ) == [(True, "LEARNER_DONT_COUNT")]
    detail = client.get(f"/v1/skills/{skill}", headers=auth(token)).json()
    assert detail["mastery"]["explanation_code"] == "NEEDS_MORE_EVIDENCE"
    assert detail["mastery"]["excluded_evidence_count"] == 2

    # Idempotency: a retry returns the original result and changes nothing.
    replay = post_feedback(
        client,
        token,
        {"action": "DONT_COUNT", "target_type": "EVIDENCE_EVENT", "target_id": str(target)},
        key="dont-count-1",
    )
    assert replay.status_code == 200 and replay.json()["created"] is False
    assert replay.json()["feedback"]["id"] == body["feedback"]["id"]
    # The same correction under a new key is the same correction.
    again = post_feedback(
        client,
        token,
        {"action": "DONT_COUNT", "target_type": "EVIDENCE_EVENT", "target_id": str(target)},
    )
    assert again.status_code == 200 and again.json()["feedback"]["id"] == body["feedback"]["id"]
    # The same key for a different request is a conflict.
    conflict = post_feedback(
        client,
        token,
        {
            "action": "EVALUATION",
            "target_type": "SKILL",
            "target_id": str(skill),
            "verdict": "AGREE",
        },
        key="dont-count-1",
    )
    assert conflict.status_code == 409
    assert ledger_row(db_pool, learner, skill)[1] == version + 1
    assert (
        fetch(db_pool, "select count(*) from public.feedback where user_id = %s", learner)[0][0]
        == 2
    )
    assert model_runs(db_pool) == runs


def test_wrong_skill_excludes_the_mapping_keeps_provenance_and_invents_no_skill(
    db_pool, seeded, client, make_token
):
    learner, skill = seeded.learner_id, seeded.skills["comprehensions"]
    token = make_token(learner)
    before = provenance(db_pool, learner)
    turns = seeded.turns["comprehensions"]

    first = post_feedback(
        client,
        token,
        {
            "action": "WRONG_SKILL",
            "target_type": "SKILL_MAPPING",
            "target_id": str(turns[0].mapping_id),
        },
    )
    assert first.status_code == 201
    assert first.json()["feedback"]["excluded_evidence_ids"] == [str(turns[0].evidence_id)]
    # Two delegations remain: still eligible, lower debt, still a verification.
    _, _, eligible, score, _ = ledger_row(db_pool, learner, skill)
    assert eligible and score == pytest.approx(16.0, abs=0.5)
    assert active_recommendation(db_pool, learner, skill)[0] == "VERIFY"

    second = post_feedback(
        client,
        token,
        {
            "action": "WRONG_SKILL",
            "target_type": "EVIDENCE_EVENT",
            "target_id": str(turns[1].evidence_id),
        },
    )
    assert second.status_code == 201
    # One delegation left: a single AI interaction never creates debt.
    _, _, eligible, score, _ = ledger_row(db_pool, learner, skill)
    assert (eligible, score) == (False, 0)
    assert active_recommendation(db_pool, learner, skill)[:2] == (
        "NO_ACTION",
        "NOT_ENOUGH_EVIDENCE",
    )
    rec = next(r for r in second.json()["recommendations"] if r["skill_id"] == str(skill))
    assert rec["type"] == "NO_ACTION" and rec["debt_band"] == "NONE"

    # Nothing deleted, no replacement mapping or skill invented.
    assert provenance(db_pool, learner) == before
    assert (
        fetch(
            db_pool,
            "select count(*) from public.skill_mappings where learner_id = %s and status = 'ACCEPTED'",
            learner,
        )[0][0]
        == 13
    )
    chips = {
        m["mapping_id"]: m
        for a in client.get("/v1/activity?limit=200", headers=auth(token)).json()["items"]
        for s in a["segments"]
        for m in s["mappings"]
    }
    for turn in turns[:2]:
        chip = chips[str(turn.mapping_id)]
        assert chip["excluded"] and chip["exclusion_reason"] == "LEARNER_WRONG_SKILL"
        assert chip["correction"]["action"] == "WRONG_SKILL"
        assert chip["skill_id"] == str(skill)  # the mapping row itself is untouched


def test_dont_count_of_a_whole_task_unit(db_pool, seeded, client, make_token):
    turn = seeded.turns["variables"][0]
    response = post_feedback(
        client,
        make_token(seeded.learner_id),
        {
            "action": "DONT_COUNT",
            "target_type": "ACTIVITY_SEGMENT",
            "target_id": str(turn.segment_id),
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["feedback"]["segment_id"] == str(turn.segment_id)
    assert body["feedback"]["mapping_id"] is None
    assert body["feedback"]["excluded_evidence_ids"] == [str(turn.evidence_id)]
    # One incorrect attempt left: support 0.675 < 1.0 -> UNKNOWN (not weak), no practice.
    assert [e["mastery_state"] for e in body["ledger"]] == ["UNKNOWN"]
    assert (
        active_recommendation(db_pool, seeded.learner_id, seeded.skills["variables"])[0]
        == "NO_ACTION"
    )
    # The dependent skill loses its prerequisite gap (an UNKNOWN prerequisite is never a gap).
    assert active_recommendation(db_pool, seeded.learner_id, seeded.skills["indexing"])[:2] == (
        "PRACTICE",
        "DEVELOPING_NEEDS_PRACTICE",
    )


def test_evaluation_never_changes_evidence(db_pool, seeded, client, make_token):
    learner, skill = seeded.learner_id, seeded.skills["for_loops"]
    version = ledger_row(db_pool, learner, skill)[1]
    excluded = fetch(
        db_pool,
        "select count(*) from public.evidence_events where learner_id = %s and excluded",
        learner,
    )[0][0]
    response = post_feedback(
        client,
        make_token(learner),
        {
            "action": "EVALUATION",
            "target_type": "SKILL",
            "target_id": str(skill),
            "verdict": "DISAGREE",
            "note": "  I think I only half understand this.  ",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["feedback"]["note"] == "I think I only half understand this."
    assert body["feedback"]["excluded_evidence_ids"] == [] and body["ledger"] == []
    assert ledger_row(db_pool, learner, skill)[1] == version
    assert (
        fetch(
            db_pool,
            "select count(*) from public.evidence_events where learner_id = %s and excluded",
            learner,
        )[0][0]
        == excluded
    )
    detail = client.get(f"/v1/skills/{skill}", headers=auth(make_token(learner))).json()
    assert [f["verdict"] for f in detail["feedback"] if f["action"] == "EVALUATION"] == ["DISAGREE"]


def test_feedback_is_authorized_and_validated(db_pool, seeded, client, make_token, new_learner):
    mine, theirs = make_token(seeded.learner_id), make_token(new_learner())
    target = seeded.evidence("indexing")[0]
    body = {"action": "DONT_COUNT", "target_type": "EVIDENCE_EVENT", "target_id": str(target)}
    assert post_feedback(client, theirs, body).status_code == 404
    assert post_feedback(client, mine, {**body, "target_id": str(uuid4())}).status_code == 404
    # No Idempotency-Key -> refused before anything is written.
    assert client.post("/v1/feedback", json=body, headers=auth(mine)).status_code == 422
    assert (
        post_feedback(
            client, mine, {**body, "action": "WRONG_SKILL", "target_type": "ACTIVITY_SEGMENT"}
        ).status_code
        == 422
    )
    assert (
        post_feedback(
            client, mine, {"action": "EVALUATION", "target_type": "SKILL", "target_id": str(target)}
        ).status_code
        == 422
    )
    assert post_feedback(client, mine, {**body, "note": "x" * 2001}).status_code == 422

    # Verification evidence (a P6 source) is not captured activity: not correctable. Since
    # migration 0008 it must name a real graded verification result.
    with db_pool.connection() as conn:
        result_id = graded_verification(
            conn, seeded.learner_id, seeded.skills["indexing"], grading_confidence=0.85
        )
    (verification,) = fetch(
        db_pool,
        """insert into public.evidence_events (
               learner_id, skill_id, source_type, source_id, raw_message_ids, evidence_type, actor,
               outcome_signal, outcome, difficulty, difficulty_multiplier, independence, base_weight,
               strength, mapping_confidence, attribution_confidence, grading_confidence,
               evidence_confidence, evidence_span, model_run_ids, qualification_reason,
               qualifier_version, policy_snapshot, occurred_at)
           values (%s, %s, 'VERIFICATION', %s, '{}', 'VERIFICATION', 'STUDENT', 'CORRECT', 1, 0.5,
                   1.0, 1.0, 1.5, 1.275, 1.0, 1.0, 0.85, 0.85, '{"response": "x"}', '{}', 'GRADED',
                   'verification/test-v1', '{}', now())
           returning id""",
        seeded.learner_id,
        seeded.skills["indexing"],
        result_id,
    )
    response = post_feedback(client, mine, {**body, "target_id": str(verification[0])})
    assert response.status_code == 422
    assert fetch(
        db_pool, "select excluded from public.evidence_events where id = %s", verification[0]
    ) == [(False,)]


def test_a_correction_holds_for_evidence_written_later(db_pool, seeded, client, make_token):
    """A job deferred before attribution finishes after the learner corrected the mapping."""
    learner, skill = seeded.learner_id, seeded.skills["while_loops"]
    with db_pool.connection() as conn:
        policy = load_policy(conn)
        pending = seed_turn(
            conn,
            learner,
            skill_id=skill,
            user_text="I wrote this myself: `while n > 0: n -= 1`. Is it right?",
            assistant_text="Yes.",
            occurred_at=seeded_now(db_pool),
            policy=policy,
            student_span="while n > 0: n -= 1",
            attribute=False,
        )
    response = post_feedback(
        client,
        make_token(learner),
        {
            "action": "WRONG_SKILL",
            "target_type": "SKILL_MAPPING",
            "target_id": str(pending.mapping_id),
        },
    )
    assert (
        response.status_code == 201 and response.json()["feedback"]["excluded_evidence_ids"] == []
    )
    with db_pool.connection() as conn:
        finish_turn(conn, pending, policy)  # the resumed evidence stage
        rebuild_ledger(conn, learner, policy=policy)
    assert fetch(
        db_pool,
        "select excluded, exclusion_reason from public.evidence_events where id = %s",
        pending.evidence_id,
    ) == [(True, "LEARNER_WRONG_SKILL")]
    state, _, _, _, evidence_count = ledger_row(db_pool, learner, skill)
    assert (state, evidence_count) == ("UNKNOWN", 0)


def seeded_now(pool):
    return fetch(pool, "select now()")[0][0]


# --- Recommendations --------------------------------------------------------------------------


def test_recommendation_queue_is_deterministic_and_scoped(
    db_pool, seeded, client, make_token, new_learner
):
    token = make_token(seeded.learner_id)
    body = client.get("/v1/recommendations", headers=auth(token)).json()
    assert body["algorithm_version"] == "recommendations/p6-v1"
    queue = [
        (r["skill_id"], r["type"], r["reason_code"], r["priority"]) for r in body["recommendations"]
    ]
    assert queue == [
        (str(seeded.skills["comprehensions"]), "VERIFY", "REPEATED_DELEGATION_UNVERIFIED", 64),
        (str(seeded.skills["indexing"]), "PREREQUISITE", "PREREQUISITE_GAP", 55),
        (str(seeded.skills["variables"]), "PRACTICE", "EMERGING_NEEDS_PRACTICE", 45),
    ]
    assert body["recommendations"][0]["debt_band"] == "MODERATE"
    everything = client.get(
        f"/v1/recommendations?include_no_action=true&course_id={seeded.course_id}",
        headers=auth(token),
    ).json()["recommendations"]
    assert len(everything) == 5
    assert {r["type"] for r in everything if r["mastery_state"] == "UNKNOWN"} <= {
        "NO_ACTION",
        "VERIFY",
    }
    assert all(r["course_ids"] == [str(seeded.course_id)] for r in everything)

    # A refresh with an unchanged ledger writes nothing; a rebuild from scratch is identical.
    snapshot = fetch(
        db_pool,
        "select id, updated_at from public.recommendations where learner_id = %s order by id",
        seeded.learner_id,
    )
    with db_pool.connection() as conn:
        policy = load_policy(conn)
        refresh_recommendations(conn, seeded.learner_id, policy=policy)
    assert (
        fetch(
            db_pool,
            "select id, updated_at from public.recommendations where learner_id = %s order by id",
            seeded.learner_id,
        )
        == snapshot
    )
    with db_pool.connection() as conn:
        conn.execute(
            "delete from public.recommendations where learner_id = %s", (seeded.learner_id,)
        )
        refresh_recommendations(conn, seeded.learner_id, policy=policy)
    rebuilt = client.get("/v1/recommendations", headers=auth(token)).json()["recommendations"]
    assert [(r["skill_id"], r["type"], r["reason_code"], r["priority"]) for r in rebuilt] == queue

    other = make_token(new_learner())
    assert client.get("/v1/recommendations", headers=auth(other)).json()["recommendations"] == []
    assert (
        client.get(
            f"/v1/recommendations?course_id={seeded.course_id}", headers=auth(other)
        ).status_code
        == 404
    )


def test_the_whole_student_experience_makes_no_model_call(
    db_pool, registry, new_learner, client, make_token
):
    before = model_runs(db_pool)
    seed = seed_p5_learner(db_pool, new_learner(), registry)
    token = make_token(seed.learner_id)
    for path in (
        "/v1/ledger",
        f"/v1/ledger?course_id={seed.course_id}",
        "/v1/activity",
        "/v1/recommendations",
        *(f"/v1/skills/{sid}" for key, sid in seed.skills.items() if key not in ("loops", "data")),
    ):
        assert client.get(path, headers=auth(token)).status_code == 200, path
    assert (
        post_feedback(
            client,
            token,
            {
                "action": "WRONG_SKILL",
                "target_type": "SKILL_MAPPING",
                "target_id": str(seed.turns["indexing"][0].mapping_id),
            },
        ).status_code
        == 201
    )
    assert model_runs(db_pool) == before


def test_the_evidence_pipeline_refreshes_the_queue_after_the_ledger(db_pool, registry, new_learner):
    from app.intelligence.processing.pipeline import process_raw_message_job
    from app.jobs.queue import JOB_PROCESS_RAW_MESSAGE
    from tests.conftest import job_for
    from tests.test_evidence_pipeline_db import REPLY, STUDENT_TURN, mixed_turn_provider
    from tests.test_pipeline_db import bootstrapped_course, gateway_for

    learner = new_learner()
    bootstrapped_course(db_pool, learner, registry)
    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    process_raw_message_job(
        db_pool,
        gateway_for(db_pool, mixed_turn_provider(db_pool, registry)),
        job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE),
    )
    rows = fetch(
        db_pool,
        "select type::text, reason_code, mastery_state::text from public.recommendations "
        "where learner_id = %s and state = 'ACTIVE'",
        learner,
    )
    # Every course skill has its current action; one piece of evidence is not a judgement.
    assert len(rows) == 30
    assert set(rows) == {("NO_ACTION", "NOT_ENOUGH_EVIDENCE", "UNKNOWN")}


def learner_rows(pool, table: str, learner) -> int:
    sql = f"select count(*) from public.{table} where learner_id = %s"  # noqa: S608 - fixed names
    return fetch(pool, sql, learner)[0][0]


LEARNER_TABLES = (
    "conversations",
    "raw_messages",
    "processing_jobs",
    "activity_segments",
    "mapping_decisions",
    "skill_mappings",
    "attributions",
    "evidence_events",
    "skill_ledger",
    "recommendations",
)


def test_the_fixture_reuses_an_existing_course_graph_and_cascades_away(
    db_pool, registry, new_learner
):
    """The hosted acceptance path: existing canonical skills, learner-owned rows only."""
    from tests.p5_fixtures import ROLES, seed_graph, seed_p5_learner_on_course

    owner, learner = new_learner(), new_learner()
    with db_pool.connection() as conn, conn.transaction():
        course_id, skills = seed_graph(conn, owner, registry)
    registry_rows = fetch(
        db_pool,
        "select (select count(*) from public.skill_nodes), (select count(*) from public.skill_edges), "
        "(select count(*) from public.course_skills where course_id = %s)",
        course_id,
    )
    seed = seed_p5_learner_on_course(db_pool, learner, course_id, {k: skills[k] for k in ROLES})
    assert (
        fetch(
            db_pool,
            "select (select count(*) from public.skill_nodes), (select count(*) from public.skill_edges), "
            "(select count(*) from public.course_skills where course_id = %s)",
            course_id,
        )
        == registry_rows
    )  # no registry or course-overlay row created
    states = dict(
        fetch(
            db_pool,
            "select skill_id, mastery_state::text from public.skill_ledger where learner_id = %s",
            learner,
        )
    )
    assert states == {
        skills["for_loops"]: "DEMONSTRATED",
        skills["comprehensions"]: "UNKNOWN",
        skills["variables"]: "EMERGING",
        skills["indexing"]: "DEVELOPING",
    }
    assert seed.excluded_evidence_id is not None
    assert (
        fetch(db_pool, "select count(*) from public.evidence_events where learner_id = %s", owner)[
            0
        ][0]
        == 0
    )  # the course owner's data is untouched

    # Deleting the learner (the Auth cascade) removes every learner-owned row, nothing else.
    with db_pool.connection() as conn:
        conn.execute("delete from auth.users where id = %s", (learner,))
    assert {t: learner_rows(db_pool, t, learner) for t in LEARNER_TABLES} == dict.fromkeys(
        LEARNER_TABLES, 0
    )
    assert (
        fetch(db_pool, "select count(*) from public.feedback where user_id = %s", learner)[0][0]
        == 0
    )
    assert fetch(
        db_pool, "select user_id from public.course_memberships where course_id = %s", course_id
    ) == [(owner,)]
    assert (
        fetch(
            db_pool,
            "select (select count(*) from public.skill_nodes), (select count(*) from public.skill_edges), "
            "(select count(*) from public.course_skills where course_id = %s)",
            course_id,
        )
        == registry_rows
    )
    with pytest.raises(ValueError, match="PREREQUISITE"):
        swapped = {k: skills[k] for k in ROLES} | {
            "variables": skills["indexing"],
            "indexing": skills["variables"],
        }
        seed_p5_learner_on_course(db_pool, new_learner(), course_id, swapped)
