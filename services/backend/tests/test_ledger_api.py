"""GET /v1/ledger (architecture §13; ADR 0005): the learner's course skills joined with the
derived ledger. No evidence -> UNKNOWN with no mean; learners see only their own ledger."""

from uuid import uuid4

import pytest

from app.intelligence.processing.pipeline import process_raw_message_job
from app.jobs.queue import JOB_PROCESS_RAW_MESSAGE
from tests.conftest import ForbiddenPool, api_client, job_for
from tests.test_courses_api import auth
from tests.test_evidence_pipeline_db import (
    INDEX_SKILL,
    REPLY,
    STUDENT_TURN,
    mixed_turn_provider,
    skill_id,
)
from tests.test_pipeline_db import LOOP_SKILL, bootstrapped_course, gateway_for, ingest_turn


def test_the_ledger_requires_a_token(verifier) -> None:
    assert api_client(verifier, ForbiddenPool()).get("/v1/ledger").status_code == 401


def test_course_filter_must_be_a_uuid(verifier, make_token) -> None:
    client = api_client(verifier, ForbiddenPool())
    response = client.get("/v1/ledger?course_id=nope", headers=auth(make_token(uuid4())))
    assert response.status_code == 422


@pytest.mark.db
def test_ledger_joins_course_skills_with_derived_state(
    db_pool, registry, new_learner, verifier, make_token
) -> None:
    learner, other = new_learner(), new_learner()
    course_id = bootstrapped_course(db_pool, learner, registry)
    client = api_client(verifier, db_pool)

    # Before any evidence: every course skill is UNKNOWN, with no mean (unknown is not weak).
    empty = client.get("/v1/ledger", headers=auth(make_token(learner)))
    assert empty.status_code == 200
    skills = empty.json()["skills"]
    assert len(skills) == 30 and empty.json()["algorithm_version"] == "ledger/p8-v1"
    assert {s["mastery_state"] for s in skills} == {"UNKNOWN"}
    assert all(
        s["mastery_mean"] is None and s["support"] == 0 and s["debt_score"] == 0 for s in skills
    )
    assert all(s["course_ids"] == [str(course_id)] and s["importance"] == 0.5 for s in skills)

    _, assistant_id = ingest_turn(db_pool, learner, STUDENT_TURN, REPLY)
    process_raw_message_job(
        db_pool,
        gateway_for(db_pool, mixed_turn_provider(db_pool, registry)),
        job_for(db_pool, assistant_id, JOB_PROCESS_RAW_MESSAGE),
    )
    body = client.get(f"/v1/ledger?course_id={course_id}", headers=auth(make_token(learner))).json()
    by_id = {s["skill_id"]: s for s in body["skills"]}
    loop = by_id[str(skill_id(db_pool, registry, LOOP_SKILL))]
    index = by_id[str(skill_id(db_pool, registry, INDEX_SKILL))]
    assert body["skills"][0]["skill_id"] in {loop["skill_id"], index["skill_id"]}  # evidence first
    assert loop["mastery_state"] == "UNKNOWN" and loop["mastery_mean"] is None
    assert loop["support"] == pytest.approx(0.7875, rel=1e-5) and loop["evidence_count"] == 1
    assert loop["canonical_name"] == f"{registry}{LOOP_SKILL}" and loop["difficulty_band"] == 2
    assert loop["ledger_version"] == 1 and loop["computed_as_of"] is not None
    assert index["performance_evidence_count"] == 0 and index["recent_delegation_count"] == 1
    assert (index["debt_eligible"], index["debt_actionable"], index["debt_score"]) == (
        False,
        False,
        0,
    )

    # Another learner sees none of it, and cannot read this learner's course.
    theirs = client.get("/v1/ledger", headers=auth(make_token(other)))
    assert theirs.status_code == 200 and theirs.json()["skills"] == []
    assert (
        client.get(f"/v1/ledger?course_id={course_id}", headers=auth(make_token(other))).status_code
        == 404
    )
