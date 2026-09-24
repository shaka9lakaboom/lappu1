"""ModelGateway: the single abstraction over generative and embedding models (§14.2).

Every intelligence engine calls this class; none imports a provider SDK. Each
provider call produces exactly one `model_runs` row with task, provider, model,
prompt version, input hash, token usage, latency and status.

Structured output is parsed and validated strictly against a Pydantic model
(plus an optional semantic validator). Invalid output gets exactly one repair
attempt; if that is invalid too, `ModelOutputInvalidError` is raised and the
caller abstains (§16 "Structured output invalid").
"""

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from app.model_gateway.recorder import ModelRunRecorder
from app.model_gateway.schema import provider_json_schema
from app.model_gateway.types import (
    EMBEDDING_DIMENSION,
    EmbeddingResult,
    Message,
    ModelCallError,
    ModelGatewayError,
    ModelOutputInvalidError,
    ModelProviderProtocol,
    ModelRateLimitedError,
    ModelRun,
    ModelRunStatus,
    ModelTimeoutError,
    ModelUnavailableError,
    ProviderError,
    ProviderUsage,
    RunContext,
    StructuredResult,
)

logger = logging.getLogger("skillmirror.model_gateway")

_PROMPT_VERSION = re.compile(r"^[a-z0-9][a-z0-9._/-]{1,79}$")
_TASK_TYPE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_MAX_REPAIR_ECHO_CHARS = 8000
_MAX_ERROR_DETAIL_CHARS = 1500
# Embedding requests are chunked; each chunk is one provider call and one model_runs row.
EMBED_BATCH_SIZE = 32
EMBED_BATCH_MAX_CHARS = 20000

REPAIR_INSTRUCTION = (
    "Your previous response was rejected because it did not satisfy the required JSON "
    "schema or constraints.\nProblems:\n{errors}\n\nRespond again with only a corrected JSON "
    "object that satisfies the schema and every constraint. Do not add commentary."
)

SemanticValidator = Callable[[Any], None]


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


