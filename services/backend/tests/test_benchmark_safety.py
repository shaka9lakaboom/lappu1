"""P3B/P4 false-debt safety benchmark (architecture §16, §17.3, §20.2; ADR 0005).

Runs the whole deterministic set in CI: scripted attribution outputs through the real
validation, evidence qualification, mastery and debt code. No model, no database."""

import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

BENCHMARK = Path(__file__).resolve().parents[3] / "benchmark"
_spec = importlib.util.spec_from_file_location(
    "p3b_p4_safety", BENCHMARK / "runners" / "p3b_p4_safety.py"
)
safety = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(safety)


def schema(name: str) -> dict:
    return json.loads((BENCHMARK / "schema" / f"{name}.schema.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report() -> dict:
    return safety.run()


def test_every_case_and_label_is_valid_and_paired() -> None:
    cases = safety.load_jsonl(BENCHMARK / "cases" / "p3b-p4-safety.jsonl")
    labels = safety.load_jsonl(BENCHMARK / "labels" / "p3b-p4-safety.jsonl")
    for case in cases:
        jsonschema.validate(case, schema("evidence-case"))
    for label in labels:
        jsonschema.validate(label, schema("evidence-label"))
    case_ids = [c["case_id"] for c in cases]
    assert len(set(case_ids)) == len(case_ids) >= 15
    assert case_ids == [label["case_id"] for label in labels]
    for case, label in zip(cases, labels, strict=True):
        assert set(label["expected"]["skills"]) == set(case["skills"]), case["case_id"]


def test_the_prepared_safety_cases_are_all_present() -> None:
    ids = {c["case_id"] for c in safety.load_jsonl(BENCHMARK / "cases" / "p3b-p4-safety.jsonl")}
    assert {
        "join-syntax-once",
        "join-delegation-repeated",
        "heavy-ai-strong-independent",
        "calculator-once",
        "repeated-arithmetic-delegation-with-struggle",
        "explanation-only-exposure",
        "student-reasoning-ai-syntax",
        "give-me-the-answer",
        "student-corrects-ai",
        "copied-ai-answer",
        "one-isolated-failure",
        "no-evidence-unknown",
        "stale-old-evidence",
        "mixed-learning-entertainment",
        "malformed-attribution",
    } <= ids


def test_every_case_passes(report) -> None:
    failed = {r["case_id"]: r["failures"] for r in report["results"] if not r["passed"]}
    assert failed == {}
    assert report["passed"] == report["cases"]


def test_false_ai_assistance_debt_rate_is_zero(report) -> None:
    summary = report["summary"]
    assert summary["no_debt_skills"] >= 15
    assert summary["false_debt"] == 0 and summary["false_debt_rate"] == 0.0
    # ... while repeated real delegation is still detected.
    assert summary["debt_skills"] >= 2 and summary["debt_recall"] == 1.0


@pytest.mark.parametrize(
    ("case_id", "decisions"),
    [
        ("copied-ai-answer", ["COPIED_FROM_AI"]),
        ("give-me-the-answer", ["AI_ACTOR_NOT_PERFORMANCE"]),
        ("low-confidence-incorrect", ["LOW_ATTRIBUTION_CONFIDENCE"]),
        ("self-claim", ["EVIDENCE_TYPE_OTHER"]),
        ("unknown-actor", ["ACTOR_UNKNOWN"] * 3),
        ("malformed-attribution", ["MODEL_OUTPUT_INVALID"]),
        ("invented-skill-id", ["MODEL_OUTPUT_INVALID"]),
        ("hint-assisted-twice", ["QUALIFIED"] * 2),
    ],
)
def test_cases_exercise_the_intended_guard(report, case_id, decisions) -> None:
    (row,) = [r for r in report["results"] if r["case_id"] == case_id]
    assert row["decisions"] == decisions


def test_the_scorer_reports_false_debt() -> None:
    label = {"case_id": "x", "expected": {"skills": {"s": {
        "evidence_events": 1, "mastery_state": "UNKNOWN", "debt_eligible": False,
        "debt_actionable": False, "max_debt_score": 0}}}}  # fmt: skip
    wrong = safety.CaseResult(
        "x",
        skills={"s": safety.SkillOutcome(1, "UNKNOWN", False, 1.0, 0.0, True, True, 22.0)},
    )
    assert safety.score_case(label, wrong) == [
        "s.debt_eligible True != False",
        "s.debt_actionable True != False",
        "s.debt_score 22.0 > 0",
    ]
    assert safety.summarize([(label, wrong)])["false_debt"] == 1
