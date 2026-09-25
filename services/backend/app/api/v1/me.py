"""GET /v1/me (ADR 0008): the signed-in account, its profile role and what it may open.

The role is the database's (`public.profiles.role`), never a token claim. The web app uses
`capabilities` only to decide which navigation to show; every teacher / admin endpoint checks
the role again on its own.
"""

from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.auth.roles import AppRole, SignedInActor
from app.db.pool import DbPool

router = APIRouter(tags=["me"])


class MeCapabilities(BaseModel):
    # Every account learns; TEACHER / ADMIN profiles also open the teacher area.
    student: bool
    teacher: bool
    admin: bool


class MeResponse(BaseModel):
    id: UUID
    email: str | None
    role: AppRole
    display_name: str | None
    timezone: str
    taught_course_count: int
    capabilities: MeCapabilities


@router.get("/me", response_model=MeResponse)
def get_me(actor: SignedInActor, pool: DbPool) -> MeResponse:
    try:
        with pool.connection() as conn:
            display_name, timezone, taught = conn.execute(
                """
                select p.display_name, p.timezone,
                       (select count(*) from public.course_memberships m
                         where m.user_id = p.id and m.role = 'TEACHER')
                  from public.profiles p where p.id = %s
                """,
                (actor.id,),
            ).fetchone()
    except psycopg.OperationalError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is temporarily unavailable; retry later.",
        ) from exc
    return MeResponse(
        id=actor.id,
        email=actor.email,
        role=actor.role,
        display_name=display_name,
        timezone=timezone,
        taught_course_count=taught,
        capabilities=MeCapabilities(student=True, teacher=actor.can_teach, admin=actor.is_admin),
    )
