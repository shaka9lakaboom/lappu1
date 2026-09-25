"""Application roles (architecture §4; ADR 0008): read from `public.profiles` on every request.

The role is never taken from the access token. `user_metadata` / `app_metadata` claims are
whatever the token carries, and `user_metadata` is writable by the signed-in client itself
(`supabase.auth.updateUser`), so a claim such as {"role": "ADMIN"} proves nothing. Every new
account is a STUDENT (the signup trigger of migration 0001); TEACHER / ADMIN are granted by the
operator (`scripts/grant_role.py`), never through the API.

    GET  /v1/me                     any signed-in account
    /v1/teacher/...                 a TEACHER or ADMIN profile (403 otherwise)
    /v1/admin/...                   an ADMIN profile (403 otherwise)
"""

from dataclasses import dataclass
from typing import Annotated, Literal, get_args
from uuid import UUID

import psycopg
from fastapi import Depends, HTTPException, status
from psycopg import Connection

from app.auth.dependencies import CurrentUser
from app.db.pool import DbPool

AppRole = Literal["STUDENT", "TEACHER", "ADMIN"]
APP_ROLES: tuple[str, ...] = get_args(AppRole)
TEACHING_ROLES: tuple[str, ...] = ("TEACHER", "ADMIN")


@dataclass(frozen=True)
class Actor:
    """The signed-in account with the role its profile has right now."""

    id: UUID
    email: str | None
    role: AppRole

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"

    @property
    def can_teach(self) -> bool:
        return self.role in TEACHING_ROLES


def load_role(conn: Connection, user_id: UUID) -> AppRole | None:
    row = conn.execute(
        "select role::text from public.profiles where id = %s", (user_id,)
    ).fetchone()
    return row[0] if row else None


def require_actor(user: CurrentUser, pool: DbPool) -> Actor:
    try:
        with pool.connection() as conn:
            role = load_role(conn, user.id)
    except psycopg.OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is temporarily unavailable; retry later.",
        ) from exc
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No SkillMirror profile exists for this account.",
        )
    return Actor(id=user.id, email=user.email, role=role)


def require_teaching_role(actor: Annotated[Actor, Depends(require_actor)]) -> Actor:
    if not actor.can_teach:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This area is for teachers."
        )
    return actor


def require_admin(actor: Annotated[Actor, Depends(require_actor)]) -> Actor:
    if not actor.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This area is for administrators."
        )
    return actor


SignedInActor = Annotated[Actor, Depends(require_actor)]
TeachingActor = Annotated[Actor, Depends(require_teaching_role)]
AdminActor = Annotated[Actor, Depends(require_admin)]
