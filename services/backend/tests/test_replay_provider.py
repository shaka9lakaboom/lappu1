"""Record / replay provider (P8 H2; ADR 0008). Benchmark and E2E only; never a production provider."""

import pytest
from pydantic import BaseModel

from app.model_gateway import InMemoryRunRecorder, ModelGateway, RunContext
from app.model_gateway.replay import (
    RecordingProvider,
    ReplayProvider,
    StaleRecordingError,
    assert_local_database,
    load_recording,
)
from app.model_gateway.types import Message
from tests.fakes import FakeProvider


class Answer(BaseModel):
    label: str


def gateway(provider):
    return ModelGateway(
        provider,
        InMemoryRunRecorder(),
        generation_model="gemini-3.5-flash-lite",
        embedding_model="gemini-embedding-2",
        default_timeout=5,
    )


def ask(gw, text="classify: hello"):
    return gw.generate_structured(
        task_type="TURN_ANALYSIS",
        messages=[Message("system", "You label text."), Message("user", text)],
        response_model=Answer,
        prompt_version="test/v1",
        context=RunContext("replay-test"),
    ).parsed.label


def test_a_recording_replays_the_same_answers_without_the_provider(tmp_path) -> None:
    live = FakeProvider(lambda system, messages, schema: {"label": messages[-1].content[-5:]})
    recorder = RecordingProvider(live)
    gw = gateway(recorder)
    assert ask(gw) == "hello"
    vectors = gw.embed(
        texts=["loop"], input_type="query", prompt_version="q/v1", context=RunContext("replay-test")
    )
    path = tmp_path / "rec.jsonl"
    assert recorder.write(path) == 2 and recorder.requests == 2

    replay = ReplayProvider.from_file(path)
    replayed = gateway(replay)
    assert ask(replayed) == "hello"
    again = replayed.embed(
        texts=["loop"], input_type="query", prompt_version="q/v1", context=RunContext("replay-test")
    )
    assert again.vectors == vectors.vectors
    assert (replay.served, replay.misses) == (2, [])


def test_a_changed_request_is_a_stale_recording_never_another_answer(tmp_path) -> None:
    recorder = RecordingProvider(FakeProvider(lambda s, m, schema: {"label": "x"}))
    ask(gateway(recorder))
    path = tmp_path / "rec.jsonl"
    recorder.write(path)
    replay = ReplayProvider.from_file(path)
    with pytest.raises(StaleRecordingError) as err:
        replay.generate_json(
            model="gemini-3.5-flash-lite",
            system="You label text.",
            messages=[Message("user", "classify: changed")],
            json_schema={},
            timeout=5,
        )
    assert err.value.code == "STALE_RECORDING" and len(replay.misses) == 1


def test_writing_merges_and_sorts_entries(tmp_path) -> None:
    path = tmp_path / "rec.jsonl"
    first = RecordingProvider(FakeProvider(lambda s, m, schema: {"label": "a"}))
    ask(gateway(first), "classify: one")
    first.write(path)
    second = RecordingProvider(FakeProvider(lambda s, m, schema: {"label": "b"}))
    ask(gateway(second), "classify: two")
    assert second.write(path) == 2
    keys = list(load_recording(path))
    assert keys == sorted(keys)


@pytest.mark.parametrize(
    "url", ["postgresql://u:p@127.0.0.1:54322/postgres", "postgresql://localhost/db"]
)
def test_replay_accepts_a_local_database(url) -> None:
    assert_local_database(url)


@pytest.mark.parametrize(
    "url", ["postgresql://u:p@db.example.supabase.co:5432/postgres", "postgresql://u:p@10.0.0.5/db"]
)
def test_replay_refuses_a_hosted_database(url) -> None:
    with pytest.raises(RuntimeError, match="local database"):
        assert_local_database(url)
