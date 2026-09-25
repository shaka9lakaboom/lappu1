"""Deterministic verification graders and response handling (architecture §11.2, §14.3; ADR 0007).

No model call. MCQ is an exact set comparison, numeric a parse plus the policy tolerance, and a
short response that equals the answer key passes deterministically. Anything a deterministic
grader cannot decide goes to the rubric evaluator (evaluator.py) instead - a mismatch against a
model answer is never turned into a failure here.

The outcome is a pure function of the grade:

    pass                  -> CORRECT,   outcome 1.0
    score 0 (not passed)  -> INCORRECT, outcome 0.0
    otherwise             -> PARTIAL,   outcome = score

Learner responses are untrusted data: validated and normalized before they are stored, and
never interpreted as instructions.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.intelligence.policy import VerificationGradingPolicy

MCQ_GRADER_VERSION = "grader/mcq-exact-v1"
NUMERIC_GRADER_VERSION = "grader/numeric-tolerance-v1"
SHORT_EXACT_GRADER_VERSION = "grader/short-exact-v1"

_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_FRACTION = re.compile(r"^([+-]?\d+)\s*/\s*(\d+)$")
_WORDS = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True)
class ChallengeItem:
    """What grading needs of a stored verification item (server-side: holds the answer key)."""

    id: UUID
    assessment_type: str
    grader_type: str
    prompt: str
    choices: tuple[tuple[str, str], ...]
    expected_answer: str | None
    rubric: tuple[tuple[str, int], ...]

    @property
    def choice_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.choices)


def challenge_item(row: tuple) -> ChallengeItem:
    """(id, assessment_type, grader_type, prompt, choices, expected_answer, rubric)."""
    return ChallengeItem(
        id=row[0],
        assessment_type=row[1],
        grader_type=row[2],
        prompt=row[3],
        choices=tuple((c["key"], c["text"]) for c in row[4]),
        expected_answer=row[5],
        rubric=tuple((c["criterion"], int(c["points"])) for c in row[6]),
    )


@dataclass(frozen=True)
class Grade:
    score: float
    passed: bool
    outcome_signal: str
    outcome: float
    evaluation: dict[str, Any]
    feedback: str
    grading_confidence: float
    evaluator_type: str
    evaluator_version: str
    evaluator_model_run_id: UUID | None = None
    evaluator_prompt_version: str | None = None
    model_run_ids: tuple[UUID, ...] = field(default_factory=tuple)


class ResponseInvalidError(ValueError):
    """The submitted answer does not fit the challenge (422): nothing is stored."""


def parse_number(text: str | None) -> float | None:
    """A plain number: 42, -3.5, .5, 1e-3, 1,234.5 or a fraction 3/4. Anything else is None."""
    if text is None:
        return None
    value = text.strip().replace("−", "-")
    if _THOUSANDS.match(value):
        value = value.replace(",", "")
    fraction = _FRACTION.match(value)
    if fraction:
        denominator = int(fraction.group(2))
        if denominator == 0:
            return None
        return int(fraction.group(1)) / denominator
    if not _NUMBER.match(value):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def answer_key_set(expected_answer: str | None) -> frozenset[str]:
    """An MCQ answer key "B" or "A,C" as a set of choice keys."""
    if not expected_answer:
        return frozenset()
    return frozenset(k.strip().upper() for k in expected_answer.split(",") if k.strip())


def normalize_text(text: str) -> str:
    return " ".join(_WORDS.sub(" ", text.casefold()).split())


def outcome_for(score: float, passed: bool) -> tuple[str, float]:
    """The deterministic outcome of a grade (see the module docstring)."""
    if passed:
        return "CORRECT", 1.0
    if score <= 0:
        return "INCORRECT", 0.0
    return "PARTIAL", score


def normalize_response(
    item: ChallengeItem,
    *,
    answer: str | None,
    selected: list[str] | None,
    policy: VerificationGradingPolicy,
) -> dict[str, Any]:
    """Validate the learner's answer against the challenge; the stored (normalized) response."""
    if item.assessment_type == "mcq":
        if answer is not None or not selected:
            raise ResponseInvalidError("Choose at least one option.")
        keys = [k.strip().upper() for k in selected]
        if len(set(keys)) != len(keys):
            raise ResponseInvalidError("Each option can be chosen once.")
        unknown = sorted(set(keys) - set(item.choice_keys))
        if unknown:
            raise ResponseInvalidError(f"Unknown option: {', '.join(unknown)}.")
        return {"selected": sorted(keys)}
    if selected is not None:
        raise ResponseInvalidError("This challenge takes a written answer, not options.")
    text = (answer or "").strip()
    if not text:
        raise ResponseInvalidError("Write an answer first.")
    if item.assessment_type == "numeric":
        if len(text) > policy.numeric_max_chars or parse_number(text) is None:
            raise ResponseInvalidError("Enter a single number (for example 42, -3.5 or 3/4).")
        return {"answer": text}
    limit = (
        policy.short_response_max_chars
        if item.assessment_type == "short_response"
        else policy.max_response_chars
    )
    if len(text) > limit:
        raise ResponseInvalidError(f"Keep the answer under {limit} characters.")
    return {"answer": text}


