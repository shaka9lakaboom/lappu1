"""P9 /admin/benchmark presentation (app/admin/benchmark_view.py). No DB.

The stored verdict is never rewritten; its parts are shown next to it: safety / correctness hard
gates, case completion, and provider / transport failures.
"""

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.admin.benchmark_view import (
    SAVED_REPORT_ERRORS,
    error_code,
    is_provider_failure,
    presentation,
)

LIVE_ID = UUID("49359994-37d5-48b9-94b9-8b20b9fc4ece")
GATES = {f"gate_{n}": {"value": 0, "threshold": 0, "pass": True} for n in range(13)}
# The hosted LIVE row's report (ids and codes only): REL-06 failed, no family broke a hard gate.
LIVE_REPORT = {
    "failing": ["REL-06"],
    "blocked": [],
    "families": {
        "REL": {"cases": 14, "passed": 13, "blocked": 0, "hard_failures": 0},
        "MAP": {"cases": 16, "passed": 16, "blocked": 0, "hard_failures": 0},
    },
}
SAVED_REPORT = Path(__file__).resolve().parents[3] / "test-results/p8-hosted/reports/live_full.json"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            "ModelUnavailableError: SKILL_ATTRIBUTION: provider unavailable (TRANSPORT)",
            "ModelUnavailableError(TRANSPORT)",
        ),
        ("ModelTimeoutError: TURN_ANALYSIS: timed out (TIMEOUT)", "ModelTimeoutError(TIMEOUT)"),
        ("ModelRateLimitedError: 429 (RATE_LIMITED)", "ModelRateLimitedError(RATE_LIMITED)"),
        ("stale recording (2 miss(es)): ProviderError: no recording", "StaleRecording"),
        ("request cap reached before this case", "RequestCapReached"),
        # Anything else keeps its class only: never the case text or a model output.
        ("AssertionError: expected 'SELECT * FROM secrets' in output", "AssertionError"),
        ("something odd", "OtherError"),
        (None, None),
        ("", None),
    ],
)
def test_error_code_keeps_codes_only(error, code):
    assert error_code(error) == code


def test_provider_failures_are_provider_and_transport_errors_only():
    assert is_provider_failure("ModelUnavailableError(TRANSPORT)")
    assert is_provider_failure("ModelTimeoutError(TIMEOUT)")
    assert is_provider_failure("ModelRateLimitedError(RATE_LIMITED)")
    assert not is_provider_failure("StaleRecording")
    assert not is_provider_failure("AssertionError")
    assert not is_provider_failure("RequestCapReached")


def test_the_hosted_live_run_reads_hard_gates_pass_one_transport_failure():
    view = presentation(LIVE_ID, 1, GATES, LIVE_REPORT)
    assert view["hard_gates_total"] == 13 and view["hard_gates_failed"] == []
    assert view["hard_gate_failure_cases"] == 0 and view["failed_without_hard_gate"] == 1
    assert view["failing_cases"] == ["REL-06"]
    assert view["case_errors"] == {"REL-06": "ModelUnavailableError(TRANSPORT)"}
    assert view["provider_failure_cases"] == ["REL-06"]
    assert "90e600d603abbdef" in view["case_errors_source"]


def test_an_older_row_without_errors_says_the_cause_is_not_recorded():
    view = presentation(uuid4(), 1, GATES, LIVE_REPORT)
    assert view["failed_without_hard_gate"] == 1
    assert view["case_errors"] is None and view["provider_failure_cases"] is None
    assert view["case_errors_source"] is None


def test_a_p9_row_carries_its_own_error_codes():
    report = {**LIVE_REPORT, "errors": {"REL-06": "ModelTimeoutError(TIMEOUT)"}}
    view = presentation(uuid4(), 1, GATES, report)
    assert (
        view["provider_failure_cases"] == ["REL-06"] and view["case_errors_source"] == "run report"
    )


def test_a_hard_gate_failure_is_never_presented_as_a_transport_problem():
    gates = {**GATES, "false_ai_assistance_debt": {"value": 1, "threshold": 0, "pass": False}}
    report = {
        "failing": ["DEBT-03"],
        "families": {"DEBT": {"cases": 20, "passed": 19, "hard_failures": 1}},
        "errors": {},
    }
    view = presentation(uuid4(), 1, gates, report)
    assert view["hard_gates_failed"] == ["false_ai_assistance_debt"]
    assert view["hard_gate_failure_cases"] == 1 and view["failed_without_hard_gate"] == 0
    assert view["provider_failure_cases"] == []


def test_a_clean_run():
    view = presentation(uuid4(), 0, GATES, {"failing": [], "families": {}, "errors": {}})
    assert view["hard_gates_failed"] == [] and view["failed_without_hard_gate"] == 0
    assert view["provider_failure_cases"] == [] and view["case_errors"] == {}


@pytest.mark.skipif(not SAVED_REPORT.exists(), reason="the saved P8 report is local only")
def test_the_saved_report_note_matches_the_saved_report():
    note = SAVED_REPORT_ERRORS[LIVE_ID]
    digest = hashlib.sha256(SAVED_REPORT.read_bytes()).hexdigest()
    assert digest in note.source
    report = json.loads(SAVED_REPORT.read_text(encoding="utf-8"))
    errors = {r["id"]: error_code(r["error"]) for r in report["results"] if not r["passed"]}
    assert errors == note.errors
