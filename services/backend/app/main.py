"""SkillMirror backend entry point.

Run locally from services/backend:

    uvicorn app.main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, v1
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.config import Settings, get_settings
from app.ingestion.models import MAX_BODY_BYTES
from app.observability.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(title="SkillMirror API", version=settings.api_version)
    app.dependency_overrides[get_settings] = lambda: settings
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_BODY_BYTES)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        )

    app.include_router(health.router)
    app.include_router(v1.router)
    return app


app = create_app()