def _choice_text(item: ChallengeItem, keys: frozenset[str]) -> str:
    texts = dict(item.choices)
    return "; ".join(f"{k}: {texts.get(k, '')}" for k in sorted(keys))


def grade_mcq(item: ChallengeItem, response: dict[str, Any]) -> Grade:
    expected = answer_key_set(item.expected_answer)
    chosen = frozenset(response.get("selected") or ())
    passed = chosen == expected
    score = 1.0 if passed else 0.0
    signal, outcome = outcome_for(score, passed)
    feedback = (
        "Correct."
        if passed
        else f"Not quite. The expected answer was {_choice_text(item, expected)}."
    )
    return Grade(
        score=score,
        passed=passed,
        outcome_signal=signal,
        outcome=outcome,
        evaluation={
            "grader": "MCQ_EXACT",
            "selected": sorted(chosen),
            "expected": sorted(expected),
        },
        feedback=feedback,
        grading_confidence=1.0,
        evaluator_type="DETERMINISTIC",
        evaluator_version=MCQ_GRADER_VERSION,
    )


def grade_numeric(
    item: ChallengeItem, response: dict[str, Any], policy: VerificationGradingPolicy
) -> Grade:
    expected = parse_number(item.expected_answer)
    given = parse_number(response.get("answer"))
    if expected is None or given is None:  # pragma: no cover - validated before delivery / submit
        raise ValueError("numeric grading needs a parseable answer key and response")
    tolerance = max(
        policy.numeric_absolute_tolerance, policy.numeric_relative_tolerance * abs(expected)
    )
    passed = abs(given - expected) <= tolerance
    score = 1.0 if passed else 0.0
    signal, outcome = outcome_for(score, passed)
    return Grade(
        score=score,
        passed=passed,
        outcome_signal=signal,
        outcome=outcome,
        evaluation={
            "grader": "NUMERIC_TOLERANCE",
            "given": given,
            "expected": expected,
            "tolerance": tolerance,
        },
        feedback="Correct."
        if passed
        else f"Not quite. The expected answer was {item.expected_answer}.",
        grading_confidence=1.0,
        evaluator_type="DETERMINISTIC",
        evaluator_version=NUMERIC_GRADER_VERSION,
    )


def grade_short_exact(item: ChallengeItem, response: dict[str, Any]) -> Grade | None:
    """A short response equal to the answer key passes deterministically; otherwise None (the
    rubric evaluator decides - a different wording is not a wrong answer)."""
    if item.assessment_type != "short_response" or not item.expected_answer:
        return None
    if normalize_text(str(response.get("answer", ""))) != normalize_text(item.expected_answer):
        return None
    return Grade(
        score=1.0,
        passed=True,
        outcome_signal="CORRECT",
        outcome=1.0,
        evaluation={"grader": "SHORT_EXACT", "matched_answer_key": True},
        feedback="Correct.",
        grading_confidence=1.0,
        evaluator_type="DETERMINISTIC",
        evaluator_version=SHORT_EXACT_GRADER_VERSION,
    )


def grade_deterministically(
    item: ChallengeItem, response: dict[str, Any], policy: VerificationGradingPolicy
) -> Grade | None:
    """The deterministic grade, or None when the rubric evaluator is needed."""
    if item.grader_type == "MCQ_EXACT":
        return grade_mcq(item, response)
    if item.grader_type == "NUMERIC_TOLERANCE":
        return grade_numeric(item, response, policy)
    return grade_short_exact(item, response)
