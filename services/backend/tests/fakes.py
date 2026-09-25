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
    # Migration 0005 (P3B) and 0006 (P4) seeds, ADR 0005.
    "attribution": {"min_confidence": 0.80, "copy_guard_min_chars": 24},
    "evidence": {
        "base_weights": {
            "EXPOSURE": 0.0,
            "OBSERVATION": 0.0,
            "ASSISTED_ATTEMPT": 0.35,
            "INDEPENDENT_EXPLANATION": 0.75,
            "INDEPENDENT_APPLICATION": 1.0,
            "TRANSFER": 1.25,
            "VERIFICATION": 1.5,
            "EXECUTION_RESULT": 1.25,
            "TEACHER_EVIDENCE": 1.0,
        },
        "independence": {
            "EXPOSURE": 0.0,
            "OBSERVATION": 0.0,
            "ASSISTED_ATTEMPT": 0.3,
            "INDEPENDENT_EXPLANATION": 0.8,
            "INDEPENDENT_APPLICATION": 1.0,
            "TRANSFER": 1.0,
            "VERIFICATION": 1.0,
            "EXECUTION_RESULT": 1.0,
            "TEACHER_EVIDENCE": 1.0,
        },
        "outcome_values": {"CORRECT": 1.0, "PARTIAL": 0.5, "INCORRECT": 0.0},
        "difficulty": {"default": 0.5, "multiplier_base": 0.75, "multiplier_slope": 0.5},
    },
    "mastery": {
        "prior_alpha": 1.0,
        "prior_beta": 1.0,
        "recency_half_life_days": 180,
        "unknown_min_support": 1.0,
        "emerging_below_mean": 0.45,
        "demonstrated_min_mean": 0.70,
        "demonstrated_min_support": 3.0,
        "application_types": [
            "INDEPENDENT_APPLICATION",
            "TRANSFER",
            "EXECUTION_RESULT",
            "VERIFICATION",
        ],
        "application_min_outcome": 1.0,
        "verified_min_mean": 0.80,
        "verified_min_support": 4.0,
        "verification_max_age_days": 180,
    },
    "debt": {
        "min_recent_delegations": 2,
        "recent_window_days": 30,
        "delegation_half_life_days": 14,
        "tau": 2.0,
        "actor_weights": {"AI": 1.0, "SHARED": 0.5},
        "delegation_evidence_types": ["OBSERVATION", "ASSISTED_ATTEMPT"],
        "min_evidence_confidence": 0.80,
        "learning_relevance_levels": ["high", "medium"],
        "trivial_reason_codes": ["TRIVIAL_UTILITY", "FACTUAL_LOOKUP"],
        "evidence_gap_support_target": 3.0,
        "verification_factor": {"recently_passed": 0.2, "unverified": 0.6, "failed_or_due": 1.0},
        "verification_recent_days": 30,
        "actionable_min_score": 15,
    },
    "recommendations": {
        "max_active_verify": 2,
        "prerequisite_gap_states": ["EMERGING"],
        "debt_bands": {"moderate_min": 15, "high_min": 25},
    },
    # Migration 0008 (P6) seed, ADR 0007.
    "verification": {
        "planner": {
            "max_daily_unsolicited": 2,
            "cooldown_after_pass_days": 7,
            "cooldown_after_fail_days": 1,
            "cooldown_after_abandon_days": 1,
            "retry_after_generation_failure_hours": 24,
            "abandon_in_progress_after_days": 14,
        },
        "difficulty": {"band_half_width": 0.15, "min": 0.1, "max": 0.9},
        "generation": {
            "max_attempts": 3,
            "supported_assessment_types": ["mcq", "numeric", "short_response", "reasoning"],
            "history_limit": 5,
            "duplicate_max_similarity": 0.8,
            "min_prompt_chars": 20,
            "max_prompt_chars": 4000,
            "min_estimated_minutes": 1,
            "max_estimated_minutes": 15,
        },
        "grading": {
            "numeric_relative_tolerance": 0.005,
            "numeric_absolute_tolerance": 0.000001,
            "max_response_chars": 4000,
            "short_response_max_chars": 1000,
            "numeric_max_chars": 64,
            "rubric_pass_min_score": 0.7,
            "min_ai_grading_confidence": 0.7,
        },
        "reverification": {
            "min_contradicting_failures": 2,
            "contradicting_evidence_types": [
                "INDEPENDENT_APPLICATION",
                "TRANSFER",
                "EXECUTION_RESULT",
                "VERIFICATION",
            ],
            "contradicting_outcome_signals": ["INCORRECT"],
        },
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


def make_gateway(
    provider: FakeProvider, recorder: InMemoryRunRecorder | None = None, **options: Any
) -> tuple[ModelGateway, InMemoryRunRecorder]:
    """options: ModelGateway keyword arguments (result_cache, budget, routine_model, ...)."""
    recorder = recorder or InMemoryRunRecorder()
    options.setdefault("generation_model", "gemini-3.7-flash")
    options.setdefault("embedding_model", "gemini-embedding-2")
    gateway = ModelGateway(provider, recorder, default_timeout=5, **options)
    return gateway, recorder


# Engine routing by system prompt marker ------------------------------------
MARKERS = {
    "graph": "design course skill graphs",
    "qualification": "relevance and skill-bearing classifier",
    "rerank": "You rank candidate skills",
    "mapping": "You are the skill mapper",
    "adjudication": "second-pass adjudicator",
    "turn": "You are the turn analyst",
    "turn_adjudication": "You are the turn adjudicator",
    "attribution": "You are the contribution attributor",
    "verification": "You write verification challenges",
    "evaluation": "You grade a learner's answer",
}


def route_responder(**handlers: Any) -> Responder:
    """handlers: engine name -> response (dict/str/Exception), list of responses consumed in
    order (each may be a callable), or callable(messages) -> response."""
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
                    scripted = queues[engine].pop(0)
                    return scripted(messages) if callable(scripted) else scripted
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


def turn_segment(
    text: str = "How do I loop over a list in Python?",
    *,
    ranked: Sequence[str] = (),
    mappings: Sequence[dict[str, Any]] = (),
    new_skill: dict[str, Any] | None = None,
    **qualification: Any,
) -> dict[str, Any]:
    """One segment of a TURN_ANALYSIS output: qualification + top-K ids + mapping proposals."""
    return {
        **segment(text, **qualification),
        "ranked_candidate_ids": list(ranked),
        "mappings": list(mappings),
        "new_skill_candidate": new_skill,
    }


def proposal(skill_id: str, confidence: float, span: str = "loop over a list") -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "confidence": confidence,
        "evidence_span": span,
        "reason_code": "CONCEPT_USE",
    }


