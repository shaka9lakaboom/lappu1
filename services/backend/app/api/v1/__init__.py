"""Versioned API (architecture section 13). All product endpoints mount here."""

from fastapi import APIRouter

from app.api.v1 import (
    activity,
    admin,
    courses,
    events,
    feedback,
    ledger,
    me,
    recommendations,
    skills,
    teacher,
    verifications,
)

router = APIRouter(prefix="/v1")
router.include_router(events.router)
router.include_router(courses.router)
router.include_router(ledger.router)
router.include_router(skills.router)
router.include_router(activity.router)
router.include_router(feedback.router)
router.include_router(recommendations.router)
router.include_router(verifications.router)
router.include_router(me.router)
router.include_router(teacher.router)
router.include_router(admin.router)
