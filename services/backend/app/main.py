"""SkillMirror backend entry point.

Run locally from services/backend:

    uvicorn app.main:app --reload --port 8000

When DATABASE_URL and GEMINI_API_KEY are set (and APP_ENV is not "test"),
the durable worker loop runs in a background thread of this process
(architecture §7.3). Set WORKER_ENABLED=false to run it separately with
`python -m app.jobs.worker`.
"""

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, v1
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.config import Settings, get_settings
from app.ingestion.models import MAX_BODY_BYTES
from app.observability.logging import configure_logging

logger = logging.getLogger("skillmirror.app")


def _start_worker(settings: Settings) -> tuple[threading.Thread, threading.Event] | None:
    if not settings.worker_enabled or settings.app_env == "test":
        return None
    if settings.database_url is None:
        logger.info("worker not started: DATABASE_URL is not set")
        return None
    if settings.gemini_api_key is None:
        logger.warning("worker not started: GEMINI_API_KEY is not set (jobs stay PENDING)")
        return None
    from app.db.pool import get_db_pool
    from app.jobs.worker import build_worker, log_model_policy
    from app.model_gateway import ModelRunsSchemaError, build_gateway

    pool = get_db_pool(settings)
    try:
        gateway = build_gateway(settings, pool)
    except ModelRunsSchemaError as exc:
        # Every model_runs insert would fail: keep jobs PENDING instead of burning attempts.
        logger.error("worker not started: %s", exc)
        return None
    if gateway is None:  # pragma: no cover - checked above
        return None
    log_model_policy(gateway, settings.turn_analysis_mode)
    worker = build_worker(
        pool,
        gateway,
        turn_analysis_mode=settings.turn_analysis_mode,
        batch_size=settings.worker_batch_size,
        stale_after=timedelta(seconds=settings.worker_stale_after_seconds),
    )
    stop = threading.Event()
    thread = threading.Thread(
        target=worker.run_forever,
        args=(stop, settings.worker_poll_seconds),
        name="skillmirror-worker",
        daemon=True,
    )
    thread.start()
    return thread, stop


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        running = _start_worker(settings)
        try:
            yield
        finally:
            if running:
                thread, stop = running
                stop.set()
                thread.join(timeout=10)

    app = FastAPI(title="SkillMirror API", version=settings.api_version, lifespan=lifespan)
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