def item_ids_in(messages: list[Message]) -> list[str]:
    """The item ids a turn adjudication was asked about, in order."""
    return re.findall(r"item_id=(a[0-9]+)", messages[-1].content)


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


def attribution(
    skill_id: str,
    actor: str = "STUDENT",
    *,
    confidence: float = 0.9,
    evidence_type: str = "INDEPENDENT_APPLICATION",
    outcome: str = "CORRECT",
    student_span: str | None = None,
    ai_span: str | None = None,
    reason_code: str = "STUDENT_WROTE_CODE",
) -> dict[str, Any]:
    """One SKILL_ATTRIBUTION item (Appendix A.3 + outcome signal)."""
    return {
        "skill_id": skill_id,
        "actor": actor,
        "confidence": confidence,
        "student_evidence_span": student_span,
        "ai_evidence_span": ai_span,
        "evidence_type": evidence_type,
        "outcome_signal": outcome,
        "reason_code": reason_code,
    }


def skill_ids_in(messages: list[Message]) -> list[str]:
    """The accepted skill ids an attribution request listed (also inside a repair call)."""
    return re.findall(r"id=([0-9a-f-]{36})", messages[0].content)


def attribute_all(**fields: Any) -> Callable[[list[Message]], dict[str, Any]]:
    """SKILL_ATTRIBUTION answer: the same attribution for every accepted skill listed."""

    def respond(messages: list[Message]) -> dict[str, Any]:
        return {"attributions": [attribution(i, **fields) for i in skill_ids_in(messages)]}

    return respond


def challenge(skill_id: str, **overrides: Any) -> dict[str, Any]:
    """A valid VERIFICATION_GENERATION output (Appendix A.4 + choices): an MCQ by default."""
    out: dict[str, Any] = {
        "skill_id": skill_id,
        "difficulty": 0.5,
        "assessment_type": "mcq",
        "prompt": (
            "A librarian must list every member, including members who never borrowed a book. "
            "Which join returns all members together with any loans they have?"
        ),
        "choices": [
            {"key": "A", "text": "INNER JOIN members to loans"},
            {"key": "B", "text": "LEFT JOIN from members to loans"},
            {"key": "C", "text": "CROSS JOIN members and loans"},
        ],
        "expected_answer": "B",
        "rubric": [],
        "prerequisites_used": [],
        "transfer_distance": "medium",
        "estimated_minutes": 2,
    }
    out.update(overrides)
    return out


def evaluation(criteria: Sequence[tuple[str, bool]], **overrides: Any) -> dict[str, Any]:
    """A VERIFICATION_EVALUATION output (Appendix A.5) consistent with 1-point criteria."""
    met = sum(1 for _, ok in criteria if ok)
    score = met / len(criteria) if criteria else 0.0
    out: dict[str, Any] = {
        "score": score,
        "pass": score >= 0.7,
        "criterion_results": [
            {"criterion": c, "met": ok, "evidence": "quoted part" if ok else ""}
            for c, ok in criteria
        ],
        "confidence": 0.9,
        "feedback": "Clear reasoning about the unmatched rows.",
        "needs_review": False,
    }
    out.update(overrides)
    return out
