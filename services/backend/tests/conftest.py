import os
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.auth import dependencies
from app.auth.jwt import SupabaseJWTVerifier
from app.core.config import Settings
from app.db import pool as db_pool_module
from app.ingestion.fingerprint import content_hash
from app.main import create_app

ISSUER = "https://project-ref.supabase.co/auth/v1"


def make_settings(**overrides: object) -> Settings:
    """Settings isolated from the developer's environment and .env file."""
    values: dict[str, object] = {"app_env": "test"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings() -> Settings:
    return make_settings(cors_origins="http://localhost:3000")


@pytest.fixture
def client(settings: Settings) -> TestClient:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


# --- Tokens -----------------------------------------------------------------


class FakeJWKSClient:
    """Stands in for PyJWKClient so tests never touch the network."""

    def __init__(self, public_key: object | None = None, error: Exception | None = None):
        self._public_key = public_key
        self._error = error

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        if self._error:
            raise self._error
        return SimpleNamespace(key=self._public_key)


@pytest.fixture(scope="session")
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def verifier(signing_key) -> SupabaseJWTVerifier:
    return SupabaseJWTVerifier(ISSUER, jwks_client=FakeJWKSClient(signing_key.public_key()))


@pytest.fixture
def make_token(signing_key) -> Callable[..., str]:
    def _make(sub: UUID | str, key: object | None = None, **overrides: object) -> str:
        now = int(time.time())
        payload: dict[str, object] = {
            "sub": str(sub),
            "aud": "authenticated",
            "iss": ISSUER,
            "role": "authenticated",
            "iat": now,
            "exp": now + 3600,
        }
        payload.update(overrides)
        return jwt.encode(payload, key or signing_key, algorithm="ES256", headers={"kid": "k1"})

    return _make


# --- Envelopes --------------------------------------------------------------


def make_envelope(learner_id: UUID | str, **overrides: object) -> dict[str, object]:
    """A valid RawActivityEnvelope as the extension sends it (JSON-shaped)."""
    text = overrides.pop("content_text", "Explain binary search in one sentence.")
    message_id = overrides.pop("external_message_id", str(uuid4()))
    revision = overrides.pop("revision_index", 0)
    envelope: dict[str, object] = {
        "event_id": str(uuid4()),
        "schema_version": 1,
        "learner_id": str(learner_id),
        "source_provider": "chatgpt",
        "source_method": "browser_extension",
        "external_conversation_id": "6750a1b2-0000-4000-8000-000000000001",
        "external_message_id": message_id,
        "external_parent_message_id": None,
        "message_index": 0,
        "role": "user",
        "content_text": text,
        "content_format": "text",
        "occurred_at": None,
        "captured_at": datetime.now(UTC).isoformat(),
        "provider_model": None,
        "revision_index": revision,
        "attachment_metadata": [],
        "context_incomplete": False,
        "active_course_id": None,
        "content_hash": content_hash(str(text)),
        "client_event_id": f"chatgpt:{message_id}:r{revision}" if message_id else "fp:test",
    }
    envelope.update(overrides)
    return envelope


def make_batch(*events: dict[str, object]) -> dict[str, object]:
    return {
        "client": {"extension_version": "0.2.0", "adapter_version": "chatgpt-1"},
        "events": list(events),
    }


# --- API client wired to a fake verifier ------------------------------------


class ForbiddenPool:
    """Used where a request must be refused before touching the database."""

    def connection(self):
        raise AssertionError("the database must not be used for this request")


def api_client(verifier: SupabaseJWTVerifier, pool: object) -> TestClient:
    app = create_app(make_settings(cors_origins="http://localhost:3000"))
    app.dependency_overrides[dependencies.get_jwt_verifier] = lambda: verifier
    app.dependency_overrides[db_pool_module.get_db_pool] = lambda: pool
    return TestClient(app, raise_server_exceptions=False)


# --- Database (integration) -------------------------------------------------

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def db_pool() -> Iterator[object]:
    if not TEST_DATABASE_URL:
        if os.environ.get("REQUIRE_DB_TESTS") == "1":
            pytest.fail("REQUIRE_DB_TESTS=1 but TEST_DATABASE_URL is not set")
        pytest.skip("TEST_DATABASE_URL not set (start the local stack: supabase db start)")
    pool = db_pool_module.create_pool(TEST_DATABASE_URL, max_size=8)
    yield pool
    pool.close()


def _create_learner(pool) -> UUID:
    learner_id = uuid4()
    with pool.connection() as conn:
        conn.execute(
            "insert into auth.users (id, email) values (%s, %s)",
            (learner_id, f"p1-{learner_id.hex[:12]}@test.invalid"),
        )
    return learner_id


@pytest.fixture
def new_learner(db_pool) -> Iterator[Callable[[], UUID]]:
    """Creates auth users (the signup trigger adds profiles); deletes them afterwards."""
    created: list[UUID] = []

    def _new() -> UUID:
        learner_id = _create_learner(db_pool)
        created.append(learner_id)
        return learner_id

    yield _new
    with db_pool.connection() as conn:
        for learner_id in created:
            conn.execute("delete from auth.users where id = %s", (learner_id,))


def count_rows(pool, table: str, learner_id: UUID) -> int:
    assert table in {"conversations", "raw_messages", "attachments", "processing_jobs"}
    with pool.connection() as conn:
        (count,) = conn.execute(
            f"select count(*) from public.{table} where learner_id = %s",  # noqa: S608 - allow-listed
            (learner_id,),
        ).fetchone()
    return count
