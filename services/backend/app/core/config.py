"""Typed environment settings.

Values come from process environment variables, then from
services/backend/.env when present. Invalid configuration fails at startup
with a pydantic ValidationError that names the offending variable.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app import __version__

AppEnvironment = Literal["development", "test", "staging", "production"]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
# Free-tier quota observed on this project (GenerateRequestsPerDayPerProjectPerModel-FreeTier).
DEFAULT_DAILY_REQUEST_LIMITS = "gemini-3.7-flash=20,gemini-3.8-flash=20"
_ALLOWED_ORIGIN_SCHEMES = {"http", "https", "chrome-extension"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: AppEnvironment = "development"
    app_name: str = Field(default="skillmirror-backend", min_length=1)
    # Release version of this service, reported by GET /health. The URL
    # namespace is fixed at /v1 and is not configurable.
    api_version: str = Field(default=__version__, min_length=1)

    # Comma-separated list of exact origins, e.g.
    # "http://localhost:3000,chrome-extension://<extension-id>".
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Supabase. SUPABASE_URL is required to verify access tokens; the keys are
    # reserved for server-side database access from P1 onward.
    supabase_url: AnyHttpUrl | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_service_role_key: SecretStr | None = None
    # Only needed for projects still signing tokens with the legacy HS256
    # shared secret. Projects on asymmetric signing keys use JWKS instead.
    supabase_jwt_secret: SecretStr | None = None

    # PostgreSQL connection string for the system of record (architecture §7),
    # e.g. the Supabase session pooler URI. Server-side only; required for
    # ingestion. Without it, database-backed endpoints fail closed with 503.
    database_url: SecretStr | None = None

    # Google AI (architecture §14.1). Server-only; never sent to web or extension.
    # Without it the model gateway is unavailable and the worker stays idle.
    gemini_api_key: SecretStr | None = None
    gemini_generation_model: str = Field(default="gemini-3.7-flash", min_length=1, max_length=100)
    gemini_embedding_model: str = Field(default="gemini-embedding-2", min_length=1, max_length=100)
    # low | medium | high (gemini-3.7-flash does not accept "minimal").
    gemini_thinking_level: Literal["low", "medium", "high"] = "low"
    model_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    # Client-side request limits per minute and model (0 = off). The Gemini free tier
    # allows as little as 5 generation requests per minute per project and model.
    gemini_generation_rpm: int = Field(default=0, ge=0, le=100000)
    gemini_embedding_rpm: int = Field(default=0, ge=0, le=100000)

    # Hackathon free-tier routing policy (ADR 0004). Unset = the frozen architecture
    # default: every generation task on GEMINI_GENERATION_MODEL. Set (e.g.
    # gemini-3.5-flash-lite) = routine per-turn tasks use this model; course graph
    # bootstrap and ambiguous-mapping adjudication stay on GEMINI_GENERATION_MODEL.
    gemini_routine_model: str | None = Field(default=None, min_length=1, max_length=100)
    # Thinking level for the routine model only (default: GEMINI_THINKING_LEVEL).
    gemini_routine_thinking_level: Literal["minimal", "low", "medium", "high"] | None = None

    # Quota-aware request budget (ADR 0004): provider requests per day and model,
    # "model=limit,model=limit"; "off" disables it (blank keeps the default, the free-tier
    # quota observed on this project). A model that is not listed is not budgeted (its
    # 429s still defer jobs).
    model_daily_request_limits: str = DEFAULT_DAILY_REQUEST_LIMITS
    # Requests per model and day SkillMirror never spends on its own (safety reserve).
    model_quota_reserve: int = Field(default=2, ge=0, le=100000)
    # The provider's quota day (Gemini API: midnight Pacific).
    model_quota_timezone: str = "America/Los_Angeles"

    # Exact ModelGateway result cache (memory + durable model_runs tier).
    model_result_cache: bool = True
    model_result_cache_max_entries: int = Field(default=512, ge=0, le=100000)

    # P3A turn execution: "combined" = local retrieval first, then ONE structured call for
    # qualification + top-8 rerank + mapping (plus at most one adjudication); "staged" =
    # the original qualification -> rerank -> mapping calls.
    turn_analysis_mode: Literal["combined", "staged"] = "combined"

    # In-process durable worker loop (architecture §7.3). It starts with the API
    # when DATABASE_URL and GEMINI_API_KEY are set and APP_ENV is not "test".
    worker_enabled: bool = True
    worker_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    worker_batch_size: int = Field(default=4, ge=1, le=50)
    # PROCESSING jobs whose lock is older than this are recovered (crash recovery).
    worker_stale_after_seconds: int = Field(default=900, ge=60, le=86400)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _validate_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            if "*" in origin:
                raise ValueError(f"wildcard CORS origin not allowed: {origin!r}")
            parts = urlsplit(origin)
            if (
                parts.scheme not in _ALLOWED_ORIGIN_SCHEMES
                or not parts.netloc
                or parts.path
                or parts.query
                or parts.fragment
            ):
                raise ValueError(
                    f"invalid CORS origin {origin!r}: expected scheme://host[:port] "
                    "with no path or trailing slash"
                )
        return origins

    @field_validator(
        "supabase_url",
        "supabase_anon_key",
        "supabase_service_role_key",
        "supabase_jwt_secret",
        "database_url",
        "gemini_api_key",
        "gemini_routine_model",
        "gemini_routine_thinking_level",
        mode="before",
    )
    @classmethod
    def _empty_as_unset(cls, value: object) -> object:
        # `SUPABASE_URL=` copied verbatim from .env.example means "not set".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("model_daily_request_limits", mode="before")
    @classmethod
    def _blank_limits_keep_default(cls, value: object) -> object:
        # A blank line copied from .env.example must not silently switch the budget off.
        if isinstance(value, str) and not value.strip():
            return DEFAULT_DAILY_REQUEST_LIMITS
        return value

    @field_validator("model_daily_request_limits")
    @classmethod
    def _validate_limits(cls, value: str) -> str:
        from app.model_gateway.budget import parse_daily_limits

        parse_daily_limits(value)
        return value

    @field_validator("model_quota_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value

    @property
    def daily_request_limits(self) -> dict[str, int]:
        from app.model_gateway.budget import parse_daily_limits

        return parse_daily_limits(self.model_daily_request_limits)

    @model_validator(mode="after")
    def _require_deployed_config(self) -> "Settings":
        if self.app_env in ("staging", "production"):
            missing = [
                name
                for name, value in (
                    ("SUPABASE_URL", self.supabase_url),
                    ("CORS_ORIGINS", self.cors_origins),
                    ("DATABASE_URL", self.database_url),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"APP_ENV={self.app_env} requires: {', '.join(missing)}")
        return self

    @property
    def supabase_issuer(self) -> str | None:
        """Expected `iss` claim of Supabase access tokens."""
        if self.supabase_url is None:
            return None
        return f"{str(self.supabase_url).rstrip('/')}/auth/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