class ModelGateway:
    def __init__(
        self,
        provider: ModelProviderProtocol,
        recorder: ModelRunRecorder,
        *,
        generation_model: str,
        embedding_model: str,
        default_timeout: float = 60.0,
        clock: Callable[[], float] = time.perf_counter,
        generation_limiter: "RequestRateLimiter | None" = None,
        embedding_limiter: "RequestRateLimiter | None" = None,
    ) -> None:
        self._provider = provider
        self._recorder = recorder
        self.generation_model = generation_model
        self.embedding_model = embedding_model
        self._default_timeout = default_timeout
        self._clock = clock
        self._generation_limiter = generation_limiter or RequestRateLimiter(0)
        self._embedding_limiter = embedding_limiter or RequestRateLimiter(0)

    @property
    def provider_name(self) -> str:
        return self._provider.name

    # ------------------------------------------------------------------
    # Structured generation
    # ------------------------------------------------------------------
    def generate_structured[T: BaseModel](
        self,
        *,
        task_type: str,
        messages: Sequence[Message],
        response_model: type[T],
        prompt_version: str,
        context: RunContext,
        preferred_model: str | None = None,
        timeout: float | None = None,
        validator: Callable[[T], None] | None = None,
    ) -> StructuredResult[T]:
        _require_versioned(task_type, prompt_version)
        if not messages:
            raise ValueError("messages must not be empty")
        model = preferred_model or self.generation_model
        schema = provider_json_schema(response_model)
        system = "\n\n".join(m.content for m in messages if m.role == "system") or None
        conversation = [m for m in messages if m.role != "system"]
        runs: list[ModelRun] = []

        first = self._generate_once(
            task_type, model, system, conversation, schema, response_model, prompt_version,
            context, timeout, validator, attempt=1, repair_of=None, runs=runs,
        )  # fmt: skip
        if isinstance(first, _Parsed):
            return StructuredResult(parsed=first.value, run=runs[-1], runs=tuple(runs))

        repair_messages = [
            *conversation,
            Message("assistant", first.raw_text[:_MAX_REPAIR_ECHO_CHARS] or "(empty)"),
            Message("user", REPAIR_INSTRUCTION.format(errors=first.errors)),
        ]
        second = self._generate_once(
            task_type, model, system, repair_messages, schema, response_model, prompt_version,
            context, timeout, validator, attempt=2, repair_of=runs[-1], runs=runs,
        )  # fmt: skip
        if isinstance(second, _Parsed):
            return StructuredResult(parsed=second.value, run=runs[-1], runs=tuple(runs))
        logger.warning(
            "model output invalid after repair task=%s prompt=%s trace=%s",
            task_type,
            prompt_version,
            context.trace_id,
        )
        raise ModelOutputInvalidError(
            f"{task_type}: output invalid after one repair attempt", tuple(runs)
        )

    def _generate_once(
        self,
        task_type: str,
        model: str,
        system: str | None,
        conversation: list[Message],
        schema: dict[str, Any],
        response_model: type[BaseModel],
        prompt_version: str,
        context: RunContext,
        timeout: float | None,
        validator: Callable[[Any], None] | None,
        *,
        attempt: int,
        repair_of: ModelRun | None,
        runs: list[ModelRun],
    ) -> "_Parsed | _Invalid":
        input_hash = sha256_hex(
            _canonical_json(
                {
                    "model": model,
                    "prompt_version": prompt_version,
                    "system": system,
                    "messages": [[m.role, m.content] for m in conversation],
                    "schema": schema,
                }
            )
        )
        self._generation_limiter.acquire()
        started = self._clock()
        try:
            response = self._provider.generate_json(
                model=model,
                system=system,
                messages=conversation,
                json_schema=schema,
                timeout=timeout or self._default_timeout,
            )
        except ProviderError as exc:
            run = self._run(
                context, task_type, model, prompt_version, input_hash, started,
                status=_status_for(exc), attempt=attempt, repair_of=repair_of,
                error_code=exc.code, error_message=str(exc),
            )  # fmt: skip
            runs.append(run)
            self._recorder.record(run)
            raise _gateway_error(exc, task_type, tuple(runs)) from exc

        parsed, errors, error_code = _parse(response.text, response_model, validator)
        if parsed is None:
            run = self._run(
                context, task_type, model, prompt_version, input_hash, started,
                status=ModelRunStatus.INVALID_OUTPUT, attempt=attempt, repair_of=repair_of,
                usage=response.usage, error_code=error_code, error_message=errors,
                output_hash=sha256_hex(response.text),
            )  # fmt: skip
            runs.append(run)
            self._recorder.record(run)
            return _Invalid(raw_text=response.text, errors=errors)

        output = parsed.model_dump(mode="json")
        run = self._run(
            context, task_type, model, prompt_version, input_hash, started,
            status=ModelRunStatus.SUCCEEDED, attempt=attempt, repair_of=repair_of,
            usage=response.usage, output=output, output_hash=sha256_hex(_canonical_json(output)),
        )  # fmt: skip
        runs.append(run)
        self._recorder.record(run)
        return _Parsed(parsed)

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------
    def embed(
        self,
        *,
        texts: Sequence[str],
        context: RunContext,
        prompt_version: str,
        input_type: Literal["query", "document"] = "document",
        titles: Sequence[str | None] | None = None,
        embedding_model: str | None = None,
        output_dimension: int = EMBEDDING_DIMENSION,
        task_type: str = "EMBED_TEXT",
        timeout: float | None = None,
    ) -> EmbeddingResult:
        """Embed texts in order. `prompt_version` versions the input formatting."""
        _require_versioned(task_type, prompt_version)
        if output_dimension != EMBEDDING_DIMENSION:
            raise ValueError(f"output_dimension must be {EMBEDDING_DIMENSION} (schema-bound)")
        if titles is not None and len(titles) != len(texts):
            raise ValueError("titles must align with texts")
        if any(not t or not t.strip() for t in texts):
            raise ValueError("cannot embed empty text")
        model = embedding_model or self.embedding_model
        vectors: list[list[float]] = []
        vector_run_ids: list[UUID] = []
        runs: list[ModelRun] = []
        for start, end in _chunks(texts):
            chunk = list(texts[start:end])
            chunk_titles = list(titles[start:end]) if titles is not None else None
            input_hash = sha256_hex(
                _canonical_json(
                    {
                        "model": model,
                        "prompt_version": prompt_version,
                        "input_type": input_type,
                        "dimension": output_dimension,
                        "texts": chunk,
                        "titles": chunk_titles,
                    }
                )
            )
            self._embedding_limiter.acquire()
            started = self._clock()
            try:
                response = self._provider.embed(
                    model=model,
                    texts=chunk,
                    titles=chunk_titles,
                    input_type=input_type,
                    output_dimension=output_dimension,
                    timeout=timeout or self._default_timeout,
                )
            except ProviderError as exc:
                run = self._run(
                    context, task_type, model, prompt_version, input_hash, started,
                    status=_status_for(exc), error_code=exc.code, error_message=str(exc),
                )  # fmt: skip
                runs.append(run)
                self._recorder.record(run)
                raise _gateway_error(exc, task_type, tuple(runs)) from exc

            problem = _check_vectors(response.vectors, len(chunk), output_dimension)
            if problem:
                run = self._run(
                    context, task_type, model, prompt_version, input_hash, started,
                    status=ModelRunStatus.INVALID_OUTPUT, usage=response.usage,
                    error_code="EMBEDDING_SHAPE", error_message=problem,
                )  # fmt: skip
                runs.append(run)
                self._recorder.record(run)
                raise ModelOutputInvalidError(f"{task_type}: {problem}", tuple(runs))

            run = self._run(
                context, task_type, model, prompt_version, input_hash, started,
                status=ModelRunStatus.SUCCEEDED, usage=response.usage,
                output_hash=sha256_hex(_canonical_json(response.vectors)),
            )  # fmt: skip
            runs.append(run)
            self._recorder.record(run)
            vectors.extend(_normalize(v) for v in response.vectors)
            vector_run_ids.extend([run.id] * len(response.vectors))
        return EmbeddingResult(
            vectors=vectors, model=model, runs=tuple(runs), vector_run_ids=tuple(vector_run_ids)
        )

    # ------------------------------------------------------------------
    def _run(
        self,
        context: RunContext,
        task_type: str,
        model: str,
        prompt_version: str,
        input_hash: str,
        started: float,
        *,
        status: ModelRunStatus,
        attempt: int = 1,
        repair_of: ModelRun | None = None,
        usage: ProviderUsage | None = None,
        output: dict[str, Any] | None = None,
        output_hash: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ModelRun:
        usage = usage or ProviderUsage()
        latency_ms = max(0, int(round((self._clock() - started) * 1000)))
        logger.info(
            "model_run task=%s model=%s prompt=%s status=%s attempt=%d ms=%d tokens=%s trace=%s",
            task_type,
            model,
            prompt_version,
            status.value,
            attempt,
            latency_ms,
            usage.total_tokens,
            context.trace_id,
        )
        return ModelRun(
            id=uuid4(),
            trace_id=context.trace_id,
            task_type=task_type,
            provider=self._provider.name,
            model=model,
            prompt_version=prompt_version,
            input_hash=input_hash,
            status=status,
            latency_ms=latency_ms,
            attempt=attempt,
            repair_of_id=repair_of.id if repair_of else None,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            output_hash=output_hash,
            output=output,
            error_code=error_code,
            error_message=error_message[:2000] if error_message else None,
            learner_id=context.learner_id,
            course_id=context.course_id,
            processing_job_id=context.processing_job_id,
        )


class _Parsed:
    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value


class _Invalid:
    __slots__ = ("raw_text", "errors")

    def __init__(self, raw_text: str, errors: str) -> None:
        self.raw_text = raw_text
        self.errors = errors


def _require_versioned(task_type: str, prompt_version: str) -> None:
    if not prompt_version or not _PROMPT_VERSION.match(prompt_version):
        raise ValueError(
            "a model call requires a valid prompt_version (e.g. 'relevance-intent/v1')"
        )
    if not _TASK_TYPE.match(task_type):
        raise ValueError(f"invalid task_type {task_type!r}")


def _parse(
    text: str, response_model: type[BaseModel], validator: SemanticValidator | None
) -> tuple[BaseModel | None, str, str]:
    stripped = text.strip()
    # Some models wrap JSON in a markdown fence despite the JSON mime type.
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", stripped)
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"response is not valid JSON: {exc}"[:_MAX_ERROR_DETAIL_CHARS], "JSON_PARSE"
    try:
        parsed = response_model.model_validate(payload, strict=True)
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors(include_url=False, include_input=False)
        )
        return None, detail[:_MAX_ERROR_DETAIL_CHARS], "SCHEMA_VALIDATION"
    if validator is not None:
        try:
            validator(parsed)
        except ValueError as exc:
            return None, str(exc)[:_MAX_ERROR_DETAIL_CHARS], "CONSTRAINT_VIOLATION"
    return parsed, "", ""


