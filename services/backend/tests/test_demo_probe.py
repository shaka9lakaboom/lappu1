"""P9 demo preflight probe (scripts/demo_probe.py config). No DB, no secret in the output."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demo_probe.py"
spec = importlib.util.spec_from_file_location("demo_probe", SCRIPT)
demo_probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_probe)

FAKE_KEY = "AIzaSy" + "Q" * 33
FAKE_DB = "postgresql://postgres.ref:super-secret-password@db.example.com:6543/postgres"
DEMO = {
    "APP_ENV": "development",
    "DATABASE_URL": FAKE_DB,
    "SUPABASE_URL": "https://project.supabase.co",
    "GEMINI_API_KEY": FAKE_KEY,
    "GEMINI_GENERATION_MODEL": "gemini-3.5-flash-lite",
    "GEMINI_ROUTINE_MODEL": "gemini-3.5-flash-lite",
    "GEMINI_GENERATION_RPM": "12",
    "MODEL_DAILY_REQUEST_LIMITS": "gemini-3.5-flash-lite=500,gemini-3.7-flash=20",
    "MODEL_QUOTA_RESERVE": "25",
    "WORKER_ENABLED": "true",
    "CORS_ORIGINS": "http://localhost:3000,chrome-extension://cohpimnabjigooghbigblennedbplojm",
}


@pytest.fixture
def demo_env(monkeypatch, tmp_path):
    # No services/backend/.env: only these variables count.
    monkeypatch.setattr(demo_probe, "BACKEND", tmp_path)
    for name, value in DEMO.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_the_demo_configuration_is_reported_by_names_and_flags_only(demo_env):
    report = demo_probe.config()
    assert report["ok"] is True
    assert report["worker_will_start"] is True
    assert (report["generation_model"], report["routine_model"], report["routing"]) == (
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash-lite",
        "free-tier",
    )
    assert report["daily_limits"]["gemini-3.5-flash-lite"] == 500
    assert report["gemini_api_key"] == "set" and report["database_url"] == "set"
    assert report["cors_allows_extension"] is True
    text = json.dumps(report)
    assert FAKE_KEY not in text and "super-secret-password" not in text


def test_a_rejected_configuration_names_the_problem_never_the_values(demo_env):
    demo_env.setenv("MODEL_DAILY_REQUEST_LIMITS", "gemini-3.7-flash=20")
    report = demo_probe.config()
    assert report["ok"] is False
    assert "no daily limit for gemini-3.5-flash-lite" in report["errors"][0]["message"]
    demo_env.setenv("MODEL_DAILY_REQUEST_LIMITS", "gemini-3.5-flash-lite=500")
    demo_env.setenv("GEMINI_GENERATION_RPM", "not-a-number")
    report = demo_probe.config()
    assert report["ok"] is False and report["errors"][0]["field"] == "gemini_generation_rpm"
    text = json.dumps(report)
    assert FAKE_KEY not in text and "super-secret-password" not in text


def test_the_worker_would_not_start_without_a_key(demo_env):
    demo_env.delenv("GEMINI_API_KEY")
    report = demo_probe.config()
    assert report["ok"] is True and report["worker_will_start"] is False
    assert report["gemini_api_key"] == "unset"
