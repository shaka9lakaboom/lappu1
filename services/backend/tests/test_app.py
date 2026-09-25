import importlib


def test_application_module_imports() -> None:
    module = importlib.import_module("app.main")
    assert module.app.title == "SkillMirror API"


def test_only_the_published_phase_routes_exist(client) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    # P1 ingestion + P2 courses + P4 ledger + P5 skill detail, activity, feedback and
    # recommendations + P6 verification. Teacher and admin (P7) endpoints belong to a later phase.
    assert set(response.json()["paths"]) == {
        "/health",
        "/v1/events/batch",
        "/v1/events/sync-status",
        "/v1/courses",
        "/v1/courses/{course_id}",
        "/v1/courses/{course_id}/skills",
        "/v1/ledger",
        "/v1/skills/{skill_id}",
        "/v1/activity",
        "/v1/feedback",
        "/v1/recommendations",
        "/v1/verifications",
        "/v1/verifications/{session_id}",
        "/v1/verifications/{session_id}/start",
        "/v1/verifications/{session_id}/submit",
        "/v1/verifications/{session_id}/abandon",
    }
