import importlib


def test_application_module_imports() -> None:
    module = importlib.import_module("app.main")
    assert module.app.title == "SkillMirror API"


def test_only_p1_routes_are_published(client) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    # P1 adds ingestion only; no intelligence endpoints exist yet.
    assert set(response.json()["paths"]) == {
        "/health",
        "/v1/events/batch",
        "/v1/events/sync-status",
    }
