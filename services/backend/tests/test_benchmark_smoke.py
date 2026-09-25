"""Benchmark contract (architecture §17): case/label files are valid; the smoke scorer is correct."""

import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

BENCHMARK = Path(__file__).resolve().parents[3] / "benchmark"
_spec = importlib.util.spec_from_file_location("p3a_smoke", BENCHMARK / "runners" / "p3a_smoke.py")
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


def schema(name: str) -> dict:
    return json.loads((BENCHMARK / "schema" / f"{name}.schema.json").read_text(encoding="utf-8"))


def test_every_case_and_label_is_valid_and_paired() -> None:
    cases = smoke.load_jsonl(BENCHMARK / "cases" / "p3a-smoke.jsonl")
    labels = smoke.load_jsonl(BENCHMARK / "labels" / "p3a-smoke.jsonl")
    for case in cases:
        jsonschema.validate(case, schema("case"))
    for label in labels:
        jsonschema.validate(label, schema("label"))
    case_ids = [c["case_id"] for c in cases]
    assert len(set(case_ids)) == len(case_ids) >= 10
    assert case_ids == [label["case_id"] for label in labels]
    families = {c["family"] for c in cases}
    assert {"relevance", "segmentation", "mapping", "abstention", "adversarial"} <= families


LEARNING = {
    "expected": {
        "routes": ["MAP"],
        "must_include_routes": ["MAP"],
        "max_segments": 1,
        "mapped_skill_any": ["loop"],
        "evidence_expected": False,
    }
}
NON_LEARNING = {"expected": {"routes": ["STOP", "METADATA_ONLY"], "evidence_expected": False}}


@pytest.mark.parametrize(
    ("label", "result", "passed"),
    [
        (LEARNING, smoke.CaseResult("c", ["MAP"], ["For Loops over Lists"]), True),
        (LEARNING, smoke.CaseResult("c", ["MAP"], ["Dictionary Lookup"]), False),
        (LEARNING, smoke.CaseResult("c", ["MAP", "MAP"], ["For Loops"]), False),
        (LEARNING, smoke.CaseResult("c", ["STOP"]), False),
        (NON_LEARNING, smoke.CaseResult("c", ["STOP"]), True),
        (NON_LEARNING, smoke.CaseResult("c", ["MAP"], ["For Loops"]), False),
        (NON_LEARNING, smoke.CaseResult("c", [], error="boom"), False),
    ],
)
def test_score_case(label, result, passed) -> None:
    assert (smoke.score_case(label, result) == []) is passed


def test_abstention_can_satisfy_an_unknown_concept_case() -> None:
    label = {
        "expected": {
            "routes": ["MAP"],
            "mapped_skill_any": ["polars"],
            "abstain_allowed": True,
            "evidence_expected": False,
        }
    }
    assert smoke.score_case(label, smoke.CaseResult("c", ["MAP"], abstained=True)) == []
