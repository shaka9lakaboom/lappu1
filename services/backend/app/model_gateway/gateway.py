"""ModelGateway: the single abstraction over generative and embedding models (§14.2).

Every intelligence engine calls this class; none imports a provider SDK. Each
provider call produces exactly one `model_runs` row with task, provider, model,
prompt version, input hash, token usage, latency and status.

Structured output is parsed and validated strictly against a Pydantic model
(plus an optional semantic validator). Invalid output gets exactly one repair
attempt; if that is invalid too, `ModelOutputInvalidError` is raised and the
caller abstains (§16 "Structured output invalid").

Free-tier execution (ADR 0004):

* Routing: the task type picks the model (`ModelRoutingPolicy`).
* Exact result cache: an identical request (provider, model, task type, prompt
  version, input hash) with a validated earlier output is served with ZERO
  provider requests. The hit is still a `model_runs` row, linked to the run that
  produced the output (`cache_source_run_id`). Failures are never cached.
* Request budget: a provider request is only sent while the model's daily budget
  (quota minus the safety reserve) has room; otherwise a transient
  `ModelBudgetExhaustedError` is raised before anything is sent.
* No retry loops: a provider error (429/503 included) is raised at once for the
  worker to defer; the only second request is the single repair of invalid output.
"""

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from app.model_gateway.budget import BudgetStatus, RequestBudget
from app.model_gateway.cache import CachedResult, CacheKey, ResultCache
from app.model_gateway.recorder import ModelRunRecorder
from app.model_gateway.routing import ModelRoutingPolicy
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
        routine_model: str | None = None,
        default_timeout: float = 60.0,
        clock: Callable[[], float] = time.perf_counter,
        generation_rpm: int = 0,
        embedding_rpm: int = 0,
        result_cache: ResultCache | None = None,
        embedding_cache: ResultCache | None = None,
        budget: RequestBudget | None = None,
        limiter_factory: Callable[[int], "RequestRateLimiter"] | None = None,
    ) -> None:
        self._provider = provider
        self._recorder = recorder
        self.routing = ModelRoutingPolicy(generation_model, routine_model)
        self.generation_model = generation_model
        self.embedding_model = embedding_model
        self._default_timeout = default_timeout
        self._clock = clock
        self._rpm = {"generation": generation_rpm, "embedding": embedding_rpm}
        self._limiter_factory = limiter_factory or RequestRateLimiter
        self._limiters: dict[str, RequestRateLimiter] = {}
        self._limiters_lock = threading.Lock()
        self._result_cache = result_cache
        self._embedding_cache = embedding_cache
        self.budget = budget

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def budget_status(self) -> list[BudgetStatus]:
        """Today's request budget of every budgeted model this gateway can call."""
        if self.budget is None:
            return []
        models = [*self.routing.generation_models, self.embedding_model]
        statuses = (self.budget.status(self._provider.name, m) for m in dict.fromkeys(models))
        return [s for s in statuses if s is not None]

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
        model = preferred_model or self.routing.model_for(task_type)
        schema = provider_json_schema(response_model)
        system = "\n\n".join(m.content for m in messages if m.role == "system") or None
        conversation = [m for m in messages if m.role != "system"]
        key = CacheKey(
            self._provider.name,
            model,
            task_type,
            prompt_version,
            _generation_input_hash(model, prompt_version, system, conversation, schema),
        )
        if self._result_cache is not None:
            cached = self._serve_cached(self._result_cache, key, response_model, validator, context)
            if cached is not None:
                return cached

        runs: list[ModelRun] = []
        first = self._generate_once(
            key, system, conversation, schema, response_model, context, timeout, validator,
            attempt=1, repair_of=None, runs=runs,
        )  # fmt: skip
        if isinstance(first, _Parsed):
            self._remember(key, runs[-1])
            return StructuredResult(parsed=first.value, run=runs[-1], runs=tuple(runs))

        repair_messages = [
            *conversation,
            Message("assistant", first.raw_text[:_MAX_REPAIR_ECHO_CHARS] or "(empty)"),
            Message("user", REPAIR_INSTRUCTION.format(errors=first.errors)),
        ]
        second = self._generate_once(
            key, system, repair_messages, schema, response_model, context, timeout, validator,
            attempt=2, repair_of=runs[-1], runs=runs,
        )  # fmt: skip
        if isinstance(second, _Parsed):
            # The repaired output answers the ORIGINAL request, so it is cached under its key.
            self._remember(key, runs[-1])
            return StructuredResult(parsed=second.value, run=runs[-1], runs=tuple(runs))
        logger.warning(
            "model output invalid after repair task=%s prompt=%s trace=%s",
            key.task_type,
            key.prompt_version,
            context.trace_id,
        )
        raise ModelOutputInvalidError(
            f"{key.task_type}: output invalid after one repair attempt", tuple(runs)
        )

    def _serve_cached[T: BaseModel](
        self,
        cache: ResultCache,
        key: CacheKey,
        response_model: type[T],
        validator: Callable[[T], None] | None,
        context: RunContext,
    ) -> StructuredResult[T] | None:
        started = self._clock()
        cached = cache.get(key)
        if cached is None or cached.output is None:
            return None
        # Served outputs pass the same strict validation as fresh ones.
        parsed, errors, _ = _validate(cached.output, response_model, validator)
        if parsed is None:
            logger.warning(
                "cached output fails current validation; calling the provider task=%s "
                "source_run=%s: %s",
                key.task_type,
                cached.source_run_id,
                errors[:200],
            )
            return None
        output = parsed.model_dump(mode="json")
        run = self._run(
            context, key.task_type, key.model, key.prompt_version, key.input_hash, started,
            status=ModelRunStatus.SUCCEEDED, output=output,
            output_hash=cached.output_hash or sha256_hex(_canonical_json(output)),
            cache_key=key.digest, cache_source_run_id=cached.source_run_id,
        )  # fmt: skip
        self._recorder.record(run)
        return StructuredResult(parsed=parsed, run=run, runs=(run,), cache_hit=True)  # type: ignore[arg-type]

    def _remember(self, key: CacheKey, run: ModelRun) -> None:
        if self._result_cache is not None and run.output is not None:
            result = CachedResult(run.id, output_hash=run.output_hash, output=run.output)
            self._result_cache.put(key, result)

    def _generate_once(
        self,
        key: CacheKey,
        system: str | None,
        conversation: list[Message],
        schema: dict[str, Any],
        response_model: type[BaseModel],
        context: RunContext,
        timeout: float | None,
        validator: Callable[[Any], None] | None,
        *,
        attempt: int,
        repair_of: ModelRun | None,
        runs: list[ModelRun],
    ) -> "_Parsed | _Invalid":
        task_type, model, prompt_version = key.task_type, key.model, key.prompt_version
        # The row records exactly what was sent (a repair has its own input hash).
        input_hash = (
            key.input_hash
            if attempt == 1
            else _generation_input_hash(model, prompt_version, system, conversation, schema)
        )
        with self._provider_slot(model, "generation"):
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
                usage=response.usage, output=output,
                output_hash=sha256_hex(_canonical_json(output)), cache_key=key.digest,
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
            key = CacheKey(
                self._provider.name,
                model,
                task_type,
                prompt_version,
                sha256_hex(
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
                ),
            )
            started = self._clock()
            cached = self._embedding_cache.get(key) if self._embedding_cache is not None else None
            if cached and cached.vectors is not None and len(cached.vectors) == len(chunk):
                run = self._run(
                    context, task_type, model, prompt_version, key.input_hash, started,
                    status=ModelRunStatus.SUCCEEDED, output_hash=cached.output_hash,
                    cache_key=key.digest, cache_source_run_id=cached.source_run_id,
                )  # fmt: skip
                runs.append(run)
                self._recorder.record(run)
                vectors.extend(list(v) for v in cached.vectors)
                vector_run_ids.extend([run.id] * len(cached.vectors))
                continue

            with self._provider_slot(model, "embedding"):
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
                        context, task_type, model, prompt_version, key.input_hash, started,
                        status=_status_for(exc), error_code=exc.code, error_message=str(exc),
                    )  # fmt: skip
                    runs.append(run)
                    self._recorder.record(run)
                    raise _gateway_error(exc, task_type, tuple(runs)) from exc

                problem = _check_vectors(response.vectors, len(chunk), output_dimension)
                if problem:
                    run = self._run(
                        context, task_type, model, prompt_version, key.input_hash, started,
                        status=ModelRunStatus.INVALID_OUTPUT, usage=response.usage,
                        error_code="EMBEDDING_SHAPE", error_message=problem,
                    )  # fmt: skip
                    runs.append(run)
                    self._recorder.record(run)
                    raise ModelOutputInvalidError(f"{task_type}: {problem}", tuple(runs))

                run = self._run(
                    context, task_type, model, prompt_version, key.input_hash, started,
                    status=ModelRunStatus.SUCCEEDED, usage=response.usage,
                    output_hash=sha256_hex(_canonical_json(response.vectors)), cache_key=key.digest,
                )  # fmt: skip
                runs.append(run)
                self._recorder.record(run)
            normalized = [_normalize(v) for v in response.vectors]
            if self._embedding_cache is not None:
                self._embedding_cache.put(
                    key,
                    CachedResult(
                        source_run_id=run.id,
                        output_hash=run.output_hash,
                        vectors=tuple(tuple(v) for v in normalized),
                    ),
                )
            vectors.extend(normalized)
            vector_run_ids.extend([run.id] * len(normalized))
        return EmbeddingResult(
            vectors=vectors, model=model, runs=tuple(runs), vector_run_ids=tuple(vector_run_ids)
        )

    # ------------------------------------------------------------------
    @contextmanager
    def _provider_slot(
        self, model: str, kind: Literal["generation", "embedding"]
    ) -> Iterator[None]:
        """Budget first (fail fast, nothing sent), then the per-model RPM limiter."""
        if self.budget is None:
            self._limiter(model, kind).acquire()
            yield
            return
        with self.budget.request(self._provider.name, model):
            self._limiter(model, kind).acquire()
            yield

    def _limiter(
        self, model: str, kind: Literal["generation", "embedding"]
    ) -> "RequestRateLimiter":
        # Free-tier rate limits are per model, so each model gets its own window.
        with self._limiters_lock:
            limiter = self._limiters.get(model)
            if limiter is None:
                limiter = self._limiters[model] = self._limiter_factory(self._rpm[kind])
            return limiter

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
        cache_key: str | None = None,
        cache_source_run_id: UUID | None = None,
    ) -> ModelRun:
        usage = usage or ProviderUsage()
        latency_ms = max(0, int(round((self._clock() - started) * 1000)))
        logger.info(
            "model_run task=%s model=%s prompt=%s status=%s attempt=%d ms=%d tokens=%s "
            "source=%s trace=%s",
            task_type,
            model,
            prompt_version,
            status.value,
            attempt,
            latency_ms,
            usage.total_tokens,
            f"cache:{cache_source_run_id}" if cache_source_run_id else "provider",
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
            # Never on a failure: only validated outputs are cacheable (migration 0004 check).
            cache_key=cache_key if status is ModelRunStatus.SUCCEEDED else None,
            cache_source_run_id=cache_source_run_id,
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


def _generation_input_hash(
    model: str,
    prompt_version: str,
    system: str | None,
    conversation: Sequence[Message],
    schema: dict[str, Any],
) -> str:
    return sha256_hex(
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
    return _validate(payload, response_model, validator)


def _validate(
    payload: Any, response_model: type[BaseModel], validator: SemanticValidator | None
) -> tuple[BaseModel | None, str, str]:
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
