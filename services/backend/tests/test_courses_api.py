"""Course API (architecture §13): auth, validation, isolation, idempotency, async bootstrap."""

from uuid import uuid4

import pytest

from tests.conftest import ForbiddenPool, api_client


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Without a database ------------------------------------------------------


def test_courses_require_a_token(verifier) -> None:
    client = api_client(verifier, ForbiddenPool())
    assert client.get("/v1/courses").status_code == 401
    assert client.post("/v1/courses", json={"name": "X"}).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 201},
        {"name": "Ok", "owner_id": str(uuid4())},  # identity never comes from the body
        {"name": "Ok", "level": ""},
        {"name": "Ok", "description": "d" * 4001},
    ],
)
def test_invalid_course_bodies_are_rejected_before_storage(verifier, make_token, body) -> None:
    client = api_client(verifier, ForbiddenPool())
    response = client.post("/v1/courses", json=body, headers=auth(make_token(uuid4())))
    assert response.status_code == 422


def test_course_id_must_be_a_uuid(verifier, make_token) -> None:
    client = api_client(verifier, ForbiddenPool())
    assert (
        client.get("/v1/courses/not-a-uuid", headers=auth(make_token(uuid4()))).status_code == 422
    )


# --- With a database ---------------------------------------------------------


@pytest.mark.db
def test_create_course_enqueues_bootstrap_and_returns_immediately(
    db_pool, verifier, make_token, new_learner
) -> None:
    learner = new_learner()
    client = api_client(verifier, db_pool)
    response = client.post(
        "/v1/courses",
        json={"name": "  Introduction to Python Programming ", "level": "Beginner"},
        headers=auth(make_token(learner)),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    course = body["course"]
    assert body["created"] is True and body["correlation_id"]
    assert course["name"] == "Introduction to Python Programming"
    assert course["subject"] is None and course["level"] == "Beginner"
    assert course["graph_status"] == "PENDING" and course["graph_version"] == 0
    assert course["role"] == "STUDENT" and course["is_owner"] is True
    assert course["skill_count"] == 0 and course["bootstrap_job_state"] == "PENDING"

    with db_pool.connection() as conn:
        membership = conn.execute(
            "select role::text from public.course_memberships where course_id = %s and user_id = %s",
            (course["id"], learner),
        ).fetchone()
        job = conn.execute(
            "select id, entity_type, learner_id, state::text from public.processing_jobs "
            "where entity_id = %s and job_type = 'BOOTSTRAP_COURSE_GRAPH'",
            (course["id"],),
        ).fetchone()
        runs = conn.execute(
            "select count(*) from public.model_runs where course_id = %s", (course["id"],)
        ).fetchone()[0]
    assert membership == ("STUDENT",)
    assert str(job[0]) == body["bootstrap_job_id"]
    assert job[1:] == ("course", learner, "PENDING")
    assert runs == 0  # no model work inside the HTTP request


@pytest.mark.db
def test_list_get_and_skills_are_scoped_to_the_learner(
    db_pool, verifier, make_token, new_learner
) -> None:
    alice, bob = new_learner(), new_learner()
    client = api_client(verifier, db_pool)
    created = client.post(
        "/v1/courses", json={"name": "Alice's course"}, headers=auth(make_token(alice))
    )
    course_id = created.json()["course"]["id"]

    listed = client.get("/v1/courses", headers=auth(make_token(alice))).json()["courses"]
    assert [c["id"] for c in listed] == [course_id]
    assert (
        client.get(f"/v1/courses/{course_id}", headers=auth(make_token(alice))).status_code == 200
    )
    skills = client.get(f"/v1/courses/{course_id}/skills", headers=auth(make_token(alice))).json()
    assert skills == {
        "course_id": course_id,
        "graph_status": "PENDING",
        "graph_version": 0,
        "skills": [],
        "edges": [],
    }

    assert client.get("/v1/courses", headers=auth(make_token(bob))).json() == {"courses": []}
    assert client.get(f"/v1/courses/{course_id}", headers=auth(make_token(bob))).status_code == 404
    assert (
        client.get(f"/v1/courses/{course_id}/skills", headers=auth(make_token(bob))).status_code
        == 404
    )
    assert client.get(f"/v1/courses/{uuid4()}", headers=auth(make_token(alice))).status_code == 404


@pytest.mark.db
def test_idempotency_key_replays_the_same_course(
    db_pool, verifier, make_token, new_learner
) -> None:
    learner = new_learner()
    client = api_client(verifier, db_pool)
    headers = {**auth(make_token(learner)), "Idempotency-Key": "course-create-1"}
    first = client.post("/v1/courses", json={"name": "Stats 101"}, headers=headers)
    second = client.post("/v1/courses", json={"name": "Stats 101"}, headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["created"] is False and second.json()["bootstrap_job_id"] is None
    assert first.json()["course"]["id"] == second.json()["course"]["id"]
    with db_pool.connection() as conn:
        count = conn.execute(
            "select count(*) from public.courses where owner_id = %s", (learner,)
        ).fetchone()[0]
        jobs = conn.execute(
            "select count(*) from public.processing_jobs where learner_id = %s", (learner,)
        ).fetchone()[0]
    assert (count, jobs) == (1, 1)


@pytest.mark.db
def test_token_without_profile_is_refused(db_pool, verifier, make_token) -> None:
    client = api_client(verifier, db_pool)
    response = client.post("/v1/courses", json={"name": "Ghost"}, headers=auth(make_token(uuid4())))
    assert response.status_code == 403
