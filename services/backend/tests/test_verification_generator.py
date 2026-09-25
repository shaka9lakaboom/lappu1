"""Challenge generation through the ModelGateway (architecture §11.2, §14, Appendix A.4; ADR 0007).

Scripted fake provider only. The request carries the session, the attempt, the earlier rejection
reasons and the history, so the exact result cache serves a replay of the same attempt (0 provider
requests) but never hands a regeneration the rejected output, and never shares a challenge
between sessions."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.intelligence.verification.generator import (
    PROMPT_VERSION,
    TASK_TYPE,
    GenerationRequest,
    Prerequisite,
    fingerprint,
    generate_challenge,
)
from app.model_gateway import InMemoryResultCache, ModelGatewayError, ProviderError, RunContext
from tests.fakes import FakeProvider, challenge, make_gateway, route_responder

SKILL = uuid4()
PREREQUISITE = Prerequisite(uuid4(), "Writing JOIN clauses")
CONTEXT = RunContext(trace_id="test:generation")


def request(session=None, attempt=1, rejections=(), history=()) -> GenerationRequest:
    return GenerationRequest(
        session_id=session or uuid4(),
        skill_id=SKILL,
        canonical_name="Choosing LEFT JOIN",
        description="Choose LEFT JOIN when unmatched rows must be kept.",
        course_context="Databases, Beginner",
        difficulty_min=0.35,
        difficulty_max=0.65,
        planned_difficulty=0.5,
        allowed_types=("mcq", "numeric", "short_response", "reasoning"),
        prerequisites=(PREREQUISITE,),
        history_prompts=tuple(history),
        attempt=attempt,
        previous_rejections=tuple(rejections),
    )


def gateway_with(*responses, **options):
    provider = FakeProvider(route_responder(verification=list(responses)))
    gateway, recorder = make_gateway(provider, result_cache=InMemoryResultCache(), **options)
    return gateway, recorder, provider


def test_one_structured_request_through_the_gateway() -> None:
    gateway, recorder, provider = gateway_with(challenge(str(SKILL)))
    result = generate_challenge(gateway, request(), CONTEXT)
    assert result.output is not None and result.output.skill_id == str(SKILL)
    assert [(r.task_type, r.prompt_version) for r in recorder.runs] == [(TASK_TYPE, PROMPT_VERSION)]
    assert len(provider.calls) == 1


def test_the_same_attempt_of_the_same_session_is_served_from_the_cache() -> None:
    gateway, recorder, provider = gateway_with(challenge(str(SKILL)))
    session = uuid4()
    first = generate_challenge(gateway, request(session), CONTEXT)
    again = generate_challenge(gateway, request(session), CONTEXT)
    assert again.cache_hit and again.output == first.output
    assert len(provider.calls) == 1 and len(recorder.provider_requests) == 1


def test_a_regeneration_never_reuses_the_rejected_cached_output() -> None:
    rejected = challenge(str(SKILL), difficulty=0.9)
    fixed = challenge(str(SKILL), difficulty=0.5)
    gateway, recorder, provider = gateway_with(rejected, fixed)
    session = uuid4()
    first = generate_challenge(gateway, request(session), CONTEXT)
    second = generate_challenge(
        gateway, request(session, attempt=2, rejections=[("DIFFICULTY_OUT_OF_BAND",)]), CONTEXT
    )
    assert first.output.difficulty == 0.9 and second.output.difficulty == 0.5
    assert not second.cache_hit and len(provider.calls) == 2
    sent = provider.calls[1]["messages"][-1].content
    assert "generation attempt 2" in sent and "attempt 1: DIFFICULTY_OUT_OF_BAND" in sent


def test_separate_sessions_never_share_a_cached_challenge() -> None:
    gateway, _, provider = gateway_with(challenge(str(SKILL)), challenge(str(SKILL)))
    generate_challenge(gateway, request(), CONTEXT)
    second = generate_challenge(gateway, request(), CONTEXT)
    assert not second.cache_hit and len(provider.calls) == 2


def test_the_input_carries_history_fingerprints_and_prerequisites_but_no_captured_text() -> None:
    earlier = "List every customer including those without orders."
    gateway, _, provider = gateway_with(challenge(str(SKILL)))
    generate_challenge(gateway, request(history=[earlier]), CONTEXT)
    sent = provider.calls[0]["messages"][-1].content
    assert fingerprint(earlier)[:12] in sent and earlier in sent
    assert f"id={PREREQUISITE.skill_id}" in sent and f"skill_id={SKILL}" in sent
    assert "Verification session:" in sent
    assert "never use code or sql" in provider.calls[0]["system"].lower()


def test_schema_invalid_output_is_repaired_once_then_abstains() -> None:
    broken = {**challenge(str(SKILL)), "answer_explanation": "extra field"}
    gateway, recorder, _ = gateway_with(broken, {"prompt": "still wrong"})
    result = generate_challenge(gateway, request(), CONTEXT)
    assert result.output is None and len(recorder.runs) == 2
    assert [r.status.value for r in recorder.runs] == ["INVALID_OUTPUT", "INVALID_OUTPUT"]


@pytest.mark.parametrize(
    ("code", "kind"), [("HTTP_429", "rate_limited"), ("HTTP_503", "unavailable")]
)
def test_backpressure_propagates_as_transient(code, kind) -> None:
    gateway, _, _ = gateway_with(ProviderError(kind, code, "busy"))
    with pytest.raises(ModelGatewayError) as raised:
        generate_challenge(gateway, request(), CONTEXT)
    assert raised.value.transient


def test_generation_runs_on_the_routine_model_when_configured() -> None:
    gateway, recorder, _ = gateway_with(
        challenge(str(SKILL)), routine_model="gemini-3.5-flash-lite"
    )
    generate_challenge(gateway, request(), CONTEXT)
    assert recorder.runs[0].model == "gemini-3.5-flash-lite"
    default, default_recorder, _ = gateway_with(challenge(str(SKILL)))
    generate_challenge(default, request(), CONTEXT)
    assert default_recorder.runs[0].model == "gemini-3.7-flash"  # the architecture default


def test_no_model_name_or_provider_sdk_in_the_verification_engines() -> None:
    package = Path(__file__).resolve().parents[1] / "app" / "intelligence" / "verification"
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "gemini" not in source.lower(), path.name
        assert "google" not in source.lower(), path.name
