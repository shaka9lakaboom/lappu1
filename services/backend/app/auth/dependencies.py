"""FastAPI dependencies for authenticated endpoints.

Usage (from P1 onward):

    @router.get("/something")
    def handler(user: CurrentUser) -> ...:
        ...
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import (
    AuthenticatedUser,
    SupabaseJWTVerifier,
    TokenVerificationError,
    VerifierUnavailableError,
)
from app.core.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


@lru_cache
def _build_verifier(issuer: str, jwt_secret: str | None) -> SupabaseJWTVerifier:
    return SupabaseJWTVerifier(issuer=issuer, jwt_secret=jwt_secret)


def get_jwt_verifier(settings: Annotated[Settings, Depends(get_settings)]) -> SupabaseJWTVerifier:
    if settings.supabase_issuer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured: SUPABASE_URL is not set.",
        )
    secret = settings.supabase_jwt_secret
    return _build_verifier(settings.supabase_issuer, secret.get_secret_value() if secret else None)


# Sync on purpose: JWKS fetching is blocking I/O, so FastAPI runs this in its
# threadpool rather than on the event loop.
def require_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[SupabaseJWTVerifier, Depends(get_jwt_verifier)],
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verifier.verify(credentials.credentials)
    except TokenVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
    except VerifierUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token verification is temporarily unavailable.",
        ) from exc


CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]
