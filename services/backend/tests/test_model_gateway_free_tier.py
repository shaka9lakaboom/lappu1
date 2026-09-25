"""ModelGateway free-tier execution (ADR 0004): exact result cache, request budget, routing.

Everything runs against the scripted fake provider: no real model is called.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.intelligence.attribution.engine import TASK_TYPE as ATTRIBUTION_TASK_TYPE
from app.intelligence.mapping.engine import ADJUDICATION_TASK_TYPE, MAPPING_TASK_TYPE
from app.intelligence.processing.turn_analysis import TASK_TYPE as TURN_TASK_TYPE
from app.intelligence.relevance.engine import TASK_TYPE as QUALIFICATION_TASK_TYPE
from app.intelligence.retrieval.rerank import TASK_TYPE as RERANK_TASK_TYPE
from app.intelligence.skill_graph.bootstrap import TASK_TYPE as GRAPH_TASK_TYPE
from app.intelligence.verification.evaluator import TASK_TYPE as VERIFICATION_EVALUATION_TASK_TYPE
from app.intelligence.verification.generator import TASK_TYPE as VERIFICATION_GENERATION_TASK_TYPE
from app.model_gateway import (
    HIGH_VALUE_TASKS,
    ROUTINE_TASKS,
    InMemoryResultCache,
    InMemoryRunRecorder,
    Message,
    ModelBudgetExhaustedError,
    ModelOutputInvalidError,
    ModelRateLimitedError,
    ModelRunsSchemaError,
    ModelRunStatus,
    ModelUnavailableError,
    ProviderError,
    QuotaPolicy,
    RequestBudget,
    RunContext,
)
from app.model_gateway.budget import parse_daily_limits
from app.model_gateway.recorder import DbModelRunRecorder
from tests.conftest import make_settings
from tests.fakes import FakeProvider, make_gateway

CTX = RunContext(trace_id="job:free-tier")
MESSAGES = [Message("system", "You label things."), Message("user", "Label <<<data>>>")]


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    score: float = Field(ge=0, le=1)


def scripted(*responses):
    queue = list(responses)
    return FakeProvider(lambda system, messages, schema: queue.pop(0))


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


def embed(gateway, texts=("loop over a list",)):
    return gateway.embed(
        texts=list(texts), context=CTX, prompt_version="retrieval-query/v1", task_type="EMBED_QUERY"
    )


# --- Exact result cache ---------------------------------------------------------


def test_identical_successful_inference_is_served_with_zero_provider_calls() -> None:
    provider = scripted({"label": "ok", "score": 0.7})
    gateway, recorder = make_gateway(provider, result_cache=InMemoryResultCache())
    first = call(gateway)
    second = call(gateway)
    assert len(provider.calls) == 1
    assert second.parsed == first.parsed and second.cache_hit and not first.cache_hit
    source, hit = recorder.runs
    # The hit is still a model_runs row, linked to the run that produced the output.
    assert hit.cache_source_run_id == source.id and not hit.provider_request
    assert source.provider_request and source.cache_source_run_id is None
    assert hit.cache_key == source.cache_key and hit.cache_key is not None
    assert (hit.input_hash, hit.output, hit.output_hash) == (
        source.input_hash,
        source.output,
        source.output_hash,
    )
    assert hit.status is ModelRunStatus.SUCCEEDED and hit.total_tokens is None
    assert hit.trace_id == CTX.trace_id and second.run == hit and second.runs == (hit,)


@pytest.mark.parametrize(
    "change",
    [
        {"prompt_version": "test-task/v2"},
        {"task_type": "OTHER_TASK"},
        {"preferred_model": "gemini-3.5-flash-lite"},
        {"messages": [Message("system", "You label things."), Message("user", "other data")]},
    ],
)
def test_cache_key_covers_model_task_prompt_version_and_input(change) -> None:
    provider = scripted(*[{"label": "ok", "score": 0.7}] * 2)
    gateway, recorder = make_gateway(provider, result_cache=InMemoryResultCache())
    call(gateway)
    call(gateway, **change)
    assert len(provider.calls) == 2 and all(r.provider_request for r in recorder.runs)


@pytest.mark.parametrize(
    "failure",
    [
        ProviderError("rate_limited", "HTTP_429", retry_after=30),
        ProviderError("unavailable", "HTTP_503"),
        ProviderError("failed", "HTTP_500"),
        ProviderError("timeout", "TIMEOUT"),
    ],
)
def test_provider_failures_are_never_cached(failure) -> None:
    provider = scripted(failure, {"label": "ok", "score": 0.7})
    cache = InMemoryResultCache()
    gateway, recorder = make_gateway(provider, result_cache=cache)
    with pytest.raises(Exception):  # noqa: B017 - each failure maps to its own gateway error
        call(gateway)
    assert len(cache) == 0 and recorder.runs[0].cache_key is None
    assert call(gateway).cache_hit is False
    assert len(provider.calls) == 2


def test_invalid_output_is_never_cached() -> None:
    provider = scripted("nope", "still nope", {"label": "ok", "score": 0.2})
    cache = InMemoryResultCache()
    gateway, recorder = make_gateway(provider, result_cache=cache)
    with pytest.raises(ModelOutputInvalidError):
        call(gateway)
    assert len(cache) == 0
    assert [r.cache_key for r in recorder.runs] == [None, None]
    assert not call(gateway).cache_hit and len(provider.calls) == 3


def test_repaired_output_is_cached_under_the_original_request() -> None:
    provider = scripted("not json", {"label": "fixed", "score": 0.5})
    gateway, recorder = make_gateway(provider, result_cache=InMemoryResultCache())
    call(gateway)
    again = call(gateway)
    assert again.cache_hit and again.parsed.label == "fixed" and len(provider.calls) == 2
    invalid, repair, hit = recorder.runs
    assert invalid.cache_key is None and repair.cache_key == hit.cache_key
    assert hit.cache_source_run_id == repair.id and hit.input_hash == invalid.input_hash


def test_cached_output_is_revalidated_and_ignored_if_it_no_longer_passes() -> None:
    provider = scripted({"label": "old", "score": 0.5}, {"label": "new", "score": 0.5})
    gateway, _ = make_gateway(provider, result_cache=InMemoryResultCache())
    call(gateway)

    def only_new(answer: Answer) -> None:
        if answer.label != "new":
            raise ValueError("label must be new")

    assert call(gateway, validator=only_new).parsed.label == "new"
    assert len(provider.calls) == 2


def test_identical_embedding_request_is_served_from_the_cache() -> None:
    provider = FakeProvider()
    gateway, recorder = make_gateway(provider, embedding_cache=InMemoryResultCache())
    first = embed(gateway)
    second = embed(gateway)
    assert len(provider.embed_calls) == 1
    assert second.vectors == first.vectors
    source, hit = recorder.runs
    assert hit.cache_source_run_id == source.id and second.vector_run_ids == (hit.id,)


def test_embedding_failure_is_never_cached() -> None:
    provider = FakeProvider(embed_error=ProviderError("rate_limited", "HTTP_429"))
    cache = InMemoryResultCache()
    gateway, recorder = make_gateway(provider, embedding_cache=cache)
    with pytest.raises(ModelRateLimitedError):
        embed(gateway)
    assert len(cache) == 0 and recorder.runs[0].cache_key is None


def test_cache_is_off_unless_configured() -> None:
    provider = scripted(*[{"label": "ok", "score": 0.7}] * 2)
    gateway, _ = make_gateway(provider)
    call(gateway)
    call(gateway)
    assert len(provider.calls) == 2


# --- No retry loop on backpressure -------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (ProviderError("rate_limited", "HTTP_429", retry_after=27), ModelRateLimitedError),
        (ProviderError("unavailable", "HTTP_503"), ModelUnavailableError),
    ],
)
def test_429_and_503_are_raised_after_one_request_for_the_worker_to_defer(failure, error) -> None:
    provider = scripted(failure, {"label": "never", "score": 0.1})
    gateway, recorder = make_gateway(provider)
    with pytest.raises(error) as exc_info:
        call(gateway)
    assert exc_info.value.transient
    assert len(provider.calls) == 1 and len(recorder.runs) == 1  # no immediate retry


# --- Request budget ----------------------------------------------------------------


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def budgeted(provider, limits, reserve, clock=None, **options):
    clock = clock or Clock(datetime(2026, 9, 25, 18, 0, tzinfo=UTC))
    recorder = InMemoryRunRecorder(clock=clock)
    budget = RequestBudget(QuotaPolicy(limits, reserve=reserve), recorder, now=clock)
    gateway, _ = make_gateway(provider, recorder, budget=budget, **options)
    return gateway, recorder, clock


def test_budget_keeps_the_reserve_and_refuses_before_sending() -> None:
    provider = scripted(*[{"label": "ok", "score": 0.5}] * 5)
    gateway, recorder, _ = budgeted(provider, {"gemini-3.7-flash": 4}, reserve=2)
    call(gateway, messages=[Message("user", "one")])
    call(gateway, messages=[Message("user", "two")])
    with pytest.raises(ModelBudgetExhaustedError) as exc_info:
        call(gateway, messages=[Message("user", "three")])
    assert exc_info.value.transient and exc_info.value.retry_after > 0
    assert "reserve 2" in str(exc_info.value)
    assert len(provider.calls) == 2 and len(recorder.runs) == 2  # nothing sent, nothing logged
    (status,) = gateway.budget_status()
    assert (status.used, status.limit, status.reserve, status.available) == (2, 4, 2, 0)


def test_cache_hits_do_not_spend_the_budget() -> None:
    provider = scripted({"label": "ok", "score": 0.5})
    gateway, recorder, _ = budgeted(
        provider, {"gemini-3.7-flash": 3}, reserve=2, result_cache=InMemoryResultCache()
    )
    call(gateway)
    for _ in range(3):
        assert call(gateway).cache_hit  # budget is spent, identical requests still served
    assert len(provider.calls) == 1 and len(recorder.provider_requests) == 1


def test_failed_requests_count_toward_the_budget() -> None:
    provider = scripted(ProviderError("unavailable", "HTTP_503"), {"label": "ok", "score": 0.5})
    gateway, _, _ = budgeted(provider, {"gemini-3.7-flash": 2}, reserve=1)
    with pytest.raises(ModelUnavailableError):
        call(gateway)
    with pytest.raises(ModelBudgetExhaustedError):
        call(gateway)
    assert len(provider.calls) == 1


def test_repair_needs_budget_too() -> None:
    provider = scripted("not json", {"label": "ok", "score": 0.5})
    gateway, _, _ = budgeted(provider, {"gemini-3.7-flash": 3}, reserve=2)
    with pytest.raises(ModelBudgetExhaustedError):
        call(gateway)
    assert len(provider.calls) == 1


def test_budget_is_per_model_and_unlisted_models_are_not_budgeted() -> None:
    provider = scripted(*[{"label": "ok", "score": 0.5}] * 4)
    gateway, _, _ = budgeted(
        provider,
        {"gemini-3.5-flash-lite": 1},
        reserve=0,
        routine_model="gemini-3.5-flash-lite",
    )
    call(gateway, task_type=TURN_TASK_TYPE)  # routine model: its only request
    with pytest.raises(ModelBudgetExhaustedError):
        call(gateway, task_type=TURN_TASK_TYPE, messages=[Message("user", "next turn")])
    # The default model keeps its own (here unbudgeted) quota for high-value work.
    call(gateway, task_type=ADJUDICATION_TASK_TYPE)
    call(gateway, task_type=GRAPH_TASK_TYPE)
    assert [c["model"] for c in provider.calls] == [
        "gemini-3.5-flash-lite",
        "gemini-3.7-flash",
        "gemini-3.7-flash",
    ]


def test_quota_day_resets_at_midnight_pacific() -> None:
    # 2026-09-25 23:30 PDT == 2026-09-26 06:30 UTC.
    clock = Clock(datetime(2026, 9, 26, 6, 30, tzinfo=UTC))
    provider = scripted(*[{"label": "ok", "score": 0.5}] * 3)
    gateway, _, _ = budgeted(provider, {"gemini-3.7-flash": 1}, reserve=0, clock=clock)
    call(gateway)
    with pytest.raises(ModelBudgetExhaustedError) as exc_info:
        call(gateway, messages=[Message("user", "later")])
    assert exc_info.value.retry_after == pytest.approx(30 * 60)
    clock.now += timedelta(minutes=31)  # 00:01 Pacific: a new quota day
    call(gateway, messages=[Message("user", "later")])
    assert len(provider.calls) == 2


def test_budget_also_guards_embeddings_when_configured() -> None:
    provider = FakeProvider()
    gateway, _, _ = budgeted(provider, {"gemini-embedding-2": 2}, reserve=1)
    embed(gateway)
    with pytest.raises(ModelBudgetExhaustedError):
        embed(gateway, texts=["another query"])
    assert len(provider.embed_calls) == 1


@pytest.mark.parametrize(
    ("value", "parsed"),
    [
        ("", {}),
        ("off", {}),
        ("gemini-3.7-flash=20", {"gemini-3.7-flash": 20}),
        (" a=1 , b=250 ,", {"a": 1, "b": 250}),
    ],
)
def test_daily_limits_parse(value, parsed) -> None:
    assert parse_daily_limits(value) == parsed


@pytest.mark.parametrize("value", ["gemini-3.7-flash", "=5", "m=-1", "m=lots"])
def test_invalid_daily_limits_are_rejected(value) -> None:
    with pytest.raises(ValueError):
        parse_daily_limits(value)
    with pytest.raises(ValueError):
        make_settings(model_daily_request_limits=value)


def test_free_tier_settings_defaults_keep_the_architecture_model() -> None:
    settings = make_settings()
    assert settings.gemini_generation_model == "gemini-3.7-flash"
    assert settings.gemini_routine_model is None  # routing is opt-in
    # N1 (ADR 0008): Flash-Lite and the embedding model are budgeted by default too.
    free_tier = {
        "gemini-3.7-flash": 20,
        "gemini-3.8-flash": 20,
        "gemini-3.5-flash-lite": 500,
        "gemini-embedding-2": 1000,
    }
    assert settings.daily_request_limits == free_tier
    assert settings.model_quota_reserve == 2
    assert settings.model_quota_timezone == "America/Los_Angeles"
    assert settings.model_result_cache and settings.turn_analysis_mode == "combined"
    # A blank value (copied from .env.example) keeps the budget; only "off" disables it.
    assert make_settings(model_daily_request_limits=" ").daily_request_limits == free_tier
    assert make_settings(model_daily_request_limits="off").daily_request_limits == {}
    with pytest.raises(ValueError):
        make_settings(model_quota_timezone="Mars/Olympus_Mons")


# --- Routing ---------------------------------------------------------------------


def test_default_routing_runs_every_task_on_the_frozen_model() -> None:
    provider = scripted(*[{"label": "ok", "score": 0.5}] * 3)
    gateway, _ = make_gateway(provider)
    for task in (TURN_TASK_TYPE, GRAPH_TASK_TYPE, ADJUDICATION_TASK_TYPE):
        call(gateway, task_type=task)
    assert {c["model"] for c in provider.calls} == {"gemini-3.7-flash"}
    assert gateway.routing.name == "architecture-default"


def test_free_tier_routing_sends_routine_tasks_to_the_routine_model() -> None:
    provider = scripted(*[{"label": "ok", "score": 0.5}] * 6)
    gateway, recorder = make_gateway(provider, routine_model="gemini-3.5-flash-lite")
    tasks = [
        TURN_TASK_TYPE,
        QUALIFICATION_TASK_TYPE,
        RERANK_TASK_TYPE,
        MAPPING_TASK_TYPE,
        GRAPH_TASK_TYPE,
        ADJUDICATION_TASK_TYPE,
    ]
    for task in tasks:
        call(gateway, task_type=task)
    assert [r.model for r in recorder.runs] == ["gemini-3.5-flash-lite"] * 4 + [
        "gemini-3.7-flash"
    ] * 2
    assert gateway.routing.name == "free-tier"


def test_every_engine_generation_task_is_classified() -> None:
    engine_tasks = {
        TURN_TASK_TYPE,
        QUALIFICATION_TASK_TYPE,
        RERANK_TASK_TYPE,
        MAPPING_TASK_TYPE,
        ADJUDICATION_TASK_TYPE,
        GRAPH_TASK_TYPE,
        ATTRIBUTION_TASK_TYPE,
        VERIFICATION_GENERATION_TASK_TYPE,
        VERIFICATION_EVALUATION_TASK_TYPE,
    }
    assert engine_tasks == ROUTINE_TASKS | HIGH_VALUE_TASKS
    # P6: both verification tasks are routine (the hackathon Flash-Lite pool can serve them).
    assert {VERIFICATION_GENERATION_TASK_TYPE, VERIFICATION_EVALUATION_TASK_TYPE} <= ROUTINE_TASKS
    assert not ROUTINE_TASKS & HIGH_VALUE_TASKS


def test_each_model_has_its_own_rpm_limiter() -> None:
    created = []

    def factory(rpm):
        limiter = SimpleNamespace(rpm=rpm, acquired=0)
        limiter.acquire = lambda: setattr(limiter, "acquired", limiter.acquired + 1)
        created.append(limiter)
        return limiter

    provider = scripted(*[{"label": "ok", "score": 0.5}] * 3)
    gateway, _ = make_gateway(
        provider,
        routine_model="gemini-3.5-flash-lite",
        generation_rpm=4,
        embedding_rpm=60,
        limiter_factory=factory,
    )
    call(gateway, task_type=TURN_TASK_TYPE)
    call(gateway, task_type=TURN_TASK_TYPE, messages=[Message("user", "x")])
    call(gateway, task_type=GRAPH_TASK_TYPE)
    embed(gateway)
    assert [(lim.rpm, lim.acquired) for lim in created] == [(4, 2), (4, 1), (60, 1)]


def test_routine_model_can_have_its_own_thinking_level() -> None:
    from app.model_gateway.gemini import GeminiProvider
    from tests.test_model_gateway import FakeModels

    usage = SimpleNamespace(
        prompt_token_count=1, candidates_token_count=1, thoughts_token_count=0, total_token_count=2
    )
    models = FakeModels(response=SimpleNamespace(text="{}", usage_metadata=usage, candidates=[]))
    provider = GeminiProvider(
        "unused",
        thinking_level="low",
        thinking_levels={"gemini-3.5-flash-lite": "minimal"},
        client=SimpleNamespace(models=models),
    )
    for model, level in (("gemini-3.5-flash-lite", "MINIMAL"), ("gemini-3.7-flash", "LOW")):
        provider.generate_json(
            model=model, system=None, messages=[Message("user", "u")], json_schema={}, timeout=1
        )
        assert models.generate_args["config"].thinking_config.thinking_level.value == level


# --- Schema guard -------------------------------------------------------------------


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _SchemaPool:
    def __init__(self, columns):
        self.columns = columns

    @contextmanager
    def connection(self):
        yield SimpleNamespace(execute=lambda *a: _Rows([(c,) for c in self.columns]))


def test_worker_refuses_a_database_without_migration_0004() -> None:
    with pytest.raises(ModelRunsSchemaError, match="0004"):
        DbModelRunRecorder(_SchemaPool([])).verify_schema()
    DbModelRunRecorder(_SchemaPool(["cache_key", "cache_source_run_id"])).verify_schema()
