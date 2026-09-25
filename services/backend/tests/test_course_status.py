"""Owner-facing course bootstrap status (demo runtime hotfix): why a graph that is not READY
waits, when it retries, and that no stored error text leaves the API."""

import logging
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from app.core.config import DEFAULT_DAILY_REQUEST_LIMITS
from app.courses import models as course_models
from app.courses.service import bootstrap_wait, public_graph_error
from app.jobs.queue import JOB_BOOTSTRAP_COURSE_GRAPH, ClaimedJob, fail_job
from app.jobs.worker import Worker
from app.model_gateway import ModelBudgetExhaustedError, ModelUnavailableError
from app.model_gateway.routing import ModelRoutingPolicy
from tests.conftest import api_client, make_settings
from tests.test_contract_parity import ts_constants, ts_interface_fields

DUE = datetime(2026, 9, 25, 14, 55, tzinfo=UTC)

# Stored errors as the worker writes them, each hiding something that must never be shown.
LEAKY_ERRORS = [
    "ModelUnavailableError: 503 {'error': {'message': 'high demand', 'status': 'UNAVAILABLE'}}",
    'OperationalError: connection to server at "db.example.supabase.co" failed: '
    "postgresql://postgres:hunter2@db.example.supabase.co:5432/postgres",
    "ValueError: key=AIzaFAKE rejected",
    'Traceback (most recent call last):\n  File "app/jobs/worker.py", line 1',
    "ModelOutputInvalidError: prompt skill_graph/v1 returned {'topics': []}",
]
SECRETS = ["high demand", "hunter2", "supabase.co", "postgresql://", "AIza", "Traceback", "worker.py",
           "prompt", "topics"]  # fmt: skip


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Without a database ------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "outcome", "expected"),
    [
        ("PENDING", "MODEL_BACKPRESSURE", ("MODEL_BACKPRESSURE", DUE)),
        ("PENDING", "MODEL_BUDGET_RESERVE", ("MODEL_BUDGET_RESERVE", DUE)),
        ("PENDING", None, (None, DUE)),  # freshly queued: due, no reason
        ("PENDING", "AWAITING_ASSISTANT", (None, DUE)),  # other deferrals are not provider waits
        ("RETRY_WAIT", "MODEL_BACKPRESSURE", ("RETRY_AFTER_ERROR", DUE)),  # a failure since
        ("PROCESSING", "MODEL_BACKPRESSURE", (None, None)),
        ("COMPLETED", "GRAPH_READY", (None, None)),
        (None, None, (None, None)),
    ],
)
def test_bootstrap_wait_is_derived_from_the_job(state, outcome, expected) -> None:
    assert bootstrap_wait(state, outcome, DUE) == expected


@pytest.mark.parametrize("error", LEAKY_ERRORS)
def test_public_graph_error_never_returns_stored_text(error) -> None:
    public = public_graph_error(error)
    assert public and all(secret not in public for secret in SECRETS)


def test_public_graph_error_summaries() -> None:
    assert public_graph_error(None) is None and public_graph_error("") is None
    assert public_graph_error("ModelTimeoutError: 60s") == "The AI provider did not answer in time."
    assert public_graph_error("worker lock expired") == (
        "The worker stopped while generating this skill graph."
    )


def test_course_contract_parity() -> None:
    assert ts_constants()["BOOTSTRAP_WAIT_REASONS"] == get_args(course_models.BootstrapWaitReason)
    assert ts_interface_fields("Course") == set(course_models.Course.model_fields)


def test_architecture_default_is_unchanged_without_the_demo_runtime(monkeypatch) -> None:
    for name in ("GEMINI_GENERATION_MODEL", "GEMINI_ROUTINE_MODEL", "MODEL_DAILY_REQUEST_LIMITS"):
        monkeypatch.delenv(name, raising=False)
    settings = make_settings()
    assert settings.gemini_generation_model == "gemini-3.7-flash"
    assert settings.gemini_routine_model is None
    assert settings.model_daily_request_limits == DEFAULT_DAILY_REQUEST_LIMITS
    policy = ModelRoutingPolicy(settings.gemini_generation_model, settings.gemini_routine_model)
    assert policy.name == "architecture-default"
    assert policy.model_for("SKILL_GRAPH_BOOTSTRAP") == "gemini-3.7-flash"


