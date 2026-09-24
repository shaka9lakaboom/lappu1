"""POST /v1/events/batch and GET /v1/events/sync-status.

Tests marked `db` run against a real PostgreSQL with the Supabase schema and
migrations applied (TEST_DATABASE_URL, e.g. the local `supabase db start`).
"""

import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.ingestion import service
from app.ingestion.models import EventBatchClientInfo, EventBatchRequest
from tests.conftest import ForbiddenPool, api_client, count_rows, make_batch, make_envelope

URL = "/v1/events/batch"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Authentication / authorization (no database needed) --------------------


def test_missing_jwt_is_rejected(verifier) -> None:
    learner = uuid4()
    response = api_client(verifier, ForbiddenPool()).post(
        URL, json=make_batch(make_envelope(learner))
    )
    assert response.status_code == 401


def test_invalid_jwt_is_rejected(verifier) -> None:
    response = api_client(verifier, ForbiddenPool()).post(
        URL, json=make_batch(make_envelope(uuid4())), headers=auth("not.a.token")
    )
    assert response.status_code == 401


def test_token_from_another_signer_is_rejected(verifier, make_token) -> None:
    learner = uuid4()
    forged = make_token(learner, key=ec.generate_private_key(ec.SECP256R1()))
    response = api_client(verifier, ForbiddenPool()).post(
        URL, json=make_batch(make_envelope(learner)), headers=auth(forged)
    )
    assert response.status_code == 401


def test_expired_jwt_is_rejected(verifier, make_token) -> None:
    learner = uuid4()
    token = make_token(learner, exp=1)
    response = api_client(verifier, ForbiddenPool()).post(
        URL, json=make_batch(make_envelope(learner)), headers=auth(token)
    )
    assert response.status_code == 401


def test_foreign_learner_spoof_is_forbidden(verifier, make_token) -> None:
    me, victim = uuid4(), uuid4()
    body = make_batch(make_envelope(me), make_envelope(victim))
    response = api_client(verifier, ForbiddenPool()).post(
        URL, json=body, headers=auth(make_token(me))
    )
    assert response.status_code == 403
    assert "learner_id" in response.json()["detail"]


def test_malformed_batch_is_rejected(verifier, make_token) -> None:
    learner = uuid4()
    envelope = make_envelope(learner, role="system")
    response = api_client(verifier, ForbiddenPool()).post(
        URL, json=make_batch(envelope), headers=auth(make_token(learner))
    )
    assert response.status_code == 422


def test_untyped_json_is_rejected(verifier, make_token) -> None:
    learner = uuid4()
    response = api_client(verifier, ForbiddenPool()).post(
        URL, json={"anything": ["goes"]}, headers=auth(make_token(learner))
    )
    assert response.status_code == 422


def test_oversize_batch_is_rejected(verifier, make_token) -> None:
    learner = uuid4()
    body = make_batch(*[make_envelope(learner) for _ in range(51)])
    response = api_client(verifier, ForbiddenPool()).post(
        URL, json=body, headers=auth(make_token(learner))
    )
    assert response.status_code == 422


