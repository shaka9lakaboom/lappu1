import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.auth import dependencies
from app.auth.dependencies import CurrentUser
from app.auth.jwt import SupabaseJWTVerifier, TokenVerificationError, VerifierUnavailableError
from app.main import create_app
from tests.conftest import make_settings

ISSUER = "https://project-ref.supabase.co/auth/v1"
HS_SECRET = "test-only-hs256-secret-with-at-least-32-bytes"


def claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    base: dict[str, object] = {
        "sub": str(uuid4()),
        "email": "learner@test.invalid",
        "aud": "authenticated",
        "iss": ISSUER,
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    base.update(overrides)
    return base


class FakeJWKSClient:
    """Stands in for PyJWKClient so tests never touch the network."""

    def __init__(self, public_key: object | None = None, error: Exception | None = None):
        self._public_key = public_key
        self._error = error

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        if self._error:
            raise self._error
        return SimpleNamespace(key=self._public_key)


@pytest.fixture(scope="module")
def ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def es256_verifier(ec_key: ec.EllipticCurvePrivateKey) -> SupabaseJWTVerifier:
    return SupabaseJWTVerifier(ISSUER, jwks_client=FakeJWKSClient(ec_key.public_key()))


# --- Verifier ---------------------------------------------------------------


def test_valid_es256_token_is_accepted(ec_key) -> None:
    payload = claims()
    token = jwt.encode(payload, ec_key, algorithm="ES256", headers={"kid": "k1"})
    user = es256_verifier(ec_key).verify(token)
    assert str(user.id) == payload["sub"]
    assert user.email == "learner@test.invalid"


def test_valid_legacy_hs256_token_is_accepted() -> None:
    payload = claims()
    token = jwt.encode(payload, HS_SECRET, algorithm="HS256")
    user = SupabaseJWTVerifier(ISSUER, jwt_secret=HS_SECRET, jwks_client=FakeJWKSClient()).verify(
        token
    )
    assert str(user.id) == payload["sub"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"exp": int(time.time()) - 10}, "expired"),
        ({"aud": "anon"}, "invalid token"),
        ({"iss": "https://other-project.supabase.co/auth/v1"}, "invalid token"),
        ({"sub": "not-a-uuid"}, "not a user id"),
    ],
)
def test_invalid_claims_are_rejected(ec_key, overrides, message) -> None:
    token = jwt.encode(claims(**overrides), ec_key, algorithm="ES256")
    with pytest.raises(TokenVerificationError, match=message):
        es256_verifier(ec_key).verify(token)


def test_missing_required_claim_is_rejected(ec_key) -> None:
    payload = claims()
    del payload["exp"]
    token = jwt.encode(payload, ec_key, algorithm="ES256")
    with pytest.raises(TokenVerificationError):
        es256_verifier(ec_key).verify(token)


def test_token_signed_by_another_key_is_rejected(ec_key) -> None:
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(claims(), attacker_key, algorithm="ES256")
    with pytest.raises(TokenVerificationError):
        es256_verifier(ec_key).verify(token)


def test_hs256_token_without_configured_secret_is_rejected() -> None:
    token = jwt.encode(claims(), HS_SECRET, algorithm="HS256")
    with pytest.raises(TokenVerificationError, match="SUPABASE_JWT_SECRET"):
        SupabaseJWTVerifier(ISSUER, jwks_client=FakeJWKSClient()).verify(token)


def test_hs256_token_with_wrong_secret_is_rejected() -> None:
    token = jwt.encode(claims(), "a-different-secret-that-is-also-32-bytes", algorithm="HS256")
    with pytest.raises(TokenVerificationError):
        SupabaseJWTVerifier(ISSUER, jwt_secret=HS_SECRET, jwks_client=FakeJWKSClient()).verify(
            token
        )


def test_unsigned_token_is_rejected() -> None:
    token = jwt.encode(claims(), key=None, algorithm="none")
    with pytest.raises(TokenVerificationError, match="unsupported"):
        SupabaseJWTVerifier(ISSUER, jwt_secret=HS_SECRET, jwks_client=FakeJWKSClient()).verify(
            token
        )


def test_malformed_token_is_rejected() -> None:
    with pytest.raises(TokenVerificationError, match="malformed"):
        SupabaseJWTVerifier(ISSUER, jwks_client=FakeJWKSClient()).verify("not.a.jwt")


def test_jwks_outage_is_reported_as_unavailable(ec_key) -> None:
    token = jwt.encode(claims(), ec_key, algorithm="ES256")
    verifier = SupabaseJWTVerifier(
        ISSUER, jwks_client=FakeJWKSClient(error=jwt.PyJWKClientConnectionError("down"))
    )
    with pytest.raises(VerifierUnavailableError):
        verifier.verify(token)


# --- FastAPI dependency -----------------------------------------------------


def client_with_protected_route(verifier: SupabaseJWTVerifier | None, **settings) -> TestClient:
    """Mounts a test-only protected route; P0 exposes no protected endpoints."""
    app = create_app(make_settings(**settings))
    router = APIRouter()

    @router.get("/_test/whoami")
    def whoami(user: CurrentUser) -> dict[str, str]:
        return {"id": str(user.id)}

    app.include_router(router)
    if verifier is not None:
        app.dependency_overrides[dependencies.get_jwt_verifier] = lambda: verifier
    return TestClient(app)


def test_protected_route_accepts_valid_token(ec_key) -> None:
    payload = claims()
    token = jwt.encode(payload, ec_key, algorithm="ES256")
    client = client_with_protected_route(es256_verifier(ec_key))
    response = client.get("/_test/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"id": payload["sub"]}


def test_protected_route_rejects_missing_token(ec_key) -> None:
    response = client_with_protected_route(es256_verifier(ec_key)).get("/_test/whoami")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_protected_route_rejects_invalid_token(ec_key) -> None:
    client = client_with_protected_route(es256_verifier(ec_key))
    response = client.get("/_test/whoami", headers={"Authorization": "Bearer garbage"})
    assert response.status_code == 401


def test_protected_route_fails_closed_when_auth_not_configured() -> None:
    client = client_with_protected_route(verifier=None)
    response = client.get("/_test/whoami", headers={"Authorization": "Bearer anything"})
    assert response.status_code == 503
    assert "SUPABASE_URL" in response.json()["detail"]


def test_verifier_is_built_from_settings() -> None:
    settings = make_settings(supabase_url="https://project-ref.supabase.co")
    verifier = dependencies.get_jwt_verifier(settings)
    assert verifier.issuer == ISSUER
