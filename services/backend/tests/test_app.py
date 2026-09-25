import importlib


def test_application_module_imports() -> None:
    module = importlib.import_module("app.main")
    assert module.app.title == "SkillMirror API"


def test_only_the_published_phase_routes_exist(client) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    # P1 ingestion + P2 courses + P4 ledger + P5 skill detail, activity, feedback and
    # recommendations + P6 verification + P7 me, teacher and admin.
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
        "/v1/me",
        "/v1/teacher/courses",
        "/v1/teacher/courses/{course_id}/overview",
        "/v1/admin/overview",
        "/v1/admin/jobs",
        "/v1/admin/jobs/{job_id}",
        "/v1/admin/jobs/{job_id}/retry",
        "/v1/admin/model-runs",
        "/v1/admin/skill-candidates",
        "/v1/admin/skill-candidates/{candidate_id}/review",
        "/v1/admin/benchmark",
        "/v1/admin/benchmark/{run_id}",
        "/v1/admin/courses",
        "/v1/admin/courses/{course_id}",
        "/v1/admin/courses/{course_id}/members",
        "/v1/admin/skills",
        "/v1/admin/skills/{skill_id}",
    }
