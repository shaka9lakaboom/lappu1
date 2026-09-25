import importlib


def test_application_module_imports() -> None:
    module = importlib.import_module("app.main")
    assert module.app.title == "SkillMirror API"


def test_only_the_published_phase_routes_exist(client) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    # P1 ingestion + P2 courses + P4 ledger. Skill detail, activity, verification and feedback
    # endpoints belong to later phases.
    assert set(response.json()["paths"]) == {
        "/health",
        "/v1/events/batch",
        "/v1/events/sync-status",
        "/v1/courses",
        "/v1/courses/{course_id}",
        "/v1/courses/{course_id}/skills",
        "/v1/ledger",
    }
