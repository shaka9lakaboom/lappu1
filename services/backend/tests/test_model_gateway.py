"""ModelGateway (architecture §14): parsing, repair-once, failures, logging, embeddings."""

import json
import re
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel, ConfigDict, Field

from app.model_gateway import (
    EMBEDDING_DIMENSION,
    Message,
    ModelOutputInvalidError,
    ModelRateLimitedError,
    ModelRunStatus,
    ModelTimeoutError,
    ModelUnavailableError,
    ProviderError,
    RunContext,
    build_gateway,
)
from app.model_gateway.gemini import GeminiProvider
from app.model_gateway.schema import provider_json_schema
from tests.conftest import make_settings
from tests.fakes import FakeProvider, make_gateway

CTX = RunContext(trace_id="job:test-trace")


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    score: float = Field(ge=0, le=1)


class Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Answer]
    note: str | None


MESSAGES = [Message("system", "You label things."), Message("user", "Label <<<data>>>")]


def call(gateway, **kwargs):
    params = {
        "task_type": "TEST_TASK",
        "messages": MESSAGES,
        "response_model": Answer,
        "prompt_version": "test-task/v1",
        "context": CTX,
    }
    params.update(kwargs)
    return gateway.generate_structured(**params)


def scripted(*responses):
    queue = list(responses)
    return FakeProvider(lambda system, messages, schema: queue.pop(0))


def test_valid_output_is_parsed_and_logged() -> None:
    provider = scripted({"label": "ok", "score": 0.7})
    gateway, recorder = make_gateway(provider)
    result = call(gateway)
    assert result.parsed == Answer(label="ok", score=0.7)
    (run,) = recorder.runs
    assert result.run == run and result.runs == (run,)
    assert run.status is ModelRunStatus.SUCCEEDED
    assert (run.task_type, run.provider, run.model, run.prompt_version) == (
        "TEST_TASK",
        "fake",
        "gemini-3.7-flash",
        "test-task/v1",
    )
    assert re.fullmatch(r"[0-9a-f]{64}", run.input_hash)
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (11, 7, 18)
    assert run.latency_ms >= 0 and run.trace_id == "job:test-trace"
    assert run.output == {"label": "ok", "score": 0.7}
    # System messages become the provider's system instruction; data stays in user messages.
    assert provider.calls[0]["system"] == "You label things."
    assert [m.role for m in provider.calls[0]["messages"]] == ["user"]


def test_same_input_has_same_hash_and_prompt_version_changes_it() -> None:
    gateway, recorder = make_gateway(scripted(*[{"label": "a", "score": 0.1}] * 3))
    call(gateway)
    call(gateway)
    call(gateway, prompt_version="test-task/v2")
    hashes = [r.input_hash for r in recorder.runs]
    assert hashes[0] == hashes[1] != hashes[2]


def test_invalid_json_is_repaired_once() -> None:
    provider = scripted("not json at all", {"label": "fixed", "score": 0.5})
    gateway, recorder = make_gateway(provider)
    result = call(gateway)
    assert result.parsed.label == "fixed"
    first, second = recorder.runs
    assert first.status is ModelRunStatus.INVALID_OUTPUT and first.error_code == "JSON_PARSE"
    assert second.status is ModelRunStatus.SUCCEEDED
    assert second.attempt == 2 and second.repair_of_id == first.id
    repair_messages = provider.calls[1]["messages"]
    assert (
        repair_messages[-2].role == "assistant" and repair_messages[-2].content == "not json at all"
    )
    assert "rejected" in repair_messages[-1].content
    assert result.runs == (first, second)


def test_schema_violation_twice_raises_invalid_output() -> None:
    gateway, recorder = make_gateway(
        scripted({"label": "x", "score": 7}, {"label": "x", "score": 1.5})
    )
    with pytest.raises(ModelOutputInvalidError) as exc_info:
        call(gateway)
    assert [r.status for r in recorder.runs] == [ModelRunStatus.INVALID_OUTPUT] * 2
    assert all(r.error_code == "SCHEMA_VALIDATION" for r in recorder.runs)
    assert exc_info.value.runs == tuple(recorder.runs)


