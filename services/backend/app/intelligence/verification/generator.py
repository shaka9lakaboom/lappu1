"""Engine 14 - Assessment generation (architecture §11.2, Appendix A.4; ADR 0007).

ONE structured VERIFICATION_GENERATION request per attempt through the ModelGateway (no provider
SDK, no model name here: routing picks the model). The challenge must require the target
competency in a fresh context, test transfer when feasible, stay in the planned difficulty band
and use only known prerequisites; changing numbers only is not enough when conceptual transfer
can be tested.

Every request carries the session id, the attempt number, the rejection reasons of the earlier
attempts and the fingerprints of the learner's earlier challenges for the skill. So separate
sessions never share a cached challenge, a regeneration never gets the rejected cached output
back, and a replay of the same attempt of the same session is served from the exact result cache
(0 provider requests). Captured learner text is never sent to the generator: the validator
compares the challenge against it locally.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.intelligence.contracts import VerificationGenerationOutput
from app.intelligence.verification.graders import normalize_text
from app.model_gateway import (
    Message,
    ModelGateway,
    ModelOutputInvalidError,
    ModelRun,
    RunContext,
)

TASK_TYPE = "VERIFICATION_GENERATION"
PROMPT_VERSION = "verification-generation/v1"
GENERATOR_VERSION = "verification-generator/p6-v1"

SYSTEM_PROMPT = """You write verification challenges for SkillMirror, an evidence-based learning \
platform. A verification challenge checks whether a learner can independently perform ONE target \
skill, without AI help, in a few minutes. It is not a trick question and not a test of reading \
speed.

Rules:
- Test exactly the target skill (return its skill_id unchanged). Do not require any other skill \
except the listed known prerequisites; list the ones the challenge relies on in \
prerequisites_used (their ids), or [] when none.
- Use a FRESH context: a new scenario, new names and new data. It must not reuse an earlier \
challenge listed below. Test transfer (the same competency in a different situation) whenever the \
skill allows it; changing only the numbers of a familiar exercise is not enough.
- difficulty in [0, 1] (0 very easy, 1 very hard) must lie inside the planned band.
- assessment_type must be one of the allowed types. Prefer the earlier types in the list: they are \
graded deterministically. Never use code or sql.
- mcq: 3 to 5 options with keys A, B, C, ... in order, exactly one correct option unless the \
prompt says "select all that apply"; distractors are plausible, reflect real misconceptions, and \
are clearly wrong for a learner who has the skill. expected_answer is the correct key (e.g. "B"), \
or keys separated by commas for "select all that apply". rubric is [].
- numeric: a problem with one numeric answer. expected_answer is the number only (no units, no \
words). choices and rubric are [].
- short_response / reasoning: expected_answer is a concise model answer; rubric lists 1 to 4 \
concrete criteria with points (1-3 each). choices is [].
- prompt: self-contained, plain text, answerable in estimated_minutes (1 to 15). Never reveal the \
answer in the prompt.
- transfer_distance: near (same kind of task), medium (new context), far (different domain).

If earlier attempts were rejected, fix every listed reason."""


@dataclass(frozen=True)
class Prerequisite:
    skill_id: UUID
    canonical_name: str


@dataclass(frozen=True)
class GenerationRequest:
    session_id: UUID
    skill_id: UUID
    canonical_name: str
    description: str
    course_context: str
    difficulty_min: float
    difficulty_max: float
    planned_difficulty: float
    allowed_types: tuple[str, ...]
    prerequisites: tuple[Prerequisite, ...]
    # Earlier challenges of this learner and skill (newest first, bounded by policy).
    history_prompts: tuple[str, ...]
    attempt: int
    # One tuple of rejection reason codes per earlier attempt of this session.
    previous_rejections: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class GenerationResult:
    output: VerificationGenerationOutput | None
    run: ModelRun | None
    runs: tuple[ModelRun, ...]
    cache_hit: bool = False


def fingerprint(prompt: str) -> str:
    """sha256 of the normalized prompt text (history and duplicate bookkeeping)."""
    return hashlib.sha256(normalize_text(prompt).encode("utf-8")).hexdigest()


def _history(prompts: Sequence[str]) -> str:
    if not prompts:
        return "(none)"
    return "\n".join(f"- [{fingerprint(p)[:12]}] {' '.join(p.split())[:240]}" for p in prompts)


def _rejections(previous: Sequence[Sequence[str]]) -> str:
    if not previous:
        return "(none)"
    return "\n".join(
        f"- attempt {i + 1}: {', '.join(reasons)}" for i, reasons in enumerate(previous)
    )


def build_messages(request: GenerationRequest) -> list[Message]:
    prerequisites = (
        "\n".join(f"- id={p.skill_id} name={p.canonical_name!r}" for p in request.prerequisites)
        or "(none)"
    )
    user = (
        f"Verification session: {request.session_id} (generation attempt {request.attempt})\n"
        f"Course context: {request.course_context}\n\n"
        f"Target skill:\n- skill_id={request.skill_id}\n- name={request.canonical_name!r}\n"
        f"- description: {request.description}\n\n"
        f"Planned difficulty: {request.planned_difficulty:.2f} "
        f"(band {request.difficulty_min:.2f} to {request.difficulty_max:.2f})\n"
        f"Allowed assessment types, preferred first: {', '.join(request.allowed_types)}\n\n"
        f"Known prerequisites of the skill:\n{prerequisites}\n\n"
        f"Earlier challenges for this learner and skill (do not repeat or paraphrase them):\n"
        f"{_history(request.history_prompts)}\n\n"
        f"Rejected earlier attempts of this session:\n{_rejections(request.previous_rejections)}"
    )
    return [Message("system", SYSTEM_PROMPT), Message("user", user)]


def generate_challenge(
    gateway: ModelGateway, request: GenerationRequest, context: RunContext
) -> GenerationResult:
    """One generation attempt (plus the gateway's single repair of schema-invalid output).

    Transient gateway errors (429/503, spent budget) propagate: the worker defers the job
    without spending an attempt, and this attempt is replayed with the same input."""
    try:
        result = gateway.generate_structured(
            task_type=TASK_TYPE,
            messages=build_messages(request),
            response_model=VerificationGenerationOutput,
            prompt_version=PROMPT_VERSION,
            context=context,
        )
    except ModelOutputInvalidError as exc:
        return GenerationResult(output=None, run=exc.runs[-1] if exc.runs else None, runs=exc.runs)
    return GenerationResult(
        output=result.parsed, run=result.run, runs=result.runs, cache_hit=result.cache_hit
    )
