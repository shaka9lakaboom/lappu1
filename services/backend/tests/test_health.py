from app.main import app


def test_app_imports_successfully():
    assert app is not None
    assert app.title == "SkillMirror Backend"


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "SkillMirror Backend"
    assert "environment" in data
    assert "version" in data
    assert "timestamp" in data


def test_api_v1_health_endpoint(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "SkillMirror Backend"