def test_validation_is_strict_no_extra_fields_no_coercion() -> None:
    gateway, recorder = make_gateway(
        scripted({"label": "x", "score": 0.2, "extra": 1}, {"label": "x", "score": "0.2"})
    )
    with pytest.raises(ModelOutputInvalidError):
        call(gateway)
    assert "extra" in recorder.runs[0].error_message
    assert "score" in recorder.runs[1].error_message


def test_semantic_validator_violation_triggers_repair() -> None:
    def validator(answer: Answer) -> None:
        if answer.label != "allowed":
            raise ValueError("label must be one of the supplied candidates")

    provider = scripted({"label": "invented", "score": 0.9}, {"label": "allowed", "score": 0.9})
    gateway, recorder = make_gateway(provider)
    assert call(gateway, validator=validator).parsed.label == "allowed"
    assert recorder.runs[0].error_code == "CONSTRAINT_VIOLATION"
    assert "supplied candidates" in provider.calls[1]["messages"][-1].content


def test_markdown_fenced_json_is_accepted() -> None:
    gateway, _ = make_gateway(scripted('```json\n{"label": "ok", "score": 0.3}\n```'))
    assert call(gateway).parsed.score == 0.3


@pytest.mark.parametrize(
    ("kind", "error", "status"),
    [
        ("timeout", ModelTimeoutError, ModelRunStatus.TIMEOUT),
        ("rate_limited", ModelRateLimitedError, ModelRunStatus.RATE_LIMITED),
        ("unavailable", ModelUnavailableError, ModelRunStatus.UNAVAILABLE),
    ],
)
def test_provider_failures_are_logged_and_raised(kind, error, status) -> None:
    gateway, recorder = make_gateway(scripted(ProviderError(kind, f"{kind.upper()}_CODE")))
    with pytest.raises(error):
        call(gateway)
    (run,) = recorder.runs
    assert run.status is status and run.error_code == f"{kind.upper()}_CODE"
    assert run.prompt_version == "test-task/v1"


@pytest.mark.parametrize("version", ["", "   ", "No Spaces Allowed", None])
def test_prompt_version_is_required(version) -> None:
    provider = scripted({"label": "x", "score": 0.1})
    gateway, recorder = make_gateway(provider)
    with pytest.raises(ValueError, match="prompt_version"):
        call(gateway, prompt_version=version)
    with pytest.raises(ValueError, match="prompt_version"):
        gateway.embed(texts=["x"], context=CTX, prompt_version=version)
    assert provider.calls == [] and provider.embed_calls == [] and recorder.runs == []


def test_provider_schema_is_inlined_and_simplified() -> None:
    schema = provider_json_schema(Nested)
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and "additionalProperties" not in text
    assert schema["properties"]["items"]["items"]["properties"]["score"]["maximum"] == 1
    assert schema["required"] == ["items", "note"]


def test_provider_schema_keeps_only_documented_keywords() -> None:
    from app.intelligence.skill_graph.schemas import GraphProposal

    text = json.dumps(provider_json_schema(GraphProposal))
    for keyword in ("pattern", "minLength", "maxLength", "title", "default", "uniqueItems"):
        assert f'"{keyword}"' not in text
    # Array bounds + enums trip Gemini's schema-complexity check (HTTP 400); Pydantic enforces them.
    assert '"maxItems"' not in text and '"minItems"' not in text
    assert '"enum"' in text and '"minimum"' in text


# --- Embeddings -----------------------------------------------------------------


