from typing import Optional, Dict, Any
from fastapi import HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from app.config import settings
from app.observability import logger

security = HTTPBearer(auto_error=False)


async def get_current_user_claims(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> Optional[Dict[str, Any]]:
    """
    Validates Supabase JWT from Authorization header.
    Returns decoded claims if valid token provided.
    """
    if not credentials:
        return None

    token = credentials.credentials
    secret = settings.supabase_jwt_secret or settings.supabase_anon_key

    if not secret:
        logger.warning("Supabase JWT secret not set; skipping signature check in dev mode")
        try:
            return jwt.decode(token, options={"verify_signature": False})
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Invalid token format: {str(e)}")

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
