"""Versioned API (architecture section 13). All product endpoints mount here."""

from fastapi import APIRouter

from app.api.v1 import courses, events

router = APIRouter(prefix="/v1")
router.include_router(events.router)
router.include_router(courses.router)
