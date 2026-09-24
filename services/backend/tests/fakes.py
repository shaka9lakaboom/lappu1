"""Test doubles for the model gateway: a scripted provider and deterministic embeddings.

Nothing here talks to a real model. The fake provider answers structured calls
through a responder (routed by the engine's system prompt) and embeds text as a
signed bag-of-words hash, so texts that share words are close in vector space.
"""

import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence
from typing import Any

from app.intelligence.policy import IntelligencePolicy
from app.model_gateway import InMemoryRunRecorder, Message, ModelGateway
from app.model_gateway.types import (
    EMBEDDING_DIMENSION,
    ProviderEmbeddingResponse,
    ProviderError,
    ProviderTextResponse,
    ProviderUsage,
)

# Architecture §9.3 / §9.4 / Appendix B defaults, identical to the migration 0003 seed
# (tests/test_policy_db.py checks the two agree).
ARCHITECTURE_POLICY: dict[str, Any] = {
    "retrieval": {
        "weights": {"semantic": 0.55, "lexical": 0.30, "course_prior": 0.15},
        "pool_size": 20,
        "rerank_size": 8,
        "channel_limit": 40,
        "query_max_chars": 4000,
    },
    "mapping": {"accept_threshold": 0.80, "adjudicate_min": 0.65, "max_skills_per_segment": 5},
    "qualification": {
        "learning_relevance_levels": ["high", "medium"],
        "min_relevance_confidence": 0.60,
        "min_skill_bearing_confidence": 0.60,
        "max_segments": 4,
    },
    "processing_unit": {
        "recent_context_messages": 4,
        "recent_context_max_chars": 4000,
        "unit_max_chars": 12000,
        "pairing_window_seconds": 120,
        "pairing_retry_seconds": 20,
    },
    "skill_graph": {
        "target_min_skills": 30,
        "target_max_skills": 60,
        "hard_min_skills": 20,
        "hard_max_skills": 80,
        "default_importance": 0.5,
    },
}


def policy(**overrides: dict[str, Any]) -> IntelligencePolicy:
    values = json.loads(json.dumps(ARCHITECTURE_POLICY))
    for key, patch in overrides.items():
        values[key].update(patch)
    return IntelligencePolicy.model_validate(values)


_WORD = re.compile(r"[a-z0-9]+")


def hash_embedding(text: str, dimension: int = EMBEDDING_DIMENSION) -> list[float]:
    vector = [0.0] * dimension
    for token in _WORD.findall(text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    vector[0] += 0.05  # never all-zero
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]


Responder = Callable[[str, list[Message], dict[str, Any]], Any]


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        responder: Responder | None = None,
        embed_fn: Callable[[str], list[float]] = hash_embedding,
        embed_error: ProviderError | None = None,
    ) -> None:
        self.responder = responder
        self.embed_fn = embed_fn
        self.embed_error = embed_error
        self.calls: list[dict[str, Any]] = []
        self.embed_calls: list[dict[str, Any]] = []

    def generate_json(
        self,
        *,
        model: str,
        system: str | None,
        messages: Sequence[Message],
        json_schema: dict[str, Any],
        timeout: float,
    ) -> ProviderTextResponse:
        self.calls.append(
            {
                "model": model,
                "system": system or "",
                "messages": list(messages),
                "schema": json_schema,
            }
        )
        if self.responder is None:
            raise AssertionError("no responder configured for a generation call")
        out = self.responder(system or "", list(messages), json_schema)
        if isinstance(out, Exception):
            raise out
        if not isinstance(out, str):
            out = json.dumps(out)
        return ProviderTextResponse(
            out, ProviderUsage(input_tokens=11, output_tokens=7, total_tokens=18)
        )

    def embed(
        self,
        *,
        model: str,
        texts: Sequence[str],
        titles: Sequence[str | None] | None,
        input_type: str,
        output_dimension: int,
        timeout: float,
    ) -> ProviderEmbeddingResponse:
        self.embed_calls.append({"model": model, "texts": list(texts), "input_type": input_type})
        if self.embed_error is not None:
            raise self.embed_error
        return ProviderEmbeddingResponse([self.embed_fn(t) for t in texts])


def make_gateway(provider: FakeProvider) -> tuple[ModelGateway, InMemoryRunRecorder]:
    recorder = InMemoryRunRecorder()
    gateway = ModelGateway(
        provider,
        recorder,
        generation_model="gemini-3.7-flash",
        embedding_model="gemini-embedding-2",
        default_timeout=5,
    )
    return gateway, recorder


# Engine routing by system prompt marker ------------------------------------
MARKERS = {
    "graph": "design course skill graphs",
    "qualification": "relevance and skill-bearing classifier",
    "rerank": "You rank candidate skills",
    "mapping": "You are the skill mapper",
    "adjudication": "second-pass adjudicator",
}


def route_responder(**handlers: Any) -> Responder:
    """handlers: engine name -> response (dict/str/Exception), list of responses consumed in
    order, or callable(messages) -> response."""
    queues = {k: list(v) for k, v in handlers.items() if isinstance(v, list)}

    def respond(system: str, messages: list[Message], schema: dict[str, Any]) -> Any:
        for engine, marker in MARKERS.items():
            if marker in system:
                if engine not in handlers:
                    raise AssertionError(f"unexpected {engine} call")
                handler = handlers[engine]
                if engine in queues:
                    if not queues[engine]:
                        raise AssertionError(f"no scripted {engine} responses left")
                    return queues[engine].pop(0)
                if callable(handler):
                    return handler(messages)
                return handler
        raise AssertionError(f"unrecognised system prompt: {system[:80]!r}")

    return respond


def candidate_ids_in(messages: list[Message]) -> list[str]:
    """The candidate ids an engine was shown, in order."""
    return re.findall(r"id=([0-9a-f-]{36})", messages[-1].content)


def segment(
    text: str = "How do I loop over a list in Python?",
    *,
    context: str = "academic",
    intent: str = "learn",
    relevance: str = "high",
    relevance_confidence: float = 0.9,
    skill_bearing: bool = True,
    skill_bearing_confidence: float = 0.9,
    reason_code: str = "CONCEPT_QUESTION",
) -> dict[str, Any]:
    return {
        "text": text,
        "context": context,
        "intent": intent,
        "learning_relevance": relevance,
        "relevance_confidence": relevance_confidence,
        "skill_bearing": skill_bearing,
        "skill_bearing_confidence": skill_bearing_confidence,
        "reason_code": reason_code,
    }


def graph_proposal(names: list[str], *, topics: int = 4, prefix: str = "") -> dict[str, Any]:
    """A structurally valid bootstrap proposal with the given skill names."""
    topic_list = [
        {
            "key": f"t{i + 1}",
            "name": f"{prefix}Topic {i + 1}",
            "description": f"Topic number {i + 1} of the course.",
        }
        for i in range(topics)
    ]
    skills = []
    for i, name in enumerate(names):
        skills.append(
            {
                "key": f"s{i + 1}",
                "canonical_name": name,
                "description": f"Demonstrate the competency called {name}.",
                "aliases": [],
                "topic_key": f"t{(i % topics) + 1}",
                "parent_skill_key": None,
                "prerequisite_keys": [f"s{i}"] if i > 0 and i % 5 else [],
                "related_keys": [],
                "importance": 0.5,
                "difficulty_band": 2,
                "assessment_types": ["short_response"],
            }
        )
    return {"topics": topic_list, "skills": skills}
