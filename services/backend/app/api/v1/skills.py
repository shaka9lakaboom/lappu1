"""Skill detail (architecture §13): GET /v1/skills/{skill_id} - skill + explanation + evidence.

Read-only apart from refreshing the learner's derived recommendation queue. Scoped to the
authenticated learner explicitly: a skill outside their courses, evidence and ledger is 404.
"""

from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, status

from app.auth.dependencies import CurrentUser
from app.db.pool import DbPool
from app.experience.models import SkillDetailResponse
from app.experience.skills import SkillNotFoundError, get_skill_detail
from app.intelligence.policy import load_policy

router = APIRouter(prefix="/skills", tags=["skills"])

_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Storage is temporarily unavailable; retry later.",
)


@router.get("/{skill_id}", response_model=SkillDetailResponse)
def get_skill(skill_id: UUID, user: CurrentUser, pool: DbPool) -> SkillDetailResponse:
    """Why SkillMirror holds this skill's state: mastery, AI Assistance Debt, every
    EvidenceEvent with its provenance, and the recommended action. No evidence -> UNKNOWN."""
    try:
        with pool.connection() as conn:
            return get_skill_detail(conn, user.id, skill_id, policy=load_policy(conn))
    except SkillNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found."
        ) from exc
    except psycopg.OperationalError as exc:
        raise _UNAVAILABLE from exc