def test_oversize_body_is_rejected_before_parsing(verifier, make_token) -> None:
    learner = uuid4()
    response = api_client(verifier, ForbiddenPool()).post(
        URL,
        content=b"{" + b" " * 6_000_001 + b"}",
        headers={**auth(make_token(learner)), "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_unauthenticated_malformed_request_gets_401_not_validation_details(verifier) -> None:
    response = api_client(verifier, ForbiddenPool()).post(URL, json={"events": "nope"})
    assert response.status_code == 401


def test_storage_not_configured_fails_closed(verifier, make_token) -> None:
    from app.auth import dependencies
    from app.main import create_app
    from tests.conftest import make_settings

    app = create_app(make_settings())
    app.dependency_overrides[dependencies.get_jwt_verifier] = lambda: verifier
    from fastapi.testclient import TestClient

    learner = uuid4()
    response = TestClient(app).post(
        URL, json=make_batch(make_envelope(learner)), headers=auth(make_token(learner))
    )
    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]


def test_extension_origin_preflight(verifier) -> None:
    from app.main import create_app
    from tests.conftest import make_settings

    origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    from fastapi.testclient import TestClient

    app = create_app(make_settings(cors_origins=f"http://localhost:3000,{origin}"))
    response = TestClient(app).options(
        URL,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers

    other = TestClient(app).options(
        URL,
        headers={
            "Origin": "chrome-extension://pponmlkjihgfedcbapponmlkjihgfedcba",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in other.headers


# --- Durable, idempotent ingestion (database) -------------------------------

pytestmark_db = pytest.mark.db


@pytest.fixture
def db_api(db_pool, verifier):
    return api_client(verifier, db_pool)


@pytestmark_db
def test_valid_batch_is_stored_with_provenance_and_jobs(
    db_pool, db_api, new_learner, make_token
) -> None:
    learner = new_learner()
    user = make_envelope(learner, message_index=0)
    assistant = make_envelope(
        learner,
        role="assistant",
        content_text="Binary search repeatedly halves a sorted range.",
        message_index=1,
        external_parent_message_id=user["external_message_id"],
        provider_model="gpt-5",
    )
    response = db_api.post(URL, json=make_batch(user, assistant), headers=auth(make_token(learner)))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 2 and body["duplicates"] == 0
    assert [r["event_id"] for r in body["results"]] == [user["event_id"], assistant["event_id"]]
    assert all(r["status"] == "accepted" for r in body["results"])

    assert count_rows(db_pool, "raw_messages", learner) == 2
    assert count_rows(db_pool, "conversations", learner) == 1
    assert count_rows(db_pool, "processing_jobs", learner) == 2

    with db_pool.connection() as conn:
        row = conn.execute(
            """
            select m.learner_id, m.source_provider::text, m.source_method::text, c.external_id,
                   m.external_message_id, m.external_parent_message_id, m.message_index,
                   m.role::text,
                   m.content_text, m.revision_index, m.provider_model, m.client_event_uuid,
                   m.capture_metadata, j.state::text, j.job_type, j.entity_type
              from public.raw_messages m
              join public.conversations c on c.id = m.conversation_id
              join public.processing_jobs j on j.entity_id = m.id
             where m.id = %s
            """,
            (UUID(body["results"][1]["raw_message_id"]),),
        ).fetchone()
    assert row[0] == learner
    assert row[1:4] == ("chatgpt", "browser_extension", "6750a1b2-0000-4000-8000-000000000001")
    assert row[4] == assistant["external_message_id"]
    assert row[5] == user["external_message_id"]
    assert row[6:11] == (1, "assistant", assistant["content_text"], 0, "gpt-5")
    assert str(row[11]) == assistant["event_id"]
    assert row[12]["extension_version"] == "0.2.0"
    assert row[13:] == ("PENDING", "PROCESS_RAW_MESSAGE", "raw_message")


@pytestmark_db
@pytest.mark.parametrize("times", [2, 10])
def test_same_event_repeatedly_is_stored_once(
    db_pool, db_api, new_learner, make_token, times
) -> None:
    learner = new_learner()
    event = make_envelope(learner)
    headers = auth(make_token(learner))
    first = db_api.post(URL, json=make_batch(event), headers=headers).json()["results"][0]
    for _ in range(times - 1):
        again = db_api.post(URL, json=make_batch(event), headers=headers)
        assert again.status_code == 200
        result = again.json()["results"][0]
        assert result["status"] == "duplicate"
        assert result["raw_message_id"] == first["raw_message_id"]
    assert count_rows(db_pool, "raw_messages", learner) == 1
    assert count_rows(db_pool, "processing_jobs", learner) == 1


@pytestmark_db
def test_same_batch_twice_and_overlapping_batches(db_pool, db_api, new_learner, make_token) -> None:
    learner = new_learner()
    headers = auth(make_token(learner))
    a, b, c, d = (
        make_envelope(learner, content_text=f"message {n}", message_index=n) for n in range(4)
    )

    first = db_api.post(URL, json=make_batch(a, b, c), headers=headers).json()
    assert (first["accepted"], first["duplicates"]) == (3, 0)
    same = db_api.post(URL, json=make_batch(a, b, c), headers=headers).json()
    assert (same["accepted"], same["duplicates"]) == (0, 3)
    overlap = db_api.post(URL, json=make_batch(c, d), headers=headers).json()
    assert [r["status"] for r in overlap["results"]] == ["duplicate", "accepted"]

    assert count_rows(db_pool, "raw_messages", learner) == 4
    assert count_rows(db_pool, "processing_jobs", learner) == 4


@pytestmark_db
def test_duplicate_inside_one_batch(db_pool, db_api, new_learner, make_token) -> None:
    learner = new_learner()
    event = make_envelope(learner)
    body = db_api.post(URL, json=make_batch(event, event), headers=auth(make_token(learner))).json()
    assert [r["status"] for r in body["results"]] == ["accepted", "duplicate"]
    assert count_rows(db_pool, "raw_messages", learner) == 1


@pytestmark_db
def test_recapture_with_new_event_id_is_a_duplicate(
    db_pool, db_api, new_learner, make_token
) -> None:
    """A page reload re-captures the same message with a fresh client event id."""
    learner = new_learner()
    headers = auth(make_token(learner))
    event = make_envelope(learner)
    earlier = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    recaptured = {**event, "event_id": str(uuid4()), "captured_at": earlier}
    db_api.post(URL, json=make_batch(event), headers=headers)
    result = db_api.post(URL, json=make_batch(recaptured), headers=headers).json()["results"][0]
    assert result["status"] == "duplicate"
    assert count_rows(db_pool, "raw_messages", learner) == 1


@pytestmark_db
def test_revisions_of_a_message(db_pool, db_api, new_learner, make_token) -> None:
    learner = new_learner()
    headers = auth(make_token(learner))
    message_id = str(uuid4())
    v0 = make_envelope(learner, external_message_id=message_id, content_text="First draft")
    v1 = make_envelope(
        learner, external_message_id=message_id, content_text="Edited", revision_index=1
    )
    # A client that restarted counting sends new content as revision 0 again.
    v2_claims_0 = make_envelope(
        learner, external_message_id=message_id, content_text="Edited again"
    )

    results = [
        db_api.post(URL, json=make_batch(e), headers=headers).json()["results"][0]
        for e in (v0, v1, v2_claims_0, v1)
    ]
    assert [(r["status"], r["revision_index"]) for r in results] == [
        ("accepted", 0),
        ("accepted", 1),
        ("accepted", 2),
        ("duplicate", 1),
    ]
    assert count_rows(db_pool, "raw_messages", learner) == 3


@pytestmark_db
def test_fallback_fingerprint_dedup_without_message_ids(
    db_pool, db_api, new_learner, make_token
) -> None:
    learner = new_learner()
    headers = auth(make_token(learner))
    event = make_envelope(learner, external_message_id=None, client_event_id="fp:abc")
    # Whitespace-only differences normalize to the same content.
    spaced = {
        **make_envelope(
            learner,
            external_message_id=None,
            content_text="  Explain  binary search in one sentence. ",
        ),
        "captured_at": event["captured_at"],
    }
    other_role = make_envelope(learner, external_message_id=None, role="assistant")
    body = db_api.post(URL, json=make_batch(event, spaced, other_role), headers=headers).json()
    assert [r["status"] for r in body["results"]] == ["accepted", "duplicate", "accepted"]
    assert count_rows(db_pool, "raw_messages", learner) == 2


@pytestmark_db
def test_attachment_metadata_is_stored(db_pool, db_api, new_learner, make_token) -> None:
    learner = new_learner()
    event = make_envelope(
        learner,
        content_text="Solve question 7 from the attached sheet.",
        attachment_metadata=[
            {
                "kind": "file",
                "filename": "sheet.pdf",
                "mime_type": "application/pdf",
                "content_available": False,
            },
            {"kind": "image", "filename": None, "mime_type": None, "content_available": False},
        ],
        context_incomplete=True,
    )
    response = db_api.post(URL, json=make_batch(event), headers=auth(make_token(learner)))
    assert response.status_code == 200
    assert count_rows(db_pool, "attachments", learner) == 2
    with db_pool.connection() as conn:
        (incomplete,) = conn.execute(
            "select context_incomplete from public.raw_messages where learner_id = %s", (learner,)
        ).fetchone()
    assert incomplete is True


@pytestmark_db
def test_learners_are_isolated(db_pool, db_api, new_learner, make_token) -> None:
    alice, bob = new_learner(), new_learner()
    shared_message_id = str(uuid4())
    for learner in (alice, bob):
        event = make_envelope(learner, external_message_id=shared_message_id)
        body = db_api.post(URL, json=make_batch(event), headers=auth(make_token(learner))).json()
        assert body["accepted"] == 1
    assert count_rows(db_pool, "raw_messages", alice) == 1
    assert count_rows(db_pool, "raw_messages", bob) == 1


@pytestmark_db
def test_valid_token_without_profile_is_forbidden(db_api, make_token) -> None:
    ghost = uuid4()
    response = db_api.post(
        URL, json=make_batch(make_envelope(ghost)), headers=auth(make_token(ghost))
    )
    assert response.status_code == 403


@pytestmark_db
def test_failure_mid_batch_rolls_back_everything(
    db_pool, db_api, new_learner, make_token, monkeypatch
) -> None:
    learner = new_learner()
    real_enqueue = service.enqueue_job
    calls = {"n": 0}

    def failing_enqueue(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash after the first event was written")
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(service, "enqueue_job", failing_enqueue)
    batch = make_batch(make_envelope(learner), make_envelope(learner, content_text="second"))
    response = db_api.post(URL, json=batch, headers=auth(make_token(learner)))
    assert response.status_code == 500
    for table in ("raw_messages", "conversations", "processing_jobs"):
        assert count_rows(db_pool, table, learner) == 0

    monkeypatch.setattr(service, "enqueue_job", real_enqueue)
    retry = db_api.post(URL, json=batch, headers=auth(make_token(learner)))
    assert retry.json()["accepted"] == 2
    assert count_rows(db_pool, "processing_jobs", learner) == 2


@pytestmark_db
def test_concurrent_identical_batches_store_once(db_pool, new_learner) -> None:
    learner = new_learner()
    batch = EventBatchRequest.model_validate(
        make_batch(*[make_envelope(learner, content_text=f"m{n}") for n in range(5)])
    )
    client = EventBatchClientInfo(extension_version="0.2.0", adapter_version="chatgpt-1")
    outcomes: list[list[str]] = []
    barrier = threading.Barrier(4)

    def send() -> None:
        barrier.wait()
        with db_pool.connection() as conn:
            stored = service.ingest_batch(conn, learner, batch.events, client)
        outcomes.append([s.status for s in stored])

    threads = [threading.Thread(target=send) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(o.count("accepted") for o in outcomes) == [0, 0, 0, 5]
    assert count_rows(db_pool, "raw_messages", learner) == 5
    assert count_rows(db_pool, "processing_jobs", learner) == 5


@pytestmark_db
def test_sync_status_reports_own_counts(db_pool, db_api, new_learner, make_token) -> None:
    learner, other = new_learner(), new_learner()
    db_api.post(URL, json=make_batch(make_envelope(learner)), headers=auth(make_token(learner)))
    db_api.post(URL, json=make_batch(make_envelope(other)), headers=auth(make_token(other)))
    body = db_api.get("/v1/events/sync-status", headers=auth(make_token(learner))).json()
    assert body["raw_message_count"] == 1
    assert body["pending_jobs"] == 1
    assert body["last_received_at"] is not None


@pytestmark_db
def test_sql_injection_text_is_stored_verbatim(db_pool, db_api, new_learner, make_token) -> None:
    learner = new_learner()
    text = "'); drop table public.raw_messages; -- <img src=x onerror=alert(1)>"
    db_api.post(
        URL,
        json=make_batch(make_envelope(learner, content_text=text)),
        headers=auth(make_token(learner)),
    )
    with db_pool.connection() as conn:
        (stored,) = conn.execute(
            "select content_text from public.raw_messages where learner_id = %s", (learner,)
        ).fetchone()
    assert stored == text
