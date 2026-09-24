"""Model gateway (architecture §14): the only path from SkillMirror to a model."""

from psycopg_pool import ConnectionPool

from app.core.config import Settings
from app.model_gateway.gateway import ModelGateway, RequestRateLimiter, sha256_hex
from app.model_gateway.recorder import DbModelRunRecorder, InMemoryRunRecorder, ModelRunRecorder
from app.model_gateway.types import (
    EMBEDDING_DIMENSION,
    EmbeddingResult,
    Message,
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
    "DbModelRunRecorder",
    "EmbeddingResult",
    "InMemoryRunRecorder",
    "Message",
    "ModelCallError",
    "ModelGateway",
    "ModelGatewayError",
    "ModelOutputInvalidError",
    "ModelRateLimitedError",
    "ModelRun",
    "ModelRunRecorder",
    "ModelRunStatus",
    "ModelTimeoutError",
    "ModelUnavailableError",
    "ProviderError",
    "RequestRateLimiter",
    "RunContext",
    "StructuredResult",
    "build_gateway",
    "sha256_hex",
]


def build_gateway(settings: Settings, pool: ConnectionPool) -> ModelGateway | None:
    """The configured production gateway, or None when no Google AI key is set."""
    if settings.gemini_api_key is None:
        return None
    from app.model_gateway.gemini import GeminiProvider

    provider = GeminiProvider(
        settings.gemini_api_key.get_secret_value(),
        thinking_level=settings.gemini_thinking_level,
    )
    return ModelGateway(
        provider,
        DbModelRunRecorder(pool),
        generation_model=settings.gemini_generation_model,
        embedding_model=settings.gemini_embedding_model,
        default_timeout=settings.model_timeout_seconds,
        generation_limiter=RequestRateLimiter(settings.gemini_generation_rpm),
        embedding_limiter=RequestRateLimiter(settings.gemini_embedding_rpm),
    )
