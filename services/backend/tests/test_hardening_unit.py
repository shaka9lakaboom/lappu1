"""P8 hardening without a database (ADR 0008 H4, H5): backpressure deferral per job type and the
exact result cache's rules."""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from pydantic import BaseModel

from app.jobs import worker as worker_module
from app.jobs.queue import (
    JOB_BOOTSTRAP_COURSE_GRAPH,
    JOB_EMBED_SKILL,
    JOB_GENERATE_VERIFICATION,
    JOB_GRADE_VERIFICATION,
    JOB_PROCESS_RAW_MESSAGE,
    ClaimedJob,
)
from app.jobs.worker import (
    BACKPRESSURE_DEFAULT_DELAY,
    BACKPRESSURE_MAX_DELAY,
    BACKPRESSURE_MIN_DELAY,
    BUDGET_MAX_DELAY,
    Worker,
)
from app.model_gateway import (
    InMemoryResultCache,
    ModelBudgetExhaustedError,
    ModelRateLimitedError,
    ModelUnavailableError,
    ProviderError,
    RunContext,
)
from app.model_gateway.types import Message
from tests.fakes import FakeProvider, make_gateway

JOB_TYPES = (
    JOB_BOOTSTRAP_COURSE_GRAPH,
    JOB_PROCESS_RAW_MESSAGE,
    JOB_GENERATE_VERIFICATION,
    JOB_GRADE_VERIFICATION,
    JOB_EMBED_SKILL,
)


class _Pool:
    @contextmanager
    def connection(self):
        yield object()


def _job(job_type: str) -> ClaimedJob:
    from uuid import uuid4

    return ClaimedJob(uuid4(), job_type, "entity", uuid4(), None, 1, 3)


@pytest.fixture
def deferred(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        worker_module,
        "defer_job",
        lambda conn, job_id, delay, outcome: calls.append((delay, outcome)),
    )
    monkeypatch.setattr(
        worker_module, "fail_job", lambda *a, **k: pytest.fail("a transient error failed a job")
    )
    return calls


@pytest.mark.parametrize(
    "error",
    [
        ModelRateLimitedError("429", transient=True, retry_after=1),
        ModelUnavailableError("503", transient=True),
        ModelBudgetExhaustedError("spent", transient=True, retry_after=30),
    ],
    ids=["429", "503", "budget"],
)
def test_every_job_type_defers_backpressure_without_failing(deferred, error) -> None:
    """H4: 429 / 503 / a spent budget defer every job type (the attempt is given back)."""

    def raising(job):
        raise error

    worker = Worker(_Pool(), {t: raising for t in JOB_TYPES})  # type: ignore[arg-type]
    for job_type in JOB_TYPES:
        worker._execute(_job(job_type))
    assert len(deferred) == len(JOB_TYPES)
    assert worker.stats.backpressure == len(JOB_TYPES) and worker.stats.failed == 0


@pytest.mark.parametrize(
    ("error", "delay"),
    [
        (
            ModelRateLimitedError("429", transient=True, retry_after=None),
            BACKPRESSURE_DEFAULT_DELAY,
        ),
        (ModelRateLimitedError("429", transient=True, retry_after=0.5), BACKPRESSURE_MIN_DELAY),
        (ModelUnavailableError("503", transient=True, retry_after=50_000), BACKPRESSURE_MAX_DELAY),
        (ModelBudgetExhaustedError("spent", transient=True, retry_after=99_999), BUDGET_MAX_DELAY),
    ],
)
def test_the_server_retry_hint_is_clamped_so_deferral_never_spins(deferred, error, delay) -> None:
    """H4: the retry delay is clamped to 15-600 s (budget: at most an hour), never a tight loop."""

    def raising(job):
        raise error

    Worker(_Pool(), {JOB_PROCESS_RAW_MESSAGE: raising})._execute(_job(JOB_PROCESS_RAW_MESSAGE))  # type: ignore[arg-type]
    ((got, outcome),) = deferred
    assert got == timedelta(seconds=delay)
    assert outcome == (
        "MODEL_BUDGET_RESERVE"
        if isinstance(error, ModelBudgetExhaustedError)
        else "MODEL_BACKPRESSURE"
    )
    assert BACKPRESSURE_MIN_DELAY == 15 and BACKPRESSURE_MAX_DELAY == 600


# --- H5: the exact result cache ----------------------------------------------------------------


class Label(BaseModel):
    label: str


def ask(gateway, *, prompt_version="test/v1", validator=None):
    return gateway.generate_structured(
        task_type="TURN_ANALYSIS",
        messages=[Message("system", "You label text."), Message("user", "classify: hello")],
        response_model=Label,
        prompt_version=prompt_version,
        context=RunContext("cache-test"),
        validator=validator,
    )


def cached_gateway(provider):
    return make_gateway(provider, result_cache=InMemoryResultCache())


def test_a_cache_hit_records_its_source_run_and_sends_nothing() -> None:
    provider = FakeProvider(lambda s, m, schema: {"label": "greeting"})
    gateway, recorder = cached_gateway(provider)
    first, second = ask(gateway), ask(gateway)
    assert len(provider.calls) == 1 and second.cache_hit and not first.cache_hit
    hit = recorder.runs[-1]
    assert hit.cache_source_run_id == first.run.id and not hit.provider_request


def test_a_cached_output_is_checked_again_against_the_current_validator() -> None:
    answers = iter([{"label": "greeting"}, {"label": "salutation"}])
    provider = FakeProvider(lambda s, m, schema: next(answers))
    gateway, _ = cached_gateway(provider)
    ask(gateway)

    def refuse_greeting(output: Label) -> None:
        if output.label == "greeting":
            raise ValueError("greeting is no longer an allowed label")

    fresh = ask(gateway, validator=refuse_greeting)
    assert len(provider.calls) == 2 and not fresh.cache_hit
    assert fresh.parsed.label == "salutation"


def test_a_failure_is_never_cached() -> None:
    outcomes = iter(
        [ProviderError("unavailable", "HTTP_503", "high demand"), {"label": "greeting"}]
    )

    def respond(s, m, schema):
        out = next(outcomes)
        if isinstance(out, Exception):
            raise out
        return out

    provider = FakeProvider(respond)
    gateway, _ = cached_gateway(provider)
    with pytest.raises(ModelUnavailableError):
        ask(gateway)
    assert not ask(gateway).cache_hit  # the retry reached the provider
    assert len(provider.calls) == 2


def test_a_prompt_version_bump_misses_the_cache() -> None:
    provider = FakeProvider(lambda s, m, schema: {"label": "greeting"})
    gateway, _ = cached_gateway(provider)
    ask(gateway, prompt_version="test/v1")
    assert not ask(gateway, prompt_version="test/v2").cache_hit
    assert len(provider.calls) == 2
