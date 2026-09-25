"""P7 teacher overview against PostgreSQL (ADR 0008): exact cohort aggregates, UNKNOWN as its own
neutral count, corrected / excluded items not counted, suppression below the minimum cohort, no
learner / debt / AI-usage fields, role-from-the-database authorization, and the N2 fix (a
TEACHER membership is never a learning context).

Three students are seeded identically by tests/p5_fixtures.py (production qualification, ledger
and recommendation code; no model call), so every per-skill count is known exactly:

    for_loops       DEMONSTRATED  4 independent applications each (+1 excluded by DONT_COUNT)
    while_loops     UNKNOWN       no ledger row
    comprehensions  UNKNOWN       3 AI observations each -> debt -> VERIFY recommendation
    variables       EMERGING      2 independent applications each
    indexing        DEVELOPING    3 independent applications each
"""

import json
from uuid import UUID, uuid4

import pytest

from app.experience.feedback import submit_feedback
from app.experience.models import FeedbackRequest
from app.intelligence.policy import load_policy
from app.intelligence.processing.pipeline import resolve_courses
from tests.conftest import api_client
from tests.p5_fixtures import ROLES, seed_p5_learner, seed_p5_learner_on_course
from tests.test_courses_api import auth
from tests.test_pipeline_db import fetch

pytestmark = pytest.mark.db

STATES = ("UNKNOWN", "EMERGING", "DEVELOPING", "DEMONSTRATED", "VERIFIED", "NEEDS_REVERIFICATION")


def set_role(pool, user: UUID, role: str) -> None:
    with pool.connection() as conn:
        conn.execute("update public.profiles set role = %s where id = %s", (role, user))


def enroll(pool, course: UUID, user: UUID, role: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "insert into public.course_memberships (course_id, user_id, role) values (%s, %s, %s)",
            (course, user, role),
        )


@pytest.fixture
def cohort(db_pool, registry, new_learner):
    """Three seeded students on one course, a TEACHER member and an ADMIN (not a member)."""
    first = new_learner()
    seed = seed_p5_learner(db_pool, first, registry)
    roles = {k: seed.skills[k] for k in ROLES}
    students = [first]
    for _ in range(2):
        learner = new_learner()
        seed_p5_learner_on_course(db_pool, learner, seed.course_id, roles)
        students.append(learner)
    teacher, admin, other_teacher = new_learner(), new_learner(), new_learner()
    set_role(db_pool, teacher, "TEACHER")
    set_role(db_pool, other_teacher, "TEACHER")
    set_role(db_pool, admin, "ADMIN")
    enroll(db_pool, seed.course_id, teacher, "TEACHER")
    return {
        "course": seed.course_id,
        "skills": roles,
        "students": students,
        "teacher": teacher,
        "other_teacher": other_teacher,
        "admin": admin,
        "seed": seed,
    }


@pytest.fixture
def client(db_pool, verifier):
    return api_client(verifier, db_pool)


def overview(client, make_token, user, course):
    return client.get(f"/v1/teacher/courses/{course}/overview", headers=auth(make_token(user)))


def by_skill(body: dict) -> dict[str, dict]:
    return {row["skill_id"]: row for row in body["skills"]}