def test_embeddings_are_768d_normalized_and_logged() -> None:
    provider = FakeProvider(embed_fn=lambda t: [2.0] * EMBEDDING_DIMENSION)
    gateway, recorder = make_gateway(provider)
    result = gateway.embed(
        texts=["a", "b"],
        context=CTX,
        prompt_version="skill-embedding-text/v1",
        task_type="EMBED_SKILLS",
    )
    assert len(result.vectors) == 2 and all(len(v) == EMBEDDING_DIMENSION for v in result.vectors)
    assert abs(sum(x * x for x in result.vectors[0]) - 1.0) < 1e-9
    (run,) = recorder.runs
    assert run.task_type == "EMBED_SKILLS" and run.model == "gemini-embedding-2"
    assert run.status is ModelRunStatus.SUCCEEDED and result.vector_run_ids == (run.id, run.id)


def test_wrong_embedding_dimension_is_invalid_output() -> None:
    gateway, recorder = make_gateway(FakeProvider(embed_fn=lambda t: [0.1] * 3072))
    with pytest.raises(ModelOutputInvalidError, match="dimension"):
        gateway.embed(texts=["a"], context=CTX, prompt_version="retrieval-query/v1")
    assert recorder.runs[0].status is ModelRunStatus.INVALID_OUTPUT
    with pytest.raises(ValueError, match="768"):
        gateway.embed(
            texts=["a"], context=CTX, prompt_version="retrieval-query/v1", output_dimension=3072
        )


def test_embedding_batches_are_chunked_one_run_per_call() -> None:
    provider = FakeProvider()
    gateway, recorder = make_gateway(provider)
    result = gateway.embed(
        texts=[f"skill {i}" for i in range(40)], context=CTX, prompt_version="x-in/v1"
    )
    assert len(provider.embed_calls) == 2 and len(recorder.runs) == 2
    assert len(result.vectors) == 40
    assert result.vector_run_ids[0] == recorder.runs[0].id
    assert result.vector_run_ids[-1] == recorder.runs[1].id


def test_embedding_provider_failure_is_logged() -> None:
    gateway, recorder = make_gateway(FakeProvider(embed_error=ProviderError("timeout", "TIMEOUT")))
    with pytest.raises(ModelTimeoutError):
        gateway.embed(texts=["a"], context=CTX, prompt_version="x-in/v1")
    assert recorder.runs[0].status is ModelRunStatus.TIMEOUT


# --- Gemini adapter (fake SDK client) -----------------------------------------


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.generate_args = None
        self.embed_args = None

    def generate_content(self, **kwargs):
        self.generate_args = kwargs
        if self.error:
            raise self.error
        return self.response

    def embed_content(self, **kwargs):
        self.embed_args = kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.5] * 768) for _ in kwargs["contents"]]
        )


def gemini(models: FakeModels) -> GeminiProvider:
    return GeminiProvider("unused-key", thinking_level="low", client=SimpleNamespace(models=models))


def test_gemini_adapter_requests_json_schema_output() -> None:
    usage = SimpleNamespace(
        prompt_token_count=100,
        candidates_token_count=20,
        thoughts_token_count=5,
        total_token_count=125,
    )
    models = FakeModels(
        response=SimpleNamespace(text='{"a": 1}', usage_metadata=usage, candidates=[])
    )
    out = gemini(models).generate_json(
        model="gemini-3.7-flash",
        system="sys",
        messages=[Message("user", "u"), Message("assistant", "prev"), Message("user", "fix")],
        json_schema={"type": "object"},
        timeout=12,
    )
    assert out.text == '{"a": 1}'
    assert (out.usage.input_tokens, out.usage.output_tokens, out.usage.total_tokens) == (
        100,
        25,
        125,
    )
    args = models.generate_args
    config = args["config"]
    assert args["model"] == "gemini-3.7-flash"
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == {"type": "object"}
    assert config.system_instruction == "sys"
    assert config.thinking_config.thinking_level.value == "LOW"
    assert config.http_options.timeout == 12000
    assert [c.role for c in args["contents"]] == ["user", "model", "user"]