def _status_for(exc: ProviderError) -> ModelRunStatus:
    return {
        "timeout": ModelRunStatus.TIMEOUT,
        "rate_limited": ModelRunStatus.RATE_LIMITED,
        "unavailable": ModelRunStatus.UNAVAILABLE,
    }.get(exc.kind, ModelRunStatus.FAILED)


# Provider backpressure: retry later without spending a job attempt.
_TRANSIENT_CODES = {"HTTP_429", "HTTP_503"}


def _gateway_error(
    exc: ProviderError, task_type: str, runs: tuple[ModelRun, ...]
) -> ModelGatewayError:
    cls = {
        "timeout": ModelTimeoutError,
        "rate_limited": ModelRateLimitedError,
        "unavailable": ModelUnavailableError,
    }.get(exc.kind, ModelCallError)
    return cls(
        f"{task_type}: provider {exc.kind} ({exc.code})",
        runs,
        transient=exc.kind == "rate_limited" or exc.code in _TRANSIENT_CODES,
        retry_after=exc.retry_after,
    )


class RequestRateLimiter:
    """Client-side sliding-window limit (requests per minute) for one model family.

    Keeps a free-tier quota (e.g. 5 RPM) from turning every burst into 429s.
    Thread-safe; `max_per_minute <= 0` disables it."""

    def __init__(
        self,
        max_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_per_minute = max_per_minute
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._sent: deque[float] = deque()

    def acquire(self) -> float:
        """Block until a request may be sent; returns the seconds waited."""
        if self.max_per_minute <= 0:
            return 0.0
        waited = 0.0
        with self._lock:
            while True:
                now = self._clock()
                while self._sent and now - self._sent[0] >= 60.0:
                    self._sent.popleft()
                if len(self._sent) < self.max_per_minute:
                    self._sent.append(now)
                    return waited
                delay = 60.0 - (now - self._sent[0]) + 0.05
                self._sleep(delay)
                waited += delay


def _chunks(texts: Sequence[str]) -> list[tuple[int, int]]:
    bounds: list[tuple[int, int]] = []
    start, chars = 0, 0
    for index, text in enumerate(texts):
        if index > start and (
            index - start >= EMBED_BATCH_SIZE or chars + len(text) > EMBED_BATCH_MAX_CHARS
        ):
            bounds.append((start, index))
            start, chars = index, 0
        chars += len(text)
    if start < len(texts):
        bounds.append((start, len(texts)))
    return bounds


def _check_vectors(vectors: list[list[float]], expected: int, dimension: int) -> str | None:
    if len(vectors) != expected:
        return f"expected {expected} embeddings, got {len(vectors)}"
    for index, vector in enumerate(vectors):
        if len(vector) != dimension:
            return f"embedding {index} has dimension {len(vector)}, expected {dimension}"
        if not all(math.isfinite(x) for x in vector):
            return f"embedding {index} contains non-finite values"
        if not any(vector):
            return f"embedding {index} is all zeros"
    return None


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]
