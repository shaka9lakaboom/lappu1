import importlib


def test_application_module_imports() -> None:
    module = importlib.import_module("app.main")
    assert module.app.title == "SkillMirror API"


def test_only_p0_routes_are_published(client) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    # /health is the only P0 endpoint; the /v1 router exists but has no routes yet.
    assert set(response.json()["paths"]) == {"/health"}