def test_gemini_embedding_2_wraps_each_text_and_uses_task_prefixes() -> None:
    models = FakeModels()
    provider = gemini(models)
    out = provider.embed(
        model="gemini-embedding-2",
        texts=["for loops", "while loops"],
        titles=["For Loops", None],
        input_type="document",
        output_dimension=768,
        timeout=5,
    )
    assert len(out.vectors) == 2
    contents = models.embed_args["contents"]
    assert len(contents) == 2  # one Content per text => one embedding per text
    assert contents[0].parts[0].text == "title: For Loops | text: for loops"
    assert contents[1].parts[0].text == "title: none | text: while loops"
    assert models.embed_args["config"].output_dimensionality == 768
    provider.embed(
        model="gemini-embedding-2",
        texts=["q"],
        titles=None,
        input_type="query",
        output_dimension=768,
        timeout=5,
    )
    assert models.embed_args["contents"][0].parts[0].text == "task: search result | query: q"


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (genai_errors.ClientError(429, {"error": {"message": "quota"}}), "rate_limited"),
        (genai_errors.ClientError(403, {"error": {"message": "denied"}}), "unavailable"),
        (genai_errors.ClientError(400, {"error": {"message": "bad"}}), "failed"),
        (genai_errors.ServerError(503, {"error": {"message": "busy"}}), "unavailable"),
        (httpx.ReadTimeout("slow"), "timeout"),
    ],
)
def test_gemini_errors_map_to_provider_errors(error, kind) -> None:
    with pytest.raises(ProviderError) as exc_info:
        gemini(FakeModels(error=error)).generate_json(
            model="m", system=None, messages=[Message("user", "u")], json_schema={}, timeout=1
        )
    assert exc_info.value.kind == kind


def test_gateway_requires_server_side_key_and_never_exposes_it() -> None:
    assert build_gateway(make_settings(), pool=None) is None
    settings = make_settings(gemini_api_key="AIza-test-secret-value")
    assert "AIza-test-secret-value" not in repr(settings)
    assert "AIza-test-secret-value" not in str(settings.model_dump())


# --- Backpressure -------------------------------------------------------------


def test_rate_limit_and_overload_are_transient_with_retry_hint() -> None:
    gateway, _ = make_gateway(scripted(ProviderError("rate_limited", "HTTP_429", retry_after=27.0)))
    with pytest.raises(ModelRateLimitedError) as exc_info:
        call(gateway)
    assert exc_info.value.transient and exc_info.value.retry_after == 27.0

    gateway, _ = make_gateway(scripted(ProviderError("unavailable", "HTTP_503")))
    with pytest.raises(ModelUnavailableError) as exc_info:
        call(gateway)
    assert exc_info.value.transient

    gateway, _ = make_gateway(scripted(ProviderError("unavailable", "HTTP_403")))
    with pytest.raises(ModelUnavailableError) as exc_info:
        call(gateway)
    assert not exc_info.value.transient  # bad credentials are not backpressure


def test_gemini_429_retry_delay_is_parsed() -> None:
    error = genai_errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Quota exceeded. Please retry in 27.4s.",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "27s"}
                ],
            }
        },
    )
    with pytest.raises(ProviderError) as exc_info:
        gemini(FakeModels(error=error)).generate_json(
            model="m", system=None, messages=[Message("user", "u")], json_schema={}, timeout=1
        )
    assert exc_info.value.kind == "rate_limited" and exc_info.value.retry_after == 27.0


def test_request_rate_limiter_spaces_calls_within_a_minute() -> None:
    from app.model_gateway import RequestRateLimiter

    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = RequestRateLimiter(2, clock=lambda: now[0], sleep=sleep)
    assert limiter.acquire() == 0 and limiter.acquire() == 0
    now[0] = 10.0
    waited = limiter.acquire()  # third call in the same minute waits for the window
    assert waited == pytest.approx(50.05) and now[0] >= 60.0
    assert RequestRateLimiter(0).acquire() == 0.0
