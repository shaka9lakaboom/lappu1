"""Verification of Supabase Auth access tokens.

Supabase signs access tokens either with asymmetric signing keys (published
at <SUPABASE_URL>/auth/v1/.well-known/jwks.json) or, on older projects, with a
legacy HS256 shared secret. Both are supported; the signature, expiry,
audience and issuer are always checked. There is no unverified fallback.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jwt

ACCESS_TOKEN_AUDIENCE = "authenticated"  # noqa: S105 - JWT audience claim, not a secret
_ASYMMETRIC_ALGORITHMS = {"ES256", "RS256", "EdDSA"}


class TokenVerificationError(Exception):
    """The token is missing, malformed, expired or not issued by our project."""


class VerifierUnavailableError(Exception):
    """The signing keys could not be fetched, so no token can be verified."""


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    email: str | None
    claims: dict[str, Any]


class SupabaseJWTVerifier:
    def __init__(
        self,
        issuer: str,
        jwt_secret: str | None = None,
        jwks_client: jwt.PyJWKClient | None = None,
    ) -> None:
        self.issuer = issuer
        self._jwt_secret = jwt_secret
        self._jwks_client = jwks_client or jwt.PyJWKClient(
            f"{issuer}/.well-known/jwks.json", cache_keys=True, lifespan=600
        )

    def verify(self, token: str) -> AuthenticatedUser:
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
        except jwt.PyJWTError as exc:
            raise TokenVerificationError("malformed token") from exc

        key: Any
        if algorithm == "HS256":
            if not self._jwt_secret:
                raise TokenVerificationError(
                    "HS256 token received but SUPABASE_JWT_SECRET is not configured"
                )
            key = self._jwt_secret
        elif algorithm in _ASYMMETRIC_ALGORITHMS:
            try:
                key = self._jwks_client.get_signing_key_from_jwt(token).key
            except jwt.PyJWKClientConnectionError as exc:
                raise VerifierUnavailableError("could not fetch Supabase JWKS") from exc
            except jwt.PyJWTError as exc:
                raise TokenVerificationError("no matching signing key") from exc
        else:
            raise TokenVerificationError(f"unsupported token algorithm: {algorithm!r}")

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=ACCESS_TOKEN_AUDIENCE,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenVerificationError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise TokenVerificationError(f"invalid token: {exc}") from exc

        try:
            user_id = UUID(claims["sub"])
        except (TypeError, ValueError) as exc:
            raise TokenVerificationError("token subject is not a user id") from exc

        return AuthenticatedUser(id=user_id, email=claims.get("email"), claims=claims)
