import pytest
from pydantic import ValidationError

from tests.conftest import make_settings


def test_defaults_are_valid_for_local_development() -> None:
    settings = make_settings(app_env="development")
    assert settings.cors_origins == []
    assert settings.supabase_url is None
    assert settings.supabase_issuer is None


def test_cors_origins_are_parsed_from_comma_separated_string() -> None:
    settings = make_settings(
        cors_origins=" http://localhost:3000 , chrome-extension://abcdefghijklmnop ,"
    )
    assert settings.cors_origins == [
        "http://localhost:3000",
        "chrome-extension://abcdefghijklmnop",
    ]


@pytest.mark.parametrize(
    "origin",
    ["*", "https://*.example.com", "http://localhost:3000/", "localhost:3000", "ftp://x.test"],
)
def test_invalid_cors_origins_are_rejected(origin: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(cors_origins=origin)


def test_unknown_app_env_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(app_env="prod")


@pytest.mark.parametrize("env", ["staging", "production"])
def test_deployed_environments_require_supabase_cors_and_database(env: str) -> None:
    with pytest.raises(ValidationError, match="SUPABASE_URL, CORS_ORIGINS, DATABASE_URL"):
        make_settings(app_env=env)


def test_production_accepts_complete_configuration() -> None:
    settings = make_settings(
        app_env="production",
        supabase_url="https://abc.supabase.co",
        cors_origins="https://app.skillmirror.dev",
        database_url="postgresql://user:pw@db.example:5432/postgres",
    )
    assert settings.supabase_issuer == "https://abc.supabase.co/auth/v1"


def test_empty_supabase_values_mean_unset() -> None:
    settings = make_settings(supabase_url="", supabase_service_role_key="  ")
    assert settings.supabase_url is None
    assert settings.supabase_service_role_key is None


def test_invalid_supabase_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(supabase_url="not a url")


def test_secrets_are_not_exposed_in_repr() -> None:
    settings = make_settings(supabase_service_role_key="super-secret-value")
    assert "super-secret-value" not in repr(settings)


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_NAME", "skillmirror-api-staging")
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("CORS_ORIGINS", "https://staging.skillmirror.dev")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db.example:5432/postgres")
    settings = make_settings(app_env="staging")
    assert settings.app_name == "skillmirror-api-staging"
    assert settings.cors_origins == ["https://staging.skillmirror.dev"]
