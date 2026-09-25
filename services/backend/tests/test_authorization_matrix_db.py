"""The role half of the authorization matrix (ADR 0008): every teacher and admin route against a
STUDENT, a TEACHER and an ADMIN profile. The role is always the database's: a STUDENT whose
token claims ADMIN in its (client-writable) metadata is refused everywhere.

    route family        anonymous  student  teacher  admin
    /v1/me                 401       200      200     200
    /v1/teacher/courses    401       403      200     200
    /v1/admin/*            401       403      403     200 (404 for a missing id)
"""

from uuid import uuid4

import pytest

from tests.conftest import api_client
from tests.test_courses_api import auth
from tests.test_p7_unit import all_routes

pytestmark = pytest.mark.db


@pytest.fixture
def people(db_pool, new_learner):
    users = {role: new_learner() for role in ("STUDENT", "TEACHER", "ADMIN")}
    with db_pool.connection() as conn:
        for role, user in users.items():
            conn.execute("update public.profiles set role = %s where id = %s", (role, user))
    return users


def call(client, method, path, token):
    return client.request(
        method, path, json={}, headers={**auth(token), "Idempotency-Key": uuid4().hex}
    )


def test_teacher_and_admin_routes_follow_the_profile_role(
    db_pool, verifier, make_token, people
) -> None:
    client = api_client(verifier, db_pool)
    routes = [(m, p) for m, p in all_routes(client) if "/v1/admin" in p or "/v1/teacher" in p]
    assert len(routes) == 16  # 14 admin + 2 teacher
    student = make_token(people["STUDENT"])
    forged = make_token(
        people["STUDENT"], user_metadata={"role": "ADMIN"}, app_metadata={"role": "ADMIN"}
    )
    teacher = make_token(people["TEACHER"])
    admin = make_token(people["ADMIN"])
    for method, path in routes:
        assert call(client, method, path, student).status_code == 403, (method, path)
        assert call(client, method, path, forged).status_code == 403, (method, path)
        teacher_status = call(client, method, path, teacher).status_code
        admin_status = call(client, method, path, admin).status_code
        if "/v1/teacher" in path:
            # The overview of a course the teacher does not teach is a 404 (existence hidden).
            assert teacher_status == (404 if "overview" in path else 200), (path, teacher_status)
            assert admin_status == (404 if "overview" in path else 200), (path, admin_status)
        else:
            assert teacher_status == 403, (method, path)
            # Admin: allowed; ids in the path do not exist (404), bodies are empty (422).
            assert admin_status in (200, 404, 422), (method, path, admin_status)
            if method == "GET" and "0000-4000" not in path:
                assert admin_status == 200, (path, admin_status)
    for token, role in ((student, "STUDENT"), (forged, "STUDENT"), (admin, "ADMIN")):
        me = client.get("/v1/me", headers=auth(token))
        assert me.status_code == 200 and me.json()["role"] == role
