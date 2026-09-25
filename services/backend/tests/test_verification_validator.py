"""The deterministic challenge validator (architecture §11.3, §16 "Verification"; ADR 0007)."""

from uuid import uuid4

import pytest

from app.intelligence.contracts import VerificationGenerationOutput
from app.intelligence.verification.validator import (
    ValidationContext,
    numbers_only_changed,
    similarity,
    validate_challenge,
)
from tests.fakes import challenge, policy

GENERATION = policy().verification.generation
SKILL = str(uuid4())
PREREQUISITE = str(uuid4())


def context(**changes) -> ValidationContext:
    values = {
        "skill_id": SKILL,
        "difficulty_min": 0.35,
        "difficulty_max": 0.65,
        "allowed_types": ("mcq", "numeric", "short_response", "reasoning"),
        "known_prerequisites": frozenset({PREREQUISITE}),
        "history_prompts": (),
        "source_texts": (),
    }
    values.update(changes)
    return ValidationContext(**values)


def validate(ctx: ValidationContext | None = None, **overrides):
    values = challenge(SKILL) | overrides
    output = VerificationGenerationOutput.model_validate(values, strict=True)
    return validate_challenge(output, ctx or context(), GENERATION)


RUBRIC = [{"criterion": "Explains why unmatched rows are kept", "points": 2}]


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"prerequisites_used": [PREREQUISITE]},
        {"assessment_type": "numeric", "choices": [], "expected_answer": "12.5"},
        {"assessment_type": "numeric", "choices": [], "expected_answer": "3/4"},
        {
            "assessment_type": "short_response",
            "choices": [],
            "expected_answer": "Every member row is kept.",
            "rubric": RUBRIC,
        },
        {"assessment_type": "reasoning", "choices": [], "expected_answer": None, "rubric": RUBRIC},
        {
            "expected_answer": "A,B",
            "prompt": "Select all that apply: which joins keep every "
            "member of the left table in this report of members and loans?",
        },
    ],
)
def test_valid_challenges_are_accepted(overrides) -> None:
    report = validate(**overrides)
    assert report.accepted, report.reasons
    assert all(report.checks.values())


def test_target_skill_mismatch_is_rejected() -> None:
    assert "SKILL_MISMATCH" in validate(skill_id=str(uuid4())).reasons


@pytest.mark.parametrize("difficulty", [0.2, 0.34, 0.66, 0.95])
def test_difficulty_outside_the_planned_band_is_rejected(difficulty) -> None:
    assert validate(difficulty=difficulty).reasons == ("DIFFICULTY_OUT_OF_BAND",)


def test_an_unsupported_prerequisite_is_rejected() -> None:
    assert validate(prerequisites_used=[str(uuid4())]).reasons == ("UNSUPPORTED_PREREQUISITE",)
    assert validate(prerequisites_used=[SKILL]).reasons == ("UNSUPPORTED_PREREQUISITE",)


@pytest.mark.parametrize("kind", ["code", "sql"])
def test_code_and_sql_are_rejected_while_the_sandbox_is_unavailable(kind) -> None:
    report = validate(assessment_type=kind, choices=[], expected_answer="SELECT 1", rubric=RUBRIC)
    assert report.reasons == ("SANDBOX_UNAVAILABLE",)


def test_a_type_outside_the_allowed_list_is_rejected() -> None:
    report = validate(context(allowed_types=("numeric",)))
    assert report.reasons == ("ASSESSMENT_TYPE_NOT_ALLOWED",)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"expected_answer": "D"}, "MCQ_ANSWER_KEY_INVALID"),
        ({"expected_answer": None}, "MCQ_ANSWER_KEY_INVALID"),
        ({"expected_answer": "A,B,C"}, "MCQ_ANSWER_KEY_INVALID"),  # every option is not a question
        (
            {"choices": [{"key": "A", "text": "LEFT JOIN"}, {"key": "A", "text": "INNER JOIN"}]},
            "MCQ_CHOICES_INVALID",
        ),
        (
            {"choices": [{"key": "A", "text": "LEFT JOIN"}, {"key": "B", "text": "left join"}]},
            "MCQ_CHOICES_INVALID",
        ),
        ({"choices": [{"key": "B", "text": "LEFT JOIN"}]}, "MCQ_CHOICES_INVALID"),
        (
            {"assessment_type": "numeric", "choices": [], "expected_answer": "about 12"},
            "NUMERIC_ANSWER_KEY_INVALID",
        ),
        ({"assessment_type": "numeric", "expected_answer": "12"}, "CHOICES_NOT_ALLOWED"),
        ({"assessment_type": "short_response", "choices": [], "rubric": []}, "RUBRIC_INVALID"),
        (
            {
                "assessment_type": "reasoning",
                "choices": [],
                "rubric": [
                    RUBRIC[0],
                    {"criterion": "explains why UNMATCHED rows are kept", "points": 1},
                ],
            },
            "RUBRIC_INVALID",
        ),
    ],
)
def test_a_bad_answer_key_or_rubric_is_rejected(overrides, reason) -> None:
    assert reason in validate(**overrides).reasons


@pytest.mark.parametrize("prompt", ["x" * 30, "1 2 3 4 5 6 7 8 9 10 11 12 13 14 15", "Short?"])
def test_an_empty_or_malformed_prompt_is_rejected(prompt) -> None:
    assert "PROMPT_MALFORMED" in validate(prompt=prompt).reasons


@pytest.mark.parametrize("minutes", [30, 60])
def test_an_unreasonable_estimated_time_is_rejected(minutes) -> None:
    assert validate(estimated_minutes=minutes).reasons == ("ESTIMATED_TIME_OUT_OF_RANGE",)


def test_a_materially_duplicate_challenge_is_rejected() -> None:
    earlier = challenge(SKILL)["prompt"]
    reworded = earlier.replace("librarian", "library clerk")
    report = validate(context(history_prompts=(reworded,)))
    assert "DUPLICATE_OF_EARLIER_CHALLENGE" in report.reasons
    assert report.max_similarity >= GENERATION.duplicate_max_similarity


def test_a_challenge_repeating_the_source_activity_is_rejected() -> None:
    source = challenge(SKILL)["prompt"]
    assert "DUPLICATE_OF_SOURCE_ACTIVITY" in validate(context(source_texts=(source,))).reasons


def test_changing_only_the_numbers_is_rejected() -> None:
    earlier = "A shop sells 12 pens at 3 dollars each. How much do the pens cost in total?"
    changed = "A shop sells 15 pens at 4 dollars each. How much do the pens cost in total?"
    assert numbers_only_changed(changed, earlier)
    report = validate(
        context(history_prompts=(earlier,)),
        assessment_type="numeric",
        choices=[],
        expected_answer="60",
        prompt=changed,
    )
    assert "NUMBERS_ONLY_CHANGE" in report.reasons


def test_a_fresh_transfer_challenge_is_not_a_duplicate() -> None:
    earlier = "Write a query listing every customer, including customers without orders."
    report = validate(context(history_prompts=(earlier,)))
    assert report.accepted and similarity(challenge(SKILL)["prompt"], earlier) < 0.8
