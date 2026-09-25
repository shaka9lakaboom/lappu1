"""How a stored benchmark run reads on /admin/benchmark (P9). Presentation only: no row is changed.

A run's stored verdict mixes two different questions. P9 keeps the verdict exactly as recorded and
shows its parts next to it:

    safety / correctness hard gates    every zero-tolerance gate held (PASS) or which failed
    case completion                    passed / cases
    hard-gate failures                 cases that broke a hard gate (the families' hard_failures)
    failed without a hard gate         the rest of the failed cases: in LIVE / REPLAY a case fails
                                       only on a hard gate or an execution error, so these did not
                                       complete (DETERMINISTIC also fails a missed label)
    provider / transport failures      the failed cases whose recorded error is a provider or
                                       transport error (unavailable, timeout, rate limit)

The error of each failing case is in the run's report from P9 on (`report.errors`: case id ->
error code, ids and codes only). Rows recorded before that kept only the failing ids; for those,
`SAVED_REPORT_ERRORS` carries the error the runner's saved JSON report recorded, with that file's
sha256 as provenance. The stored counts and verdict are never rewritten (LIVE stays FAIL).
"""

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

PROVIDER_ERRORS = frozenset(
    {"ModelUnavailableError", "ModelTimeoutError", "ModelRateLimitedError", "ProviderError"}
)
_CLASS = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b")
_CODE = re.compile(r"\(([A-Z][A-Z0-9_]{2,40})\)\s*$")


def error_code(error: str | None) -> str | None:
    """A failing case's error as a code only (exception class + provider code), never its text:
    'ModelUnavailableError: SKILL_ATTRIBUTION: provider unavailable (TRANSPORT)' ->
    'ModelUnavailableError(TRANSPORT)'."""
    if not error:
        return None
    if error.startswith("stale recording"):
        return "StaleRecording"
    if error.startswith("request cap reached"):
        return "RequestCapReached"
    cls = _CLASS.match(error)
    code = _CODE.search(error)
    name = cls.group(1) if cls else "OtherError"
    return f"{name}({code.group(1)})" if code else name


def is_provider_failure(code: str) -> bool:
    return code.split("(", 1)[0] in PROVIDER_ERRORS


@dataclass(frozen=True)
class SavedReportErrors:
    source: str
    errors: dict[str, str]


# Rows recorded before report.errors existed (P8, hosted 2026-09-25). The value is error_code() of
# the error in the runner's saved report; the source names that file and its sha256.
SAVED_REPORT_ERRORS: dict[UUID, SavedReportErrors] = {
    UUID("49359994-37d5-48b9-94b9-8b20b9fc4ece"): SavedReportErrors(
        source=(
            "saved runner report live_full.json, sha256 "
            "90e600d603abbdefd378572747ed65f5b4c1fe97eb68a6e0f0c87ff1d9e715a7 (ADR 0008 §35)"
        ),
        errors={"REL-06": "ModelUnavailableError(TRANSPORT)"},
    ),
}


def presentation(
    run_id: UUID, failed_count: int, hard_gates: dict[str, Any], report: dict[str, Any]
) -> dict[str, Any]:
    gates = {k: v for k, v in hard_gates.items() if isinstance(v, dict)}
    failed_gates = sorted(k for k, v in gates.items() if v.get("pass") is False)
    families = report.get("families") or {}
    hard_failures = sum(int(f.get("hard_failures", 0)) for f in families.values())
    failing = [str(c) for c in report.get("failing") or []]
    errors: dict[str, str] | None = None
    source: str | None = None
    if isinstance(report.get("errors"), dict):
        errors = {str(k): str(v) for k, v in report["errors"].items()}
        source = "run report"
    elif run_id in SAVED_REPORT_ERRORS:
        saved = SAVED_REPORT_ERRORS[run_id]
        errors, source = dict(saved.errors), saved.source
    known = {c: errors[c] for c in failing if c in errors} if errors is not None else None
    provider = (
        sorted(c for c, code in known.items() if is_provider_failure(code))
        if known is not None
        else None
    )
    return {
        "hard_gates_total": len(gates),
        "hard_gates_failed": failed_gates,
        "hard_gate_failure_cases": hard_failures,
        "failed_without_hard_gate": max(failed_count - hard_failures, 0),
        "failing_cases": failing,
        "case_errors": known,
        "case_errors_source": source,
        "provider_failure_cases": provider,
    }