def test_overview_is_an_exact_cohort_aggregate(db_pool, cohort, client, make_token) -> None:
    runs_before = fetch(db_pool, "select count(*) from public.model_runs")[0][0]
    listed = client.get("/v1/teacher/courses", headers=auth(make_token(cohort["teacher"])))
    assert listed.status_code == 200
    assert listed.json()["min_cohort"] == 3
    [course] = listed.json()["courses"]
    assert (course["id"], course["student_count"], course["skill_count"], course["suppressed"]) == (
        str(cohort["course"]),
        3,
        5,
        False,
    )

    response = overview(client, make_token, cohort["teacher"], cohort["course"])
    assert response.status_code == 200
    body = response.json()
    assert body["cohort"] == {"student_count": 3, "min_cohort": 3, "suppressed": False}
    skills = by_skill(body)
    expected = {
        "for_loops": "DEMONSTRATED",
        "while_loops": "UNKNOWN",
        "comprehensions": "UNKNOWN",
        "variables": "EMERGING",
        "indexing": "DEVELOPING",
    }
    assert set(skills) == {str(cohort["skills"][k]) for k in expected}
    for key, state in expected.items():
        row = skills[str(cohort["skills"][key])]
        assert set(row["states"]) == set(STATES)
        assert sum(row["states"].values()) == 3, key  # every student is counted once
        assert row["states"][state] == 3, (key, row["states"])
    # UNKNOWN is its own neutral count, not folded into a weakness measure.
    assert body["state_totals"] == {
        "UNKNOWN": 6,
        "EMERGING": 3,
        "DEVELOPING": 3,
        "DEMONSTRATED": 3,
        "VERIFIED": 0,
        "NEEDS_REVERIFICATION": 0,
    }
    evidence = {k: skills[str(cohort["skills"][k])]["students_with_evidence"] for k in expected}
    # Observations of the AI's work are not counted: comprehensions has none of the students' own.
    assert evidence == {
        "for_loops": 3,
        "while_loops": 0,
        "comprehensions": 0,
        "variables": 3,
        "indexing": 3,
    }
    # 4 + 2 + 3 independent applications per student; the excluded attempt is not counted.
    assert body["evidence_counts"] == {
        "window_days": 30,
        "independent": 27,
        "verification": 0,
        "students_with_evidence": 3,
        "window_independent": 27,
        "window_verification": 0,
    }
    assert body["verification_needs"] == [
        {
            "skill_id": str(cohort["skills"]["comprehensions"]),
            "name": skills[str(cohort["skills"]["comprehensions"])]["name"],
            "students": 3,
        }
    ]
    assert skills[str(cohort["skills"]["comprehensions"])]["verification_need_students"] == 3
    assert {m["skill_id"]: m["students"] for m in body["common_mapped_skills"]} == {
        str(cohort["skills"][k]): 3
        for k in ("for_loops", "comprehensions", "variables", "indexing")
    }
    assert body["as_of"] is not None

    # No learner identity, debt or AI-usage field anywhere in the payload.
    text = json.dumps(body)
    for user in [*cohort["students"], cohort["teacher"]]:
        assert str(user) not in text
    keys = set()

    def walk(value):
        if isinstance(value, dict):
            keys.update(value)
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(body)
    forbidden = {"learner_id", "user_id", "email", "display_name", "debt_score", "debt_band"}
    forbidden |= {"debt_eligible", "actor", "ai_span", "student_span", "mastery_mean"}
    assert not keys & forbidden
    assert fetch(db_pool, "select count(*) from public.model_runs")[0][0] == runs_before


def test_corrections_and_exclusions_are_not_counted(db_pool, cohort, client, make_token) -> None:
    student = cohort["students"][1]
    variables = cohort["skills"]["variables"]
    mappings = fetch(
        db_pool,
        "select id from public.skill_mappings where learner_id = %s and skill_id = %s",
        student,
        variables,
    )
    with db_pool.connection() as conn:
        policy = load_policy(conn)
        for (mapping_id,) in mappings:
            submit_feedback(
                conn,
                student,
                FeedbackRequest(
                    action="WRONG_SKILL", target_type="SKILL_MAPPING", target_id=mapping_id
                ),
                f"wrong-{mapping_id}",
                policy=policy,
            )
    body = overview(client, make_token, cohort["teacher"], cohort["course"]).json()
    row = by_skill(body)[str(variables)]
    assert row["states"]["EMERGING"] == 2 and row["states"]["UNKNOWN"] == 1
    assert sum(row["states"].values()) == 3
    assert row["students_with_evidence"] == 2
    assert body["evidence_counts"]["independent"] == 25
    mapped = {m["skill_id"]: m["students"] for m in body["common_mapped_skills"]}
    assert mapped[str(variables)] == 2


