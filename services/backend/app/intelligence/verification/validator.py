"""Deterministic validation of a generated challenge before delivery (architecture §11.3,
§16 "Verification"; ADR 0007). No model call.

    generated item -> validator
        target skill aligned?          skill_id is exactly the planned skill
        difficulty in the band?        difficulty_min <= difficulty <= difficulty_max
        assessment type supported?     a policy type the skill allows; code / sql need the
                                       sandbox grader V1 does not have
        known prerequisites only?      every prerequisites_used id is a PREREQUISITE of the skill
        well formed?                   prompt length and content, estimated minutes in range
        answer key / rubric shape?     mcq: 2-6 unique keys A-F with distinct texts and a key set
                                       that names existing options (not all of them);
                                       numeric: the answer key parses as a single number;
                                       short_response / reasoning: 1+ distinct rubric criteria
        not a material duplicate?      token-set similarity to earlier challenges of the learner
                                       and skill and to the source activity stays below the policy
                                       limit, and the prompt is not an earlier one with only its
                                       numbers changed
    FAIL -> regenerate with the rejection reasons (bounded by generation.max_attempts)

The answer key's semantic correctness cannot be proven deterministically for free text; the
structural checks above make MCQ and numeric keys gradeable, and the rubric evaluator grades
free text against the rubric.
"""

import re
from dataclasses import dataclass, field

from app.intelligence.contracts import VerificationGenerationOutput
from app.intelligence.policy import SANDBOX_ASSESSMENT_TYPES, VerificationGenerationPolicy
from app.intelligence.verification.graders import answer_key_set, normalize_text, parse_number

VALIDATOR_VERSION = "verification-validator/p6-v1"

_TOKENS = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_DIGITS = re.compile(r"\d+([.,]\d+)?")
_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)


@dataclass(frozen=True)
class ValidationContext:
    skill_id: str
    difficulty_min: float
    difficulty_max: float
    allowed_types: tuple[str, ...]
    known_prerequisites: frozenset[str]
    # Earlier challenges of this learner and skill, and the captured activity that led here.
    history_prompts: tuple[str, ...] = ()
    source_texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    reasons: tuple[str, ...]
    checks: dict[str, bool] = field(default_factory=dict)
    max_similarity: float = 0.0

    @property
    def accepted(self) -> bool:
        return not self.reasons

    def as_json(self) -> dict:
        return {
            "validator_version": VALIDATOR_VERSION,
            "checks": self.checks,
            "max_similarity": round(self.max_similarity, 4),
        }


def tokens(text: str) -> frozenset[str]:
    return frozenset(t.casefold() for t in _TOKENS.findall(text))


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of the word sets (numbers ignored)."""
    left, right = tokens(a), tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def numbers_only_changed(a: str, b: str) -> bool:
    """The same text once every number is masked (a changed-numbers copy)."""
    mask = lambda text: normalize_text(_DIGITS.sub("#", text))  # noqa: E731
    return a.strip() != b.strip() and mask(a) == mask(b)


def _check_answer_key(output: VerificationGenerationOutput) -> list[str]:
    kind = output.assessment_type
    reasons: list[str] = []
    if kind == "mcq":
        keys = [c.key for c in output.choices]
        texts = [normalize_text(c.text) for c in output.choices]
        if not 2 <= len(keys) <= 6 or len(set(keys)) != len(keys) or len(set(texts)) != len(texts):
            reasons.append("MCQ_CHOICES_INVALID")
        expected = answer_key_set(output.expected_answer)
        if not expected or not expected <= set(keys) or expected == set(keys):
            reasons.append("MCQ_ANSWER_KEY_INVALID")
    else:
        if output.choices:
            reasons.append("CHOICES_NOT_ALLOWED")
        if kind == "numeric" and parse_number(output.expected_answer) is None:
            reasons.append("NUMERIC_ANSWER_KEY_INVALID")
    if kind in ("short_response", "reasoning"):
        criteria = [normalize_text(c.criterion) for c in output.rubric]
        if not criteria or len(set(criteria)) != len(criteria):
            reasons.append("RUBRIC_INVALID")
    return reasons


def validate_challenge(
    output: VerificationGenerationOutput,
    context: ValidationContext,
    policy: VerificationGenerationPolicy,
) -> ValidationReport:
    limit = policy.duplicate_max_similarity
    reasons: list[str] = []
    checks: dict[str, bool] = {}

    def check(name: str, ok: bool, reason: str) -> None:
        checks[name] = ok
        if not ok:
            reasons.append(reason)

    check("target_skill", output.skill_id == context.skill_id, "SKILL_MISMATCH")
    check(
        "difficulty_in_band",
        context.difficulty_min <= output.difficulty <= context.difficulty_max,
        "DIFFICULTY_OUT_OF_BAND",
    )
    if output.assessment_type in SANDBOX_ASSESSMENT_TYPES:
        check("assessment_type_supported", False, "SANDBOX_UNAVAILABLE")
    else:
        check(
            "assessment_type_supported",
            output.assessment_type in context.allowed_types,
            "ASSESSMENT_TYPE_NOT_ALLOWED",
        )
    used = set(output.prerequisites_used)
    check(
        "known_prerequisites",
        used <= context.known_prerequisites and context.skill_id not in used,
        "UNSUPPORTED_PREREQUISITE",
    )
    prompt = output.prompt.strip()
    check(
        "prompt_well_formed",
        policy.min_prompt_chars <= len(prompt) <= policy.max_prompt_chars
        and len(_LETTERS.findall(prompt)) >= policy.min_prompt_chars // 2
        and len(tokens(prompt)) >= 4,
        "PROMPT_MALFORMED",
    )
    check(
        "estimated_time",
        policy.min_estimated_minutes <= output.estimated_minutes <= policy.max_estimated_minutes,
        "ESTIMATED_TIME_OUT_OF_RANGE",
    )
    key_reasons = (
        [] if output.assessment_type in SANDBOX_ASSESSMENT_TYPES else _check_answer_key(output)
    )
    checks["answer_key_gradeable"] = not key_reasons
    reasons.extend(key_reasons)

    earlier = [(p, "DUPLICATE_OF_EARLIER_CHALLENGE") for p in context.history_prompts]
    sources = [(t, "DUPLICATE_OF_SOURCE_ACTIVITY") for t in context.source_texts]
    max_similarity = 0.0
    duplicate: set[str] = set()
    for text, reason in (*earlier, *sources):
        score = similarity(prompt, text)
        max_similarity = max(max_similarity, score)
        if score >= limit:
            duplicate.add(reason)
        if numbers_only_changed(prompt, text):
            duplicate.add("NUMBERS_ONLY_CHANGE")
    checks["not_duplicate"] = not duplicate
    reasons.extend(sorted(duplicate))
    return ValidationReport(tuple(reasons), checks, max_similarity)
