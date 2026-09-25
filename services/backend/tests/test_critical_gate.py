"""The P8 critical gate (benchmark/runners/critical_gate.py; ADR 0008) in CI, zero real model calls.

    unit  the 120-case set is exactly the planned taxonomy (families, 34 migrated, 72 live-capable)
          and the database-free kinds (evidence, verification, grading) pass deterministically
    db    all 120 cases in deterministic mode on the local database, every hard gate 0
    db    the replay set from the committed recording, when the recording exists

A replay miss is a stale recording: the case fails (never answered with something else).
"""

import importlib.util
import os
from pathlib import Path

import pytest

BENCHMARK = Path(__file__).resolve().parents[3] / "benchmark"
_spec = importlib.util.spec_from_file_location(
    "critical_gate", BENCHMARK / "runners" / "critical_gate.py"
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def test_the_case_set_is_the_planned_taxonomy() -> None:
    cases = gate.load_cases()
    assert gate.validate_cases(cases) == []
    assert len(cases) == 120
    assert sum(1 for c in cases if c.get("source")) == 34
    assert sum(1 for c in cases if c.get("live")) == gate.LIVE_COUNT == 72
    # Every live-capable case scripts its ideal answer, so CI runs it without a model.
    for case in cases:
        if case["kind"] == "turn":
            assert all("ideal" in t for t in case["turns"]), case["id"]


def test_the_hosted_consistency_defect_is_a_gate_case() -> None:
    """ADR 0008 §24-26: the reused-example + own-explanation turn, and the owner's
    own-work-checked-by-the-AI fixture, are ATT cases; the consistency check is a hard gate."""
    cases = {c["id"]: c for c in gate.load_cases()}
    reused, own = cases["ATT-05"], cases["ATT-09"]
    assert len(reused["turns"]) == 2 and "compromised" in reused["turns"][1]
    assert reused["expect"]["ledger"]["py-for-loops"]["debt_eligible"] is False
    assert reused["expect"]["evidence"]["py-for-loops"]["actor"] == "STUDENT"
    assert own["expect"]["evidence"]["py-for-loops"]["actor"] == "STUDENT"
    assert "attribution_evidence_inconsistency" in gate.HARD_GATES


def test_database_free_kinds_pass_deterministically() -> None:
    report = gate.run_gate("deterministic", database_url=None, only={"EVM", "VER", "GRD"})
    data = report.as_json()
    assert (data["cases"], data["passed"], data["verdict"]) == (28, 28, "PASS"), data["failing"]
    assert all(g["pass"] for g in data["hard_gates"].values())
    assert data["hard_gates"]["deterministic_grader_accuracy"]["value"] == 1.0
    assert data["provider_requests"] == 0


@pytest.mark.db
def test_all_120_cases_pass_deterministically_on_the_database() -> None:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    report = gate.run_gate("deterministic", database_url=DATABASE_URL)
    data = report.as_json()
    assert data["verdict"] == "PASS", (data["failing"], data["hard_gates"])
    assert (data["cases"], data["passed"]) == (120, 120)
    assert {k: g["value"] for k, g in data["hard_gates"].items() if not g["pass"]} == {}


@pytest.mark.db
def test_the_replay_set_passes_from_the_recording() -> None:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    recording = gate.RECORDINGS / f"{gate.LIVE_MODEL}.jsonl"
    # A recording is complete only with its baseline (written by a full live run, H16).
    if not (recording.exists() and (gate.BASELINES / f"{gate.LIVE_MODEL}.json").exists()):
        pytest.skip("no complete live recording committed yet")
    report = gate.run_gate("replay", database_url=DATABASE_URL, recording=recording)
    data = report.as_json()
    assert data["verdict"] == "PASS", (data["failing"], data["hard_gates"])
    assert data["cases"] == 72 and data["blocked"] == 0
    assert data["provider_requests"] == 0  # a replay never reaches a network