def test_a_cohort_below_the_minimum_is_suppressed(
    db_pool, registry, new_learner, client, make_token
) -> None:
    first = new_learner()
    seed = seed_p5_learner(db_pool, first, registry)
    second = new_learner()
    seed_p5_learner_on_course(db_pool, second, seed.course_id, {k: seed.skills[k] for k in ROLES})
    teacher = new_learner()
    set_role(db_pool, teacher, "TEACHER")
    enroll(db_pool, seed.course_id, teacher, "TEACHER")

    body = overview(client, make_token, teacher, seed.course_id).json()
    assert body["cohort"] == {"student_count": 2, "min_cohort": 3, "suppressed": True}
    assert body["state_totals"] is None and body["evidence_counts"] is None
    assert body["skills"] == [] and body["common_mapped_skills"] == []
    assert body["verification_needs"] == [] and body["as_of"] is None
    listed = client.get("/v1/teacher/courses", headers=auth(make_token(teacher))).json()
    assert listed["courses"][0]["suppressed"] is True


def test_authorization_uses_the_profile_role_and_the_membership(
    db_pool, cohort, client, make_token
) -> None:
    course = cohort["course"]
    student = cohort["students"][0]
    assert client.get(f"/v1/teacher/courses/{course}/overview").status_code == 401
    assert client.get("/v1/teacher/courses").status_code == 401
    # A student is refused, whatever the token claims about the role.
    for token in (
        make_token(student),
        make_token(student, user_metadata={"role": "ADMIN"}),
        make_token(student, app_metadata={"role": "TEACHER"}, role="service_role"),
    ):
        assert client.get("/v1/teacher/courses", headers=auth(token)).status_code == 403
        assert (
            client.get(f"/v1/teacher/courses/{course}/overview", headers=auth(token)).status_code
            == 403
        )
    # A teacher of other courses: 404, the course's existence is not revealed.
    assert overview(client, make_token, cohort["other_teacher"], course).status_code == 404
    assert overview(client, make_token, cohort["teacher"], uuid4()).status_code == 404
    listed = client.get("/v1/teacher/courses", headers=auth(make_token(cohort["other_teacher"])))
    assert listed.status_code == 200 and listed.json()["courses"] == []
    assert overview(client, make_token, cohort["teacher"], course).status_code == 200
    # An admin may open any course's aggregate overview.
    assert overview(client, make_token, cohort["admin"], course).status_code == 200
    me = client.get("/v1/me", headers=auth(make_token(cohort["teacher"]))).json()
    assert me["role"] == "TEACHER" and me["taught_course_count"] == 1
    assert me["capabilities"] == {"student": True, "teacher": True, "admin": False}
    forged = client.get(
        "/v1/me", headers=auth(make_token(student, user_metadata={"role": "ADMIN"}))
    ).json()
    assert forged["role"] == "STUDENT"
    assert forged["capabilities"] == {"student": True, "teacher": False, "admin": False}


def test_a_teacher_membership_is_never_a_learning_context(
    db_pool, cohort, client, make_token
) -> None:
    """N2: the courses a teacher teaches never become their own learning context."""
    teacher, course = cohort["teacher"], cohort["course"]
    headers = auth(make_token(teacher))
    assert client.get("/v1/courses", headers=headers).json()["courses"] == []
    assert client.get(f"/v1/courses/{course}", headers=headers).status_code == 404
    assert client.get("/v1/ledger", headers=headers).json()["skills"] == []
    assert client.get(f"/v1/ledger?course_id={course}", headers=headers).status_code == 404
    assert client.get(f"/v1/recommendations?course_id={course}", headers=headers).status_code == 404
    skill = cohort["skills"]["for_loops"]
    assert client.get(f"/v1/skills/{skill}", headers=headers).status_code == 404
    with db_pool.connection() as conn:
        # The pipeline maps the teacher's own captured turns against no taught course.
        assert resolve_courses(conn, teacher, None) == []
        assert resolve_courses(conn, teacher, course) == []
        assert [c.id for c in resolve_courses(conn, cohort["students"][0], course)] == [course]
    # A student's view of the same course is unchanged.
    student = auth(make_token(cohort["students"][0]))
    assert [c["id"] for c in client.get("/v1/courses", headers=student).json()["courses"]] == [
        str(course)
    ]
