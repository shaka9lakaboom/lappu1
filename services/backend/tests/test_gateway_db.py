"""build_gateway wiring and the durable result cache against PostgreSQL (ADR 0004)."""

from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from app.model_gateway import (
    DbModelRunRecorder,
    DbResultCache,
    Message,
    ModelGateway,
    RunContext,
    TieredResultCache,
    build_gateway,
)
from tests.conftest import make_settings
from tests.fakes import FakeProvider

pytestmark = pytest.mark.db


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str


def test_default_settings_build_the_architecture_policy_with_budget_and_cache(db_pool) -> None:
    gateway = build_gateway(make_settings(gemini_api_key="not-a-real-key"), db_pool)
    assert gateway.routing.name == "architecture-default"
    assert gateway.routing.model_for("TURN_ANALYSIS") == "gemini-3.7-flash"
    assert gateway.routing.model_for("SKILL_GRAPH_BOOTSTRAP") == "gemini-3.7-flash"
    # Only budgeted models this gateway can call; the embedding model is budgeted too (N1).
    budgets = {s.model: (s.limit, s.reserve) for s in gateway.budget_status()}
    assert budgets == {"gemini-3.7-flash": (20, 2), "gemini-embedding-2": (1000, 2)}
    assert isinstance(gateway._result_cache, TieredResultCache)


def test_free_tier_settings_route_routine_work_to_the_routine_model(db_pool) -> None:
    settings = make_settings(
        gemini_api_key="not-a-real-key",
        gemini_routine_model="gemini-3.5-flash-lite",
        gemini_routine_thinking_level="minimal",
        model_daily_request_limits="gemini-3.7-flash=20,gemini-3.5-flash-lite=500",
        model_quota_reserve=3,
        model_result_cache=False,
    )
    gateway = build_gateway(settings, db_pool)
    assert gateway.routing.name == "free-tier"
    assert gateway.routing.model_for("TURN_ANALYSIS") == "gemini-3.5-flash-lite"
    assert gateway.routing.model_for("MAPPING_ADJUDICATION") == "gemini-3.7-flash"
    assert gateway.routing.model_for("SKILL_GRAPH_BOOTSTRAP") == "gemini-3.7-flash"
    budgets = {s.model: (s.limit, s.reserve) for s in gateway.budget_status()}
    assert budgets == {"gemini-3.7-flash": (20, 3), "gemini-3.5-flash-lite": (500, 3)}
    assert gateway._result_cache is None and gateway._embedding_cache is None
    assert gateway._provider._thinking_levels == {"gemini-3.5-flash-lite": "minimal"}


def test_durable_cache_survives_a_restart_with_zero_provider_calls(db_pool) -> None:
    provider = FakeProvider(lambda system, messages, schema: {"label": "stored"})
    messages = [Message("system", "Label it."), Message("user", f"unique {uuid4()}")]

    def gateway():  # a fresh process: nothing in memory, only the database
        return ModelGateway(
            provider,
            DbModelRunRecorder(db_pool),
            generation_model="gemini-3.7-flash",
            embedding_model="gemini-embedding-2",
            result_cache=DbResultCache(db_pool),
        )

    def call(gw):
        return gw.generate_structured(
            task_type="TEST_TASK",
            messages=messages,
            response_model=Label,
            prompt_version="test-task/v1",
            context=RunContext(f"test:{uuid4()}"),
        )

    first = call(gateway())
    second = call(gateway())
    assert len(provider.calls) == 1 and second.cache_hit and second.parsed.label == "stored"
    with db_pool.connection() as conn:
        rows = conn.execute(
            "select id, cache_key, cache_source_run_id, output from public.model_runs "
            "where id = any(%s) order by created_at",
            ([first.run.id, second.run.id],),
        ).fetchall()
    (src_id, src_key, src_source, src_out), (hit_id, hit_key, hit_source, hit_out) = rows
    assert src_source is None and hit_source == src_id and src_key == hit_key
    assert src_out == hit_out == {"label": "stored"}
    assert hit_id == second.run.id
