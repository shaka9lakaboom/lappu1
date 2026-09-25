"""P7 rules without a database (ADR 0008): redaction, retry rules, request shapes, the teacher
view policy floor, and the anonymous half of the authorization matrix over every route."""

import pytest
from pydantic import ValidationError

from app.admin.jobs import MAX_MANUAL_RETRIES, retry_options
from app.admin.models import CandidateReviewRequest, CourseMemberAddRequest
from app.core.redaction import redact
from app.teacher.models import MIN_COHORT_FLOOR, TeacherViewPolicy
from tests.conftest import ForbiddenPool, api_client

# --- Redaction ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "leak"),
    [
        ("request failed: https://x/v1?key=AIzaSyA1234567890123456789012345678901", "AIzaSy"),
        # Secret-shaped values are assembled at runtime so the repository scan never sees one.
        (
            "auth "
            + ".".join(
                ["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiIxMjMifQ", "abcdefghijklmnop"]
            ),
            "hbGci",
        ),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123", "abcdefghijklmnop"),
        ("could not connect: postgresql://postgres.ref:s3cret@pooler:6543/postgres", "s3cret"),
        ("key " + "sb_" + "secret_abcdefghijklmnop leaked", "secret_abc"),
        ("user someone.else+tag@example.com not found", "someone.else"),
        ("password=hunter2 rejected", "hunter2"),
    ],
)
def test_redaction_masks_secrets_and_emails(text, leak) -> None:
    assert leak not in redact(text)


def test_redaction_keeps_ordinary_errors_and_truncates() -> None:
    assert redact("ModelCallError: HTTP 503 high demand") == "ModelCallError: HTTP 503 high demand"
    assert redact(None) is None
    long = redact("x" * 500, 300)
    assert len(long) == 300 and long.endswith("…")


# --- Retry rules ------------------------------------------------------------------------------


def options(**overrides):
    values = {
        "state": "FAILED",
        "job_type": "PROCESS_RAW_MESSAGE",
        "manual_retry_count": 0,
        "resumable": False,
        "session_state": None,
        "session_failure": None,
    }
    values.update(overrides)
    return retry_options(**values)


def test_retry_rules() -> None:
    assert options() == ("RETRY", None)
    assert options(job_type="BOOTSTRAP_COURSE_GRAPH") == ("RETRY", None)
    assert options(job_type="EMBED_SKILL") == ("RETRY", None)
    assert options(manual_retry_count=MAX_MANUAL_RETRIES) == (None, "RETRY_LIMIT_REACHED")
    assert options(job_type="SOMETHING_ELSE") == (None, "UNSUPPORTED_JOB_TYPE")
    # A verification job is retried only while its session is still open.
    assert options(job_type="GENERATE_VERIFICATION", session_state="PLANNED") == ("RETRY", None)
    assert options(
        job_type="GENERATE_VERIFICATION",
        session_state="PLANNED",
        session_failure="GENERATION_FAILED",
    ) == (None, "VERIFICATION_CLOSED")
    assert options(job_type="GRADE_VERIFICATION", session_state="SUBMITTED") == ("RETRY", None)
    assert options(job_type="GRADE_VERIFICATION", session_state="EVALUATED") == (
        None,
        "VERIFICATION_CLOSED",
    )
    # Resume only a completed raw-message job with pending attribution.
    assert options(state="COMPLETED", resumable=True) == ("RESUME_ATTRIBUTION", None)
    assert options(state="COMPLETED") == (None, None)
    assert options(state="COMPLETED", job_type="BOOTSTRAP_COURSE_GRAPH", resumable=True) == (
        None,
        None,
    )
    for state in ("PENDING", "PROCESSING", "RETRY_WAIT"):
        assert options(state=state) == (None, None)
    assert MAX_MANUAL_RETRIES == 5  # the processing_jobs check of migration 0009


# --- Request shapes -----------------------------------------------------------------------------


def test_candidate_review_request_shapes() -> None:
    CandidateReviewRequest(action="APPROVE", description="A proper description.", difficulty_band=2)
    CandidateReviewRequest(action="MERGE", target_skill_id="00000000-0000-4000-8000-000000000001")
    CandidateReviewRequest(action="REJECT", note="Out of scope.")
    for bad in (
        {"action": "MERGE"},
        {"action": "REJECT", "target_skill_id": "00000000-0000-4000-8000-000000000001"},
        {"action": "REJECT", "description": "A proper description."},
        {"action": "APPROVE", "difficulty_band": 6},
        {"action": "APPROVE", "description": "short"},
        {"action": "APPROVE", "unexpected": True},
        {"action": "DELETE"},
    ):
        with pytest.raises(ValidationError):
            CandidateReviewRequest.model_validate(bad)


def test_member_request_names_exactly_one_account() -> None:
    CourseMemberAddRequest(email="t@example.edu", role="TEACHER")
    CourseMemberAddRequest(user_id="00000000-0000-4000-8000-000000000001", role="STUDENT")
    for bad in (
        {"role": "STUDENT"},
        {
            "email": "t@example.edu",
            "user_id": "00000000-0000-4000-8000-000000000001",
            "role": "STUDENT",
        },
        {"email": "t@example.edu", "role": "ADMIN"},
    ):
        with pytest.raises(ValidationError):
            CourseMemberAddRequest.model_validate(bad)


def test_the_minimum_cohort_can_never_go_below_three() -> None:
    assert MIN_COHORT_FLOOR == 3
    TeacherViewPolicy(min_cohort=3, window_days=30, top_n=10)
    for bad in ({"min_cohort": 2}, {"min_cohort": 1}, {"window_days": 0}, {"extra": 1}):
        values = {"min_cohort": 3, "window_days": 30, "top_n": 10, **bad}
        with pytest.raises(ValidationError):
            TeacherViewPolicy.model_validate(values)


# --- Authorization matrix, anonymous half: every route refuses before any database access -------


def all_routes(client) -> list[tuple[str, str]]:
    paths = client.get("/openapi.json").json()["paths"]
    routes = []
    for path, ops in paths.items():
        concrete = path
        for param in (
            "{course_id}",
            "{skill_id}",
            "{session_id}",
            "{job_id}",
            "{candidate_id}",
            "{run_id}",
        ):
            concrete = concrete.replace(param, "00000000-0000-4000-8000-000000000001")
        routes.extend((method.upper(), concrete) for method in ops)
    return routes


def test_every_route_but_health_requires_a_token(verifier) -> None:
    client = api_client(verifier, ForbiddenPool())
    routes = all_routes(client)
    assert len(routes) >= 30
    for method, path in routes:
        response = client.request(method, path, json={}, headers={"Idempotency-Key": "k"})
        if path == "/health":
            assert response.status_code == 200
            continue
        assert response.status_code == 401, (method, path, response.status_code)


def test_a_malformed_or_foreign_token_is_refused_everywhere(verifier, make_token) -> None:
    client = api_client(verifier, ForbiddenPool())
    foreign = make_token(
        "00000000-0000-4000-8000-000000000001", iss="https://other.supabase.co/auth/v1"
    )
    for method, path in all_routes(client):
        if path == "/health":
            continue
        for token in ("not-a-jwt", foreign):
            response = client.request(
                method,
                path,
                json={},
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "k"},
            )
            assert response.status_code == 401, (method, path)
