"""Versioned API (architecture section 13). All product endpoints mount here.

P0 intentionally registers no endpoints; P1 adds /v1/events/batch and
/v1/events/sync-status, protected with app.auth.dependencies.CurrentUser.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/v1")