def test_demo_runtime_overrides_route_the_bootstrap_to_flash_lite(monkeypatch) -> None:
    # The values scripts/demo.mjs sets (process env wins over services/backend/.env).
    monkeypatch.setenv("GEMINI_GENERATION_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("GEMINI_ROUTINE_MODEL", "gemini-3.5-flash-lite")
    settings = make_settings()
    policy = ModelRoutingPolicy(settings.gemini_generation_model, settings.gemini_routine_model)
    assert policy.model_for("SKILL_GRAPH_BOOTSTRAP") == "gemini-3.5-flash-lite"
    assert policy.model_for("TURN_ANALYSIS") == "gemini-3.5-flash-lite"


# --- With a database ---------------------------------------------------------


def _create_course(client, token: str) -> dict:
    response = client.post(
        "/v1/courses", json={"name": "Introduction to Python"}, headers=auth(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _claim(pool, job_id) -> ClaimedJob:
    """Claim exactly this job the way claim_jobs does (never other sessions' runnable jobs)."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            update public.processing_jobs
               set state = 'PROCESSING', locked_at = now(), locked_by = 'status-test',
                   attempts = attempts + 1
             where id = %s and state in ('PENDING', 'RETRY_WAIT')
            returning id, job_type, entity_type, entity_id, learner_id, attempts, max_attempts
            """,
            (job_id,),
        ).fetchone()
    assert row is not None
    return ClaimedJob(*row)


def _job(pool, job_id) -> tuple:
    with pool.connection() as conn:
        return conn.execute(
            "select state::text, attempts, outcome, available_at from public.processing_jobs "
            "where id = %s",
            (job_id,),
        ).fetchone()


def _raiser(exc: Exception):
    def handler(job):
        raise exc

    return handler


@pytest.mark.db
@pytest.mark.parametrize(
    ("exc", "reason", "min_delay"),
    [
        (ModelUnavailableError("HTTP 503: high demand", transient=True), "MODEL_BACKPRESSURE", 55),
        (
            ModelBudgetExhaustedError(
                "gemini-3.7-flash budget spent", transient=True, retry_after=7 * 3600.0
            ),
            "MODEL_BUDGET_RESERVE",
            3500,
        ),
    ],
)
def test_deferred_bootstrap_exposes_reason_and_next_attempt(
    db_pool, verifier, make_token, new_learner, caplog, exc, reason, min_delay
) -> None:
    learner = new_learner()
    client = api_client(verifier, db_pool)
    created = _create_course(client, make_token(learner))
    course_id, job_id = created["course"]["id"], created["bootstrap_job_id"]
    assert created["course"]["bootstrap_wait_reason"] is None
    assert created["course"]["bootstrap_next_attempt_at"] is not None  # queued: due now

    worker = Worker(db_pool, {JOB_BOOTSTRAP_COURSE_GRAPH: _raiser(exc)}, worker_id="status-test")
    with caplog.at_level(logging.WARNING, logger="skillmirror.worker"):
        worker._execute(_claim(db_pool, job_id))

    state, attempts, outcome, available_at = _job(db_pool, job_id)
    assert (state, attempts, outcome) == ("PENDING", 0, reason)  # no attempt spent
    assert available_at > datetime.now(UTC) + timedelta(seconds=min_delay)
    assert worker.stats.backpressure == 1 and worker.stats.failed == 0

    course = client.get(f"/v1/courses/{course_id}", headers=auth(make_token(learner))).json()
    assert course["bootstrap_job_state"] == "PENDING"
    assert course["bootstrap_wait_reason"] == reason
    assert datetime.fromisoformat(course["bootstrap_next_attempt_at"]) == available_at
    assert course["graph_error"] is None
    listed = client.get("/v1/courses", headers=auth(make_token(learner))).json()["courses"]
    assert listed[0]["bootstrap_wait_reason"] == reason

    (log,) = [r.getMessage() for r in caplog.records if "job deferred" in r.getMessage()]
    assert f"course:{course_id}" in log and reason in log and "BOOTSTRAP_COURSE_GRAPH" in log
    assert "next retry" in log and "attempts 0/3 preserved" in log

    # Not due yet: a worker claims nothing for it (available_at is honoured).
    with db_pool.connection() as conn:
        (claimable,) = conn.execute(
            "select count(*) from public.processing_jobs where id = %s and available_at <= now()",
            (job_id,),
        ).fetchone()
    assert claimable == 0


@pytest.mark.db
def test_failed_attempt_shows_a_retry_and_no_stored_error_text(
    db_pool, verifier, make_token, new_learner
) -> None:
    from app.intelligence.skill_graph.jobs import mark_bootstrap_failed

    learner = new_learner()
    client = api_client(verifier, db_pool)
    created = _create_course(client, make_token(learner))
    course_id, job_id = created["course"]["id"], created["bootstrap_job_id"]
    job = _claim(db_pool, job_id)
    for error in LEAKY_ERRORS:
        with db_pool.connection() as conn:
            fail_job(conn, job.id, error)
        mark_bootstrap_failed(db_pool, job, error, final=False)
        body = client.get(f"/v1/courses/{course_id}", headers=auth(make_token(learner))).text
        assert all(secret not in body for secret in SECRETS), error
        course = client.get(f"/v1/courses/{course_id}", headers=auth(make_token(learner))).json()
        assert course["bootstrap_wait_reason"] == "RETRY_AFTER_ERROR"
        assert course["bootstrap_next_attempt_at"] is not None
        assert course["graph_error"] == public_graph_error(error)

    mark_bootstrap_failed(db_pool, job, LEAKY_ERRORS[1], final=True)
    with db_pool.connection() as conn:
        conn.execute("update public.processing_jobs set state = 'FAILED' where id = %s", (job_id,))
    course = client.get(f"/v1/courses/{course_id}", headers=auth(make_token(learner))).json()
    assert course["graph_status"] == "FAILED"
    assert course["graph_error"] == "An internal error interrupted skill graph generation."
    assert (course["bootstrap_wait_reason"], course["bootstrap_next_attempt_at"]) == (None, None)
