"""Model gateway (architecture §14): the only path from SkillMirror to a model."""

from psycopg_pool import ConnectionPool

from app.core.config import Settings
from app.model_gateway.budget import BudgetStatus, QuotaPolicy, RequestBudget
from app.model_gateway.cache import (
    CacheKey,
    DbResultCache,
    InMemoryResultCache,
    ResultCache,
    TieredResultCache,
)
from app.model_gateway.gateway import ModelGateway, RequestRateLimiter, sha256_hex
from app.model_gateway.recorder import (
    DbModelRunRecorder,
    InMemoryRunRecorder,
    ModelRunRecorder,
    ModelRunsSchemaError,
)
from app.model_gateway.routing import HIGH_VALUE_TASKS, ROUTINE_TASKS, ModelRoutingPolicy
from app.model_gateway.types import (
    EMBEDDING_DIMENSION,
    EmbeddingResult,
    Message,
    ModelBudgetExhaustedError,
    ModelCallError,
    ModelGatewayError,
    ModelOutputInvalidError,
    ModelRateLimitedError,
    ModelRun,
    ModelRunStatus,
    ModelTimeoutError,
    ModelUnavailableError,
    ProviderError,
    RunContext,
    StructuredResult,
)

__all__ = [
    "EMBEDDING_DIMENSION",
    "HIGH_VALUE_TASKS",
    "ROUTINE_TASKS",
    "BudgetStatus",
    "CacheKey",
    "DbModelRunRecorder",
    "DbResultCache",
    "EmbeddingResult",
    "InMemoryResultCache",
    "InMemoryRunRecorder",
    "Message",
    "ModelBudgetExhaustedError",
    "ModelCallError",
    "ModelGateway",
    "ModelGatewayError",
    "ModelOutputInvalidError",
    "ModelRateLimitedError",
    "ModelRoutingPolicy",
    "ModelRun",
    "ModelRunRecorder",
    "ModelRunStatus",
    "ModelRunsSchemaError",
    "ModelTimeoutError",
    "ModelUnavailableError",
    "ProviderError",
    "QuotaPolicy",
    "RequestBudget",
    "RequestRateLimiter",
    "ResultCache",
    "RunContext",
    "StructuredResult",
    "TieredResultCache",
    "build_gateway",
    "sha256_hex",
]


def build_gateway(settings: Settings, pool: ConnectionPool) -> ModelGateway | None:
    """The configured production gateway, or None when no Google AI key is set.

    Raises ModelRunsSchemaError if the database lacks migration 0004: every model_runs
    insert would fail, so no job may start against it."""
    if settings.gemini_api_key is None:
        return None
    from app.model_gateway.gemini import GeminiProvider

    routine = settings.gemini_routine_model
    thinking_levels = {}
    if routine and settings.gemini_routine_thinking_level:
        thinking_levels[routine] = settings.gemini_routine_thinking_level
    provider = GeminiProvider(
        settings.gemini_api_key.get_secret_value(),
        thinking_level=settings.gemini_thinking_level,
        thinking_levels=thinking_levels,
    )
    recorder = DbModelRunRecorder(pool)
    recorder.verify_schema()
    result_cache = embedding_cache = None
    if settings.model_result_cache:
        entries = settings.model_result_cache_max_entries
        result_cache = TieredResultCache(InMemoryResultCache(entries), DbResultCache(pool))
        embedding_cache = InMemoryResultCache(entries)
    budget = None
    if settings.daily_request_limits:
        budget = RequestBudget(
            QuotaPolicy(
                settings.daily_request_limits,
                reserve=settings.model_quota_reserve,
                timezone=settings.model_quota_timezone,
            ),
            recorder,
        )
    return ModelGateway(
        provider,
        recorder,
        generation_model=settings.gemini_generation_model,
        embedding_model=settings.gemini_embedding_model,
        routine_model=routine,
        default_timeout=settings.model_timeout_seconds,
        generation_rpm=settings.gemini_generation_rpm,
        embedding_rpm=settings.gemini_embedding_rpm,
        result_cache=result_cache,
        embedding_cache=embedding_cache,
        budget=budget,
    )
