"""Model gateway value types (architecture §14.2).

Engines never see provider SDK objects: they call `ModelGateway` and get back
validated Pydantic output plus the `ModelRun` records of every call made.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

# skill_embeddings.embedding is vector(768): the dimension is part of the schema.
EMBEDDING_DIMENSION = 768


class ModelRunStatus(StrEnum):
    """Mirrors the `public.model_run_status` enum."""

    SUCCEEDED = "SUCCEEDED"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class Message:
    """One chat message. Captured learner text only ever appears in `user` messages."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class RunContext:
    """Correlation for model_runs rows. `trace_id` ties every call of one job together."""

    trace_id: str
    learner_id: UUID | None = None
    course_id: UUID | None = None
    processing_job_id: UUID | None = None


@dataclass(frozen=True)
class ModelRun:
    """One row of `public.model_runs`."""

    id: UUID
    trace_id: str
    task_type: str
    provider: str
    model: str
    prompt_version: str
    input_hash: str
    status: ModelRunStatus
    latency_ms: int
    attempt: int = 1
    repair_of_id: UUID | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    output_hash: str | None = None
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    learner_id: UUID | None = None
    course_id: UUID | None = None
    processing_job_id: UUID | None = None


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    parsed: T
    run: ModelRun
    # Every call made for this result, including a failed first attempt before a repair.
    runs: tuple[ModelRun, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    runs: tuple[ModelRun, ...]
    # model_runs id of the provider call that produced each vector (same order).
    vector_run_ids: tuple[UUID, ...] = ()


# --- Provider boundary --------------------------------------------------------


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ProviderTextResponse:
    text: str
    usage: ProviderUsage = field(default_factory=ProviderUsage)


@dataclass(frozen=True)
class ProviderEmbeddingResponse:
    vectors: list[list[float]]
    usage: ProviderUsage = field(default_factory=ProviderUsage)


ProviderErrorKind = Literal["timeout", "rate_limited", "unavailable", "failed"]


class ProviderError(Exception):
    """Raised by provider adapters; the gateway maps it to a model_runs status."""

    def __init__(self, kind: ProviderErrorKind, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.kind = kind
        self.code = code


class ModelProviderProtocol(Protocol):
    """A generative + embedding provider adapter (Gemini today; local models later)."""

    name: str

    def generate_json(
        self,
        *,
        model: str,
        system: str | None,
        messages: Sequence[Message],
        json_schema: dict[str, Any],
        timeout: float,
    ) -> ProviderTextResponse: ...

    def embed(
        self,
        *,
        model: str,
        texts: Sequence[str],
        titles: Sequence[str | None] | None,
        input_type: Literal["query", "document"],
        output_dimension: int,
        timeout: float,
    ) -> ProviderEmbeddingResponse: ...


# --- Gateway errors -------------------------------------------------------------


class ModelGatewayError(Exception):
    """Base class. `runs` holds the model_runs records of the failed call(s)."""

    def __init__(self, message: str, runs: tuple[ModelRun, ...] = ()) -> None:
        super().__init__(message)
        self.runs = runs


class ModelUnavailableError(ModelGatewayError):
    """Not configured, credentials rejected, or provider unavailable. Retry later."""


class ModelTimeoutError(ModelGatewayError):
    pass


class ModelRateLimitedError(ModelGatewayError):
    pass


class ModelCallError(ModelGatewayError):
    pass


class ModelOutputInvalidError(ModelGatewayError):
    """Output failed validation twice (original + one repair). Callers abstain."""
